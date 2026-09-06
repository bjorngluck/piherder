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
from ..integrations.registry import (
    GRAFANA_KIND_CONTAINERS,
    ROLE_DASHBOARD,
    ROLE_PROXY_HOST,
    ROLE_SERVICE,
    binding_grafana_kind,
)
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
    dest_project: Optional[str] = None,
    log: Optional[LogFn] = None,
) -> dict[str, Any]:
    """Rewrite maps / Kuma / templates / certs so they follow dest. DNS is M4."""
    name = compose_project_name(project)
    dest_name = compose_project_name(dest_project or project)
    sid = int(source.id or 0)
    did = int(dest.id or 0)
    counts = {
        "stack_deployments": 0,
        "kuma_bindings": 0,
        "dashboard_bindings": 0,
        "proxy_host_bindings": 0,
        "visual_stacks": 0,
        "annotations": 0,
        "ports": 0,
        "edges": 0,
        "cert_targets": 0,
        "bindings_dup_dropped": 0,
    }

    for row in session.exec(
        select(StackDeployment).where(
            StackDeployment.server_id == sid,
            StackDeployment.project_name == name,
        )
    ).all():
        row.server_id = did
        if dest_name != name:
            row.project_name = dest_name
        row.updated_at = _now()
        session.add(row)
        counts["stack_deployments"] += 1

    dest_scopes: set[tuple] = set()
    for existing in session.exec(
        select(IntegrationBinding).where(IntegrationBinding.server_id == did)
    ).all():
        dest_scopes.add(
            (
                int(existing.integration_id or 0),
                (existing.role or "").strip(),
                (existing.external_id or "").strip(),
                (existing.docker_project or "").strip(),
                (existing.docker_container or "").strip(),
            )
        )

    def _move_bind(row, *, count_key: str) -> None:
        new_proj = dest_name if dest_name != name else (row.docker_project or name)
        key = (
            int(row.integration_id or 0),
            (row.role or "").strip(),
            (row.external_id or "").strip(),
            new_proj,
            (row.docker_container or "").strip(),
        )
        if int(row.server_id or 0) == did:
            dest_scopes.add(key)
            return
        if key in dest_scopes:
            # Dest already has this Grafana/Kuma/NPM scope (clone from an earlier
            # stay). Drop the extra source row so unique uq_integ_bind_scope holds.
            session.delete(row)
            counts["bindings_dup_dropped"] += 1
            return
        row.server_id = did
        if dest_name != name:
            row.docker_project = dest_name
        row.updated_at = _now()
        session.add(row)
        dest_scopes.add(key)
        counts[count_key] += 1

    for row in session.exec(
        select(IntegrationBinding).where(
            IntegrationBinding.docker_project == name,
        )
    ).all():
        role = (row.role or "").strip()
        if role == ROLE_PROXY_HOST:
            _move_bind(row, count_key="proxy_host_bindings")
            continue
        if role == ROLE_DASHBOARD:
            if binding_grafana_kind(row) != GRAFANA_KIND_CONTAINERS:
                continue
            _move_bind(row, count_key="dashboard_bindings")
            continue
        if role != ROLE_SERVICE:
            continue
        _move_bind(row, count_key="kuma_bindings")

    for row in session.exec(
        select(VisualServiceStack).where(
            VisualServiceStack.server_id == sid,
            VisualServiceStack.compose_project == name,
        )
    ).all():
        row.server_id = did
        if dest_name != name:
            row.compose_project = dest_name
        session.add(row)
        counts["visual_stacks"] += 1

    for row in session.exec(
        select(ContainerAnnotation).where(
            ContainerAnnotation.server_id == sid,
            ContainerAnnotation.compose_project == name,
        )
    ).all():
        row.server_id = did
        if dest_name != name:
            row.compose_project = dest_name
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
        if dest_name != name:
            row.owner_project = dest_name
        row.updated_at = _now()
        session.add(row)
        counts["ports"] += 1

    for row in session.exec(select(RuntimeEdge)).all():
        changed = False
        if int(row.from_server_id) == sid and (row.from_project or "") == name:
            row.from_server_id = did
            if dest_name != name:
                row.from_project = dest_name
            changed = True
        if int(row.to_server_id) == sid and (row.to_project or "") == name:
            row.to_server_id = did
            if dest_name != name:
                row.to_project = dest_name
            changed = True
        if changed:
            row.updated_at = _now()
            session.add(row)
            counts["edges"] += 1

    cert_ids: set[int] = set()
    recs = list(_dns_rows(session, sid, name))
    recs.extend(_dns_rows(session, did, dest_name))
    if dest_name != name:
        recs.extend(_dns_rows(session, did, name))
    for rec in recs:
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
