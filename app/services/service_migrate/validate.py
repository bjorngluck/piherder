"""Post-cutover TLS + Kuma checks (v1.4 M6). Fail does not auto-rollback."""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from sqlmodel import Session, select

from ...models import IntegrationBinding, ManagedCertificate, Server
from ..certificates import verify_tls_endpoint_fingerprint
from ..integrations.registry import ROLE_SERVICE
from .host_lock import compose_project_name
from .preflight import _dns_rows

logger = logging.getLogger(__name__)

LogFn = Callable[[str], None]


class ValidateError(Exception):
    pass


def _log(log: Optional[LogFn], msg: str) -> None:
    if log:
        log(msg)
    else:
        logger.info("[migrate-validate] %s", msg)


def validate_migrate(
    session: Session,
    *,
    source: Server,
    dest: Server,
    project: str,
    log: Optional[LogFn] = None,
    tls_fn=None,
    kuma_poll_fn=None,
) -> dict[str, Any]:
    """TLS fingerprint (SNI = FQDN) and Kuma last_state when those rows exist."""
    name = compose_project_name(project)
    tls_probe = tls_fn or verify_tls_endpoint_fingerprint
    out: dict[str, Any] = {"ok": True, "tls": [], "kuma": []}
    seen_fqdn: set[str] = set()
    recs = _dns_rows(session, int(dest.id or 0), name) + _dns_rows(
        session, int(source.id or 0), name
    )
    for rec in recs:
        fqdn = rec.fqdn
        if fqdn in seen_fqdn:
            continue
        seen_fqdn.add(fqdn)
        cid = rec.certificate_id
        if not cid:
            continue
        cert = session.get(ManagedCertificate, int(cid))
        fp = (getattr(cert, "fingerprint_sha256", None) or "").strip() if cert else ""
        url = f"https://{fqdn}?sni={fqdn}"
        _log(log, f"TLS probe {fqdn} (SNI={fqdn})")
        res = tls_probe(verify_url=url, expected_fingerprint=fp)
        row = {
            "fqdn": fqdn,
            "ok": bool(res.get("ok")),
            "status": res.get("status") or ("ok" if res.get("ok") else "failed"),
            "message": (res.get("message") or "")[:300],
        }
        out["tls"].append(row)
        if row["status"] == "skipped":
            _log(log, f"TLS skipped {fqdn}: {row['message']}")
            continue
        if not row["ok"]:
            raise ValidateError(
                f"TLS probe failed for {fqdn}: {row['message'] or 'mismatch'}"
            )

    binds = list(
        session.exec(
            select(IntegrationBinding).where(
                IntegrationBinding.server_id == int(dest.id or 0),
                IntegrationBinding.role == ROLE_SERVICE,
                IntegrationBinding.docker_project == name,
            )
        ).all()
    )
    poll = kuma_poll_fn
    if poll is None and binds:
        from ..integrations.poll import poll_integration

        def poll(iid: int, _session=session) -> Any:
            return poll_integration(iid, notify=False, session=_session)

    seen_integ: set[int] = set()
    for b in binds:
        iid = int(b.integration_id or 0)
        if iid and iid not in seen_integ and poll:
            seen_integ.add(iid)
            try:
                poll(iid)
            except Exception as e:
                logger.warning("kuma poll %s: %s", iid, e)
        session.refresh(b)
        state = (b.last_state or "").strip().lower()
        item = {
            "binding_id": b.id,
            "label": b.external_label or b.external_id,
            "state": state or "unknown",
        }
        out["kuma"].append(item)
        if state == "down":
            raise ValidateError(
                f"Kuma monitor {item['label']} is down after migrate"
            )
        if state and state not in ("up", "maintenance"):
            _log(log, f"Kuma {item['label']} state={state} (not failing the job)")

    return out
