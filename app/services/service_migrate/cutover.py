"""DNS fabric + Pi-hole restartdns + NPM forward_host after dest up (v1.4 M4 / M-npm)."""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from sqlmodel import Session, select

from ...models import Integration, Server
from ..dns_fabric.core import is_host_identity_name, upsert_service_record
from ..integrations import pihole as ph
from ..integrations import registry as reg
from ..integrations.npm import get_token, retarget_proxy_host_backend
from .host_lock import compose_project_name
from .preflight import _dns_rows, _match_npm, _npm_hosts_cached

logger = logging.getLogger(__name__)

LogFn = Callable[[str], None]


class CutoverError(Exception):
    pass


def dest_forward_host(dest: Server) -> str:
    """Address NPM / LAN clients use for the dest backend."""
    ip = (getattr(dest, "ip_address", None) or "").strip()
    if ip:
        return ip
    host = (getattr(dest, "hostname", None) or "").strip()
    if host:
        return host
    return (getattr(dest, "dns_name", None) or "").strip()


def _log(log: Optional[LogFn], msg: str) -> None:
    if log:
        log(msg)
    else:
        logger.info("[migrate-cutover] %s", msg)


def fanout_pihole_restartdns(session: Session) -> list[dict[str, Any]]:
    rows = [
        r
        for r in reg.list_integrations(session, type_filter=reg.TYPE_PIHOLE)
        if r.enabled
    ]
    results: list[dict[str, Any]] = []
    for r in rows:
        item: dict[str, Any] = {"id": r.id, "name": r.name, "ok": False, "error": ""}
        try:
            sess = ph.login(
                r.base_url,
                reg.pihole_password(r),
                tls_verify=reg.tls_verify(r),
            )
            try:
                ph.run_action(sess, "restartdns")
                item["ok"] = True
            finally:
                ph.logout(sess)
        except Exception as e:
            item["error"] = str(e)[:200]
            logger.warning("pihole restartdns on %s: %s", r.name, e)
        results.append(item)
    return results


def _put_npm_backend(
    session: Session,
    *,
    fqdn: str,
    cached_id: Optional[str],
    new_host: str,
) -> dict[str, Any]:
    rows = session.exec(
        select(Integration).where(
            Integration.type == reg.TYPE_NPM,
            Integration.enabled == True,  # noqa: E712
        )
    ).all()
    if not rows:
        raise CutoverError(f"{fqdn}: no enabled NPM integration")
    last_err = "no matching proxy host"
    for integ in rows:
        try:
            identity, password = reg.npm_credentials(integ)
            token = get_token(
                integ.base_url,
                identity,
                password,
                tls_verify=reg.tls_verify(integ),
            )
            host_id = (cached_id or "").strip()
            if not host_id:
                hosts, _n = _npm_hosts_cached(session)
                match = _match_npm(hosts, fqdn)
                host_id = str((match or {}).get("id") or "")
            if not host_id:
                last_err = f"{fqdn}: unmatched proxy host on {integ.name}"
                continue
            return retarget_proxy_host_backend(
                integ.base_url,
                token,
                host_id,
                new_host,
                tls_verify=reg.tls_verify(integ),
            )
        except Exception as e:
            last_err = f"{integ.name}: {e}"[:300]
            logger.warning("npm PUT %s on %s: %s", fqdn, integ.name, e)
    raise CutoverError(f"NPM backend retarget failed for {fqdn}: {last_err}")


def retarget_dns_npm(
    session: Session,
    *,
    source: Server,
    dest: Server,
    project: str,
    log: Optional[LogFn] = None,
    upsert_fn=None,
    npm_put_fn=None,
    restartdns_fn=None,
) -> dict[str, Any]:
    """Direct CNAME → dest dns_name; NPM-fronted PUT forward_host; restartdns if needed."""
    name = compose_project_name(project)
    rows = _dns_rows(session, int(source.id or 0), name)
    upsert = upsert_fn or upsert_service_record
    npm_put = npm_put_fn or (
        lambda fqdn, cached_id, new_host: _put_npm_backend(
            session, fqdn=fqdn, cached_id=cached_id, new_host=new_host
        )
    )
    restart = restartdns_fn or (lambda: fanout_pihole_restartdns(session))
    out: list[dict[str, Any]] = []
    need_ftl = False
    new_backend = dest_forward_host(dest)
    npm_hosts, _npm_n = _npm_hosts_cached(session)

    for rec in rows:
        fqdn = rec.fqdn
        if is_host_identity_name(fqdn, source) or is_host_identity_name(fqdn, dest):
            _log(log, f"Skip host-identity name {fqdn}")
            out.append({"fqdn": fqdn, "action": "skip_host_identity"})
            continue
        if rec.via_proxy:
            if not new_backend:
                raise CutoverError(
                    f"{fqdn}: dest has no IP/hostname for NPM forward_host"
                )
            match = _match_npm(npm_hosts, fqdn)
            cached_id = str((match or {}).get("id") or rec.npm_hint or "")
            _log(log, f"NPM PUT {fqdn} forward_host → {new_backend}")
            npm_res = npm_put(fqdn, cached_id, new_backend)
            upsert(
                session,
                fqdn=fqdn,
                target_server_id=int(rec.target_server_id),
                backend_server_id=int(dest.id),
                stack_deployment_id=rec.stack_deployment_id,
                docker_project=name,
                label=rec.label,
                managed_on_pihole=rec.managed_on_pihole,
                via_proxy=True,
                npm_hint=rec.npm_hint,
                certificate_id=rec.certificate_id,
                external_dns_status=rec.external_dns_status or "checklist",
                notes=rec.notes,
                record_id=rec.id,
                sync_now=bool(rec.managed_on_pihole),
            )
            out.append(
                {
                    "fqdn": fqdn,
                    "action": "npm",
                    "forward_host": new_backend,
                    "npm": npm_res if isinstance(npm_res, dict) else {"ok": True},
                }
            )
        else:
            dest_dns = (getattr(dest, "dns_name", None) or "").strip()
            if not dest_dns:
                raise CutoverError(
                    f"{fqdn}: destination has no DNS name for CNAME retarget"
                )
            _log(log, f"CNAME {fqdn} → {dest_dns}")
            upsert(
                session,
                fqdn=fqdn,
                target_server_id=int(dest.id),
                backend_server_id=int(dest.id),
                stack_deployment_id=rec.stack_deployment_id,
                docker_project=name,
                label=rec.label,
                managed_on_pihole=rec.managed_on_pihole,
                via_proxy=False,
                npm_hint=rec.npm_hint,
                certificate_id=rec.certificate_id,
                external_dns_status=rec.external_dns_status or "checklist",
                notes=rec.notes,
                record_id=rec.id,
                sync_now=bool(rec.managed_on_pihole),
            )
            need_ftl = need_ftl or bool(rec.managed_on_pihole)
            out.append({"fqdn": fqdn, "action": "cname", "target": dest_dns})

    ftl: list[dict[str, Any]] = []
    if need_ftl:
        _log(log, "Restarting Pi-hole DNS (all instances)…")
        ftl = restart() or []
        bad = [r for r in ftl if not r.get("ok")]
        if bad:
            detail = "; ".join(
                f"{r.get('name')}:{r.get('error') or 'fail'}" for r in bad
            )
            raise CutoverError(f"Pi-hole restartdns failed: {detail}")

    return {"ok": True, "records": out, "restartdns": ftl}
