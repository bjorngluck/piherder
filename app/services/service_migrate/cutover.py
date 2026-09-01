"""DNS fabric + Pi-hole restartdns + NPM forward_host after dest up (v1.4 M4 / M-npm)."""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from sqlmodel import Session, select

from ...models import Integration, Server, ServiceDnsRecord
from ..dns_fabric.core import (
    DnsFabricError,
    fanout_pihole_dns,
    is_host_identity_name,
    normalize_fqdn,
    pihole_login_urls,
    upsert_service_record,
)
from ..integrations import pihole as ph
from ..integrations import registry as reg
from ..integrations.npm import get_token, retarget_proxy_host_backend
from .host_lock import compose_project_name
from .overrides import port_map_key
from .preflight import (
    _dns_rows,
    _match_npm,
    _npm_hosts_cached,
    is_npm_edge_project,
    npm_edge_dependents,
    npm_edge_server,
    npm_proxy_hosts_for_project,
)

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
            last_err: Exception | None = None
            sess = None
            for url in pihole_login_urls(session, r):
                try:
                    sess = ph.login(
                        url,
                        reg.pihole_password(r),
                        tls_verify=reg.tls_verify(r),
                    )
                    last_err = None
                    break
                except Exception as e:
                    last_err = e
                    logger.info("pihole restartdns login %s at %s: %s", r.name, url, e)
            if sess is None:
                raise last_err or RuntimeError("Pi-hole has no base URL")
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
    forward_port: Optional[int] = None,
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
                forward_port=forward_port,
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
    dest_project: Optional[str] = None,
    port_map: Optional[dict[str, str]] = None,
    adopt_fabric: bool = False,
    log: Optional[LogFn] = None,
    upsert_fn=None,
    npm_put_fn=None,
    restartdns_fn=None,
) -> dict[str, Any]:
    """Direct CNAME → dest dns_name; NPM-fronted PUT forward_host; restartdns if needed.

    Moving the **NPM edge** (the stack whose FQDN is the NPM base URL): public
    names stay CNAME → that alias; only the alias is rewritten to dest. Fabric
    ``via_proxy`` rows flip ``target_server_id`` without a Pi-hole CNAME write.
    """
    name = compose_project_name(project)
    dest_name = compose_project_name(dest_project or project)
    rows = _dns_rows(
        session,
        int(source.id or 0),
        name,
        dest_id=int(dest.id or 0),
    )
    moving_edge = is_npm_edge_project(
        session, int(source.id or 0), name, rows=rows
    )
    upsert = upsert_fn or upsert_service_record
    def _default_npm(fqdn, cached_id, new_host, forward_port=None):
        return _put_npm_backend(
            session,
            fqdn=fqdn,
            cached_id=cached_id,
            new_host=new_host,
            forward_port=forward_port,
        )

    npm_put = npm_put_fn or _default_npm
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
            fwd_port = None
            raw_fp = (match or {}).get("forward_port")
            if raw_fp is not None and port_map:
                try:
                    old_p = int(raw_fp)
                    mapped = port_map.get(port_map_key(old_p, "tcp"))
                    if mapped and str(mapped) != str(old_p):
                        fwd_port = int(mapped)
                except (TypeError, ValueError):
                    fwd_port = None
            _log(
                log,
                f"NPM PUT {fqdn} forward_host → {new_backend}"
                + (f" forward_port → {fwd_port}" if fwd_port else ""),
            )
            try:
                npm_res = npm_put(
                    fqdn, cached_id, new_backend, forward_port=fwd_port
                )
            except TypeError:
                npm_res = npm_put(fqdn, cached_id, new_backend)
            try:
                upsert(
                    session,
                    fqdn=fqdn,
                    target_server_id=int(rec.target_server_id),
                    backend_server_id=int(dest.id),
                    stack_deployment_id=rec.stack_deployment_id,
                    docker_project=dest_name,
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
            except DnsFabricError as e:
                raise CutoverError(str(e)) from e
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
            src_dns = normalize_fqdn(getattr(source, "dns_name", None) or "")
            if (
                upsert_fn is None
                and rec.managed_on_pihole
                and src_dns
                and src_dns != normalize_fqdn(dest_dns)
            ):
                # Retry after a partial cutover: fabric may already point at dest
                # while Pi-hole still has the source-host CNAME.
                fanout_pihole_dns(
                    session,
                    op="delete",
                    kind="cname",
                    domain=normalize_fqdn(fqdn),
                    target=src_dns,
                )
            try:
                upsert(
                    session,
                    fqdn=fqdn,
                    target_server_id=int(dest.id),
                    backend_server_id=int(dest.id),
                    stack_deployment_id=rec.stack_deployment_id,
                    docker_project=dest_name,
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
            except DnsFabricError as e:
                raise CutoverError(str(e)) from e
            need_ftl = need_ftl or bool(rec.managed_on_pihole)
            out.append({"fqdn": fqdn, "action": "cname", "target": dest_dns})

    done_npm = {
        normalize_fqdn(str(x.get("fqdn") or ""))
        for x in out
        if x.get("action") == "npm"
    }
    if not moving_edge:
        for phost in npm_proxy_hosts_for_project(session, name):
            fqdn = str(phost.get("fqdn") or "").strip()
            key = normalize_fqdn(fqdn)
            if not key or key in done_npm:
                continue
            if not new_backend:
                raise CutoverError(
                    f"{fqdn}: dest has no IP/hostname for NPM forward_host"
                )
            cached_id = str(phost.get("npm_id") or "")
            match = _match_npm(npm_hosts, fqdn) if not cached_id else None
            if match:
                cached_id = str(match.get("id") or cached_id)
            fwd_port = None
            raw_fp = phost.get("forward_port")
            if raw_fp is None and match:
                raw_fp = match.get("forward_port")
            if raw_fp is not None and port_map:
                try:
                    old_p = int(raw_fp)
                    mapped = port_map.get(port_map_key(old_p, "tcp"))
                    if mapped and str(mapped) != str(old_p):
                        fwd_port = int(mapped)
                except (TypeError, ValueError):
                    fwd_port = None
            _log(
                log,
                f"NPM PUT {fqdn} forward_host → {new_backend}"
                + (f" forward_port → {fwd_port}" if fwd_port else "")
                + " (proxy-host binding)",
            )
            try:
                npm_res = npm_put(
                    fqdn, cached_id, new_backend, forward_port=fwd_port
                )
            except TypeError:
                npm_res = npm_put(fqdn, cached_id, new_backend)
            done_npm.add(key)
            out.append(
                {
                    "fqdn": fqdn,
                    "action": "npm",
                    "forward_host": new_backend,
                    "from_binding": True,
                    "npm": npm_res if isinstance(npm_res, dict) else {"ok": True},
                }
            )

    if adopt_fabric and not moving_edge:
        edge = npm_edge_server(session)
        if not edge:
            _log(log, "Adopt fabric skipped: no NPM edge host in fabric")
        else:
            for rec_out in list(out):
                if rec_out.get("action") != "npm" or not rec_out.get("from_binding"):
                    continue
                fqdn = str(rec_out.get("fqdn") or "").strip()
                key = normalize_fqdn(fqdn)
                if not key:
                    continue
                existing = session.exec(
                    select(ServiceDnsRecord).where(ServiceDnsRecord.fqdn == key)
                ).first()
                if existing:
                    _log(log, f"Adopt fabric skip {fqdn}: already in DNS list")
                    rec_out["adopted"] = False
                    continue
                _log(
                    log,
                    f"Adopt {fqdn} into fabric via_proxy → {edge.name} "
                    "(no cert, Pi-hole CNAME not rewritten)",
                )
                try:
                    upsert(
                        session,
                        fqdn=fqdn,
                        target_server_id=int(edge.id),
                        backend_server_id=int(dest.id),
                        docker_project=dest_name,
                        label=fqdn.split(".")[0],
                        managed_on_pihole=False,
                        via_proxy=True,
                        npm_hint=str(rec_out.get("npm", {}).get("id") or "") or None,
                        certificate_id=None,
                        external_dns_status="none",
                        notes="Adopted from NPM proxy-host binding on migrate",
                        sync_now=False,
                    )
                except DnsFabricError as e:
                    raise CutoverError(str(e)) from e
                rec_out["adopted"] = True

    if not out:
        _log(log, "No DNS fabric rows or NPM proxy hosts for this project")

    if moving_edge:
        skip = {int(r.id) for r in rows if r.id is not None}
        for rec in npm_edge_dependents(
            session,
            source_id=int(source.id or 0),
            dest_id=int(dest.id or 0),
            skip_ids=skip,
        ):
            fqdn = rec.fqdn
            if int(rec.target_server_id or 0) == int(dest.id or 0):
                _log(log, f"Edge alias already dest for {fqdn}")
                out.append(
                    {
                        "fqdn": fqdn,
                        "action": "keep_cname_on_edge",
                        "target_server_id": int(dest.id),
                    }
                )
                continue
            _log(
                log,
                f"Keep {fqdn} CNAME on NPM edge; fabric target → {dest.name}",
            )
            rec.target_server_id = int(dest.id)
            session.add(rec)
            out.append(
                {
                    "fqdn": fqdn,
                    "action": "keep_cname_on_edge",
                    "target_server_id": int(dest.id),
                }
            )
        session.commit()

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
