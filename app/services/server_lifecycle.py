"""Remove a server from the PiHerder fleet (DB + schedules only).

Host configuration is intentionally left untouched: Docker stacks, data,
SSH users, sudoers, and authorized_keys on the remote machine stay as they are.
Use the separate host cleanup script if you want to remove the piherder user later.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from sqlmodel import Session, select

from ..models import (
    AuditLog,
    CertificateTarget,
    ContainerAnnotation,
    ContainerAnnotationTag,
    DockerVersion,
    IntegrationBinding,
    Job,
    NmapDevice,
    Notification,
    PortAnnotation,
    RuntimeEdge,
    Server,
    ServiceDnsRecord,
    StackDeployment,
    UserFavourite,
    VisualServiceStack,
)
from . import jobs as job_service

logger = logging.getLogger(__name__)


class ServerDeleteError(Exception):
    def __init__(self, message: str, code: str = "error"):
        self.message = message
        self.code = code
        super().__init__(message)


def _unregister_schedules(server_id: int) -> None:
    try:
        from ..main import HAS_SCHEDULER, scheduler
        from .scheduler import unregister_server_cron_jobs

        unregister_server_cron_jobs(scheduler, HAS_SCHEDULER, server_id)
    except Exception as e:
        logger.warning(f"[lifecycle] schedule unregister for server {server_id}: {e}")


def _cancel_active_jobs(session: Session, server_id: int, user_id: int | None) -> int:
    """Best-effort cancel of pending/running jobs before delete."""
    active = list(
        session.exec(
            select(Job).where(
                Job.server_id == server_id,
                Job.status.in_(["pending", "running"]),
            )
        ).all()
    )
    n = 0
    for job in active:
        try:
            # cancel_job commits; re-bind may be needed
            job_service.cancel_job(
                session,
                job,
                user_id=user_id,
                message="Cancelled — server removed from PiHerder",
            )
            n += 1
        except job_service.JobNotCancellable:
            continue
        except Exception as e:
            logger.warning(f"[lifecycle] cancel job #{getattr(job, 'id', '?')}: {e}")
            try:
                j = session.get(Job, job.id)
                if j and j.status in ("pending", "running"):
                    job_service._mark_job_cancelled(
                        j,
                        "Cancelled — server removed from PiHerder",
                        session,
                        record_audit=False,
                    )
                    session.commit()
                    n += 1
            except Exception:
                pass
    return n


def _purge_server_children(session: Session, server_id: int, snapshot: dict[str, Any]) -> None:
    """Delete or unlink herder-only rows that FK this server (no remote wipe)."""

    def _count_delete(rows) -> int:
        n = 0
        for row in rows:
            session.delete(row)
            n += 1
        return n

    ann_ids = [
        int(a.id)
        for a in session.exec(
            select(ContainerAnnotation).where(ContainerAnnotation.server_id == server_id)
        ).all()
        if getattr(a, "id", None)
    ]
    tags = 0
    if ann_ids:
        tags = _count_delete(
            session.exec(
                select(ContainerAnnotationTag).where(
                    ContainerAnnotationTag.annotation_id.in_(ann_ids)
                )
            ).all()
        )
    snapshot["container_annotation_tags_removed"] = tags
    snapshot["container_annotations_removed"] = _count_delete(
        session.exec(
            select(ContainerAnnotation).where(ContainerAnnotation.server_id == server_id)
        ).all()
    )
    snapshot["visual_stacks_removed"] = _count_delete(
        session.exec(
            select(VisualServiceStack).where(VisualServiceStack.server_id == server_id)
        ).all()
    )
    snapshot["certificate_targets_removed"] = _count_delete(
        session.exec(
            select(CertificateTarget).where(CertificateTarget.server_id == server_id)
        ).all()
    )
    snapshot["runtime_edges_removed"] = _count_delete(
        session.exec(
            select(RuntimeEdge).where(
                (RuntimeEdge.from_server_id == server_id)
                | (RuntimeEdge.to_server_id == server_id)
            )
        ).all()
    )
    snapshot["favourites_removed"] = _count_delete(
        session.exec(select(UserFavourite).where(UserFavourite.server_id == server_id)).all()
    )
    snapshot["dns_records_purged"] = _count_delete(
        session.exec(
            select(ServiceDnsRecord).where(
                (ServiceDnsRecord.target_server_id == server_id)
                | (ServiceDnsRecord.backend_server_id == server_id)
            )
        ).all()
    )
    snapshot["stack_deployments_removed"] = _count_delete(
        session.exec(select(StackDeployment).where(StackDeployment.server_id == server_id)).all()
    )
    snapshot["integration_bindings_removed"] = _count_delete(
        session.exec(
            select(IntegrationBinding).where(IntegrationBinding.server_id == server_id)
        ).all()
    )
    snapshot["port_annotations_removed"] = _count_delete(
        session.exec(select(PortAnnotation).where(PortAnnotation.server_id == server_id)).all()
    )
    try:
        from . import ssh_identities as ident_svc

        snapshot["ssh_identities_removed"] = ident_svc.purge_for_server(session, server_id)
    except Exception as e:
        logger.warning(f"[lifecycle] ssh identities purge for server {server_id}: {e}")
        snapshot["ssh_identities_removed"] = 0
    unlinked = 0
    for dev in session.exec(
        select(NmapDevice).where(NmapDevice.linked_server_id == server_id)
    ).all():
        dev.linked_server_id = None
        session.add(dev)
        unlinked += 1
    snapshot["nmap_devices_unlinked"] = unlinked


def delete_server_from_fleet(
    session: Session,
    server: Server,
    *,
    confirm_name: str,
    user_id: int | None = None,
) -> dict[str, Any]:
    """Delete a Server row from PiHerder.

    Does **not** SSH to the host or modify remote config / backup files on disk.

    - Cancels active jobs
    - Unregisters APScheduler cron jobs
    - Nulls optional FKs (jobs, audit, notifications) so history remains
    - Deletes DockerVersion drafts (PiHerder-only compose history)
    - Deletes the Server row + stored SSH credentials in DB
    """
    if not server or not server.id:
        raise ServerDeleteError("Server not found", "not_found")

    expected = (server.name or "").strip()
    given = (confirm_name or "").strip()
    if not expected or given != expected:
        raise ServerDeleteError(
            "Type the exact server name to confirm deletion",
            "confirm_name",
        )

    server_id = int(server.id)
    snapshot = {
        "former_server_id": server_id,
        "name": server.name,
        "hostname": server.hostname,
        "ssh_username": server.ssh_username,
        "ssh_port": server.ssh_port,
        "host_left_intact": True,
        "backups_on_disk": "not removed",
        "note": "Remote Docker, data, users, and keys were not changed",
    }

    cancelled = _cancel_active_jobs(session, server_id, user_id)
    snapshot["jobs_cancelled"] = cancelled

    _unregister_schedules(server_id)

    # Compose drafts / version history live only in PiHerder
    for dv in session.exec(
        select(DockerVersion).where(DockerVersion.server_id == server_id)
    ).all():
        session.delete(dv)

    try:
        _purge_server_children(session, server_id, snapshot)
    except Exception as e:
        logger.warning(f"[lifecycle] child purge for server {server_id}: {e}")
        raise

    # DNS fabric rows that reference this host
    try:
        from .dns_fabric import cleanup_dns_for_server

        n_dns = cleanup_dns_for_server(session, server_id)
        snapshot["dns_records_removed"] = n_dns
    except Exception as e:
        logger.warning(f"[lifecycle] dns cleanup for server {server_id}: {e}")

    # Keep job / audit / notification rows; unlink from server
    for job in session.exec(select(Job).where(Job.server_id == server_id)).all():
        job.server_id = None
        session.add(job)

    for al in session.exec(select(AuditLog).where(AuditLog.server_id == server_id)).all():
        al.server_id = None
        session.add(al)

    for note in session.exec(
        select(Notification).where(Notification.server_id == server_id)
    ).all():
        note.server_id = None
        session.add(note)

    # Fleet-level audit (no server_id — row is about to go away)
    from .audit_write import make_audit_log

    session.add(
        make_audit_log(
            user_id=user_id,
            server_id=None,
            action="server_deleted",
            status="success",
            details=json.dumps(snapshot),
            started_at=datetime.utcnow(),
            finished_at=datetime.utcnow(),
        )
    )

    session.delete(server)
    session.commit()
    logger.info(
        f"[lifecycle] Deleted server #{server_id} {snapshot['name']!r} "
        f"({snapshot['hostname']}) — host left intact"
    )
    return snapshot
