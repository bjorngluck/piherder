"""Move control-plane rows to dest after a successful copy (v1.4 M7)."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable, Optional

from sqlmodel import Session, select

from ...models import (
    CertificateTarget,
    ContainerAnnotation,
    IntegrationBinding,
    PortAnnotation,
    RuntimeEdge,
    Server,
    StackDeployment,
    VisualServiceStack,
)
from ..integrations.registry import ROLE_SERVICE
from .host_lock import compose_project_name
from .preflight import _dns_rows

logger = logging.getLogger(__name__)

LogFn = Callable[[str], None]


def _log(log: Optional[LogFn], msg: str) -> None:
    if log:
        log(msg)
    else:
        logger.info("[migrate-rebind] %s", msg)


def _now() -> datetime:
    return datetime.utcnow()


def rebind_control_plane(
    session: Session,
    *,
    source: Server,
    dest: Server,
    project: str,
    log: Optional[LogFn] = None,
) -> dict[str, Any]:
    """Rewrite maps / Kuma / templates / certs so they follow dest. DNS is M4."""
    name = compose_project_name(project)
    sid = int(source.id or 0)
    did = int(dest.id or 0)
    counts = {
        "stack_deployments": 0,
        "kuma_bindings": 0,
        "visual_stacks": 0,
        "annotations": 0,
        "ports": 0,
        "edges": 0,
        "cert_targets": 0,
    }

    for row in session.exec(
        select(StackDeployment).where(
            StackDeployment.server_id == sid,
            StackDeployment.project_name == name,
        )
    ).all():
        row.server_id = did
        row.updated_at = _now()
        session.add(row)
        counts["stack_deployments"] += 1

    for row in session.exec(
        select(IntegrationBinding).where(
            IntegrationBinding.server_id == sid,
            IntegrationBinding.role == ROLE_SERVICE,
            IntegrationBinding.docker_project == name,
        )
    ).all():
        row.server_id = did
        row.updated_at = _now()
        session.add(row)
        counts["kuma_bindings"] += 1

    for row in session.exec(
        select(VisualServiceStack).where(
            VisualServiceStack.server_id == sid,
            VisualServiceStack.compose_project == name,
        )
    ).all():
        row.server_id = did
        session.add(row)
        counts["visual_stacks"] += 1

    for row in session.exec(
        select(ContainerAnnotation).where(
            ContainerAnnotation.server_id == sid,
            ContainerAnnotation.compose_project == name,
        )
    ).all():
        row.server_id = did
        row.updated_at = _now()
        session.add(row)
        counts["annotations"] += 1

    for row in session.exec(
        select(PortAnnotation).where(
            PortAnnotation.server_id == sid,
            PortAnnotation.owner_project == name,
        )
    ).all():
        row.server_id = did
        row.updated_at = _now()
        session.add(row)
        counts["ports"] += 1

    for row in session.exec(select(RuntimeEdge)).all():
        changed = False
        if int(row.from_server_id) == sid and (row.from_project or "") == name:
            row.from_server_id = did
            changed = True
        if int(row.to_server_id) == sid and (row.to_project or "") == name:
            row.to_server_id = did
            changed = True
        if changed:
            row.updated_at = _now()
            session.add(row)
            counts["edges"] += 1

    cert_ids: set[int] = set()
    for rec in _dns_rows(session, sid, name):
        if rec.certificate_id:
            cert_ids.add(int(rec.certificate_id))
    for cid in cert_ids:
        dest_existing = session.exec(
            select(CertificateTarget).where(
                CertificateTarget.server_id == did,
                CertificateTarget.certificate_id == cid,
            )
        ).first()
        if dest_existing:
            continue
        src_t = session.exec(
            select(CertificateTarget).where(
                CertificateTarget.server_id == sid,
                CertificateTarget.certificate_id == cid,
            )
        ).first()
        if not src_t:
            continue
        clone = CertificateTarget(
            certificate_id=cid,
            server_id=did,
            label=src_t.label,
            remote_dir=src_t.remote_dir,
            layout=src_t.layout,
            write_mode=src_t.write_mode,
            fullchain_filename=src_t.fullchain_filename,
            privkey_filename=src_t.privkey_filename,
            combined_filename=src_t.combined_filename,
            pfx_filename=src_t.pfx_filename,
            file_mode=src_t.file_mode,
            file_owner=src_t.file_owner,
            file_group=src_t.file_group,
            pfx_export_password_encrypted=src_t.pfx_export_password_encrypted,
            post_deploy_command=src_t.post_deploy_command,
            verify_url=src_t.verify_url,
            enabled=src_t.enabled,
        )
        session.add(clone)
        counts["cert_targets"] += 1

    session.commit()
    _log(
        log,
        "Rebind: "
        + ", ".join(f"{k}={v}" for k, v in counts.items() if v),
    )
    return {"ok": True, "counts": counts}
