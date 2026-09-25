"""Fail-path Move undo (v1.6 Undo-1).

Only a **failed** Move whose ``failed_step`` is ``cutover``, ``rebind``, or
``validate``. Never a green Move. Never dest ``down -v`` / volume rm / project
rm. Dest directory and volumes stay. Pre-flip failures stay on Start source.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional

from sqlmodel import Session, select

from ...models import CertificateTarget, Job, Server, ServiceDnsRecord
from .cutover import retarget_dns_npm
from .host_lock import compose_project_name
from .leftover import jailed_source_project_path
from .overrides import port_map_key
from .rebind import rebind_control_plane

logger = logging.getLogger(__name__)

LogFn = Callable[[str], None]

# Names have flipped (or a flip was in progress). Pre-flip is not in this set.
UNDO_STEPS = frozenset({"cutover", "rebind", "validate"})


class UndoError(Exception):
    def __init__(self, message: str, status_code: int = 400, steps: list[str] | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = int(status_code)
        # Steps that stayed committed. A rolled-back stop is not included.
        self.steps = list(steps or [])


def _log(log: Optional[LogFn], msg: str) -> None:
    if log:
        log(msg)
    else:
        logger.info("[migrate-undo] %s", msg)


def _details(job: Job) -> dict[str, Any]:
    try:
        data = json.loads(job.details or "{}") or {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def undo_move_details(
    *,
    source_id: int,
    dest_id: int,
    project: str,
    dest_project: str | None,
    port_map: dict | None,
    failed_step: str | None,
) -> dict[str, Any] | None:
    """Payload stored on a failed Move. None when this failure is not undoable."""
    step = (failed_step or "").strip()
    if step not in UNDO_STEPS:
        return None
    try:
        name = compose_project_name(project)
        dest_name = compose_project_name(dest_project or project)
    except Exception:
        return None
    return {
        "source_id": int(source_id),
        "dest_id": int(dest_id),
        "project": name,
        "dest_project": dest_name,
        "port_map": dict(port_map or {}),
        "failed_step": step,
    }


def eligible_undo(job: Job | None) -> dict[str, Any] | None:
    """Return the stored payload when this failed Move can be undone."""
    if job is None or (job.job_type or "") != "service_migrate":
        return None
    if (job.status or "") != "failed":
        return None
    data = _details(job)
    step = str(data.get("failed_step") or "")
    if step not in UNDO_STEPS:
        return None
    raw = data.get("undo_move")
    if not isinstance(raw, dict):
        raw = undo_move_details(
            source_id=int(data.get("source_id") or job.server_id or 0),
            dest_id=int(data.get("dest_server_id") or 0),
            project=str(data.get("project") or ""),
            dest_project=data.get("dest_project"),
            port_map=data.get("port_map") if isinstance(data.get("port_map"), dict) else {},
            failed_step=step,
        )
    if not raw:
        return None
    if int(raw.get("source_id") or 0) <= 0 or int(raw.get("dest_id") or 0) <= 0:
        return None
    if not raw.get("project"):
        return None
    return raw


def invert_port_map(port_map: dict | None) -> dict[str, str]:
    """Dest published port → original source port, for the NPM PUT on the way back."""
    out: dict[str, str] = {}
    for raw_key, raw_val in (port_map or {}).items():
        key = str(raw_key).strip().lower()
        val = str(raw_val).strip()
        if "/" not in key or not val.isdigit():
            continue
        host_s, proto = key.split("/", 1)
        if not host_s.isdigit():
            continue
        out[port_map_key(val, proto)] = str(int(host_s))
    return out


def _project_rows(
    session: Session, source_id: int, dest_id: int, project: str, dest_project: str
) -> list[ServiceDnsRecord]:
    names = {project, dest_project}
    ids = {int(source_id), int(dest_id)}
    rows: list[ServiceDnsRecord] = []
    seen: set[int] = set()
    for rec in session.exec(select(ServiceDnsRecord)).all():
        if (rec.docker_project or "") not in names:
            continue
        if int(rec.backend_server_id or 0) not in ids and int(rec.target_server_id or 0) not in ids:
            continue
        rid = int(rec.id) if rec.id is not None else None
        if rid is not None and rid in seen:
            continue
        if rid is not None:
            seen.add(rid)
        rows.append(rec)
    return rows


def preview_undo(session: Session, job: Job) -> dict[str, Any]:
    """FQDNs, NPM names, and dest project path. No SSH and no writes."""
    payload = eligible_undo(job)
    if not payload:
        raise UndoError("Undo is only for a failed Move after names flipped")
    source = session.get(Server, int(payload["source_id"]))
    dest = session.get(Server, int(payload["dest_id"]))
    if not source or not dest:
        raise UndoError("source or destination host is gone")
    project = str(payload["project"])
    dest_project = str(payload.get("dest_project") or project)
    rows = _project_rows(session, source.id, dest.id, project, dest_project)
    fqdns = []
    npm = []
    for rec in rows:
        fqdn = (rec.fqdn or "").strip()
        if not fqdn:
            continue
        fqdns.append(fqdn)
        if rec.via_proxy:
            npm.append(fqdn)
    try:
        dest_path = jailed_source_project_path(dest, dest_project)
        source_path = jailed_source_project_path(source, project)
    except Exception as e:
        raise UndoError(str(e)) from e
    return {
        "parent_job_id": job.id,
        "failed_step": payload.get("failed_step"),
        "project": project,
        "dest_project": dest_project,
        "source_id": int(source.id),
        "dest_id": int(dest.id),
        "source_name": source.name,
        "dest_name": dest.name,
        "source_path": source_path,
        "dest_path": dest_path,
        "fqdns": fqdns,
        "npm": npm,
        "keeps_dest_dir": True,
        "keeps_dest_volumes": True,
    }


def reenable_source_certs(
    session: Session, source: Server, project: str, dest_project: str
) -> int:
    """Turn source cert targets back on. Do not delete the dest clone."""
    sid = int(source.id or 0)
    names = {compose_project_name(project), compose_project_name(dest_project or project)}
    cert_ids: set[int] = set()
    for rec in session.exec(select(ServiceDnsRecord)).all():
        if (rec.docker_project or "") not in names or not rec.certificate_id:
            continue
        cert_ids.add(int(rec.certificate_id))
    n = 0
    for cid in cert_ids:
        row = session.exec(
            select(CertificateTarget).where(
                CertificateTarget.server_id == sid,
                CertificateTarget.certificate_id == cid,
            )
        ).first()
        if row is not None and not row.enabled:
            row.enabled = True
            session.add(row)
            n += 1
    if n:
        session.commit()
    return n


def _compose_stop(server: Server, path: str) -> dict[str, Any]:
    from ..docker_management import compose_action

    return compose_action(server, path, "stop", remove_volumes=False)


def _compose_start(server: Server, path: str) -> dict[str, Any]:
    from ..docker_management import compose_action

    return compose_action(server, path, "start")


def _ok(result: Any) -> bool:
    if not isinstance(result, dict):
        return True
    if result.get("success") is False or result.get("ok") is False:
        return False
    return True


def run_undo_pipeline(
    session: Session,
    *,
    source: Server,
    dest: Server,
    project: str,
    dest_project: str,
    port_map: dict | None = None,
    log: Optional[LogFn] = None,
    dns_fn=None,
    rebind_fn=None,
    stop_fn=None,
    start_fn=None,
    cert_fn=None,
    done: list[str] | None = None,
) -> dict[str, Any]:
    """Stop dest, then revert names, then start source. Dest tree and volumes stay.

    Stop runs first so a stop failure leaves names on the dest that is still up.
    ``done`` lists steps a previous attempt already committed. If names have not
    moved yet and a later step fails, dest is started again.
    """
    name = compose_project_name(project)
    dest_name = compose_project_name(dest_project or project)
    dest_path = jailed_source_project_path(dest, dest_name)
    source_path = jailed_source_project_path(source, name)
    back_ports = invert_port_map(port_map)

    def _default_dns(*_args, **_kwargs):
        return retarget_dns_npm(
            session,
            source=dest,
            dest=source,
            project=dest_name,
            dest_project=name,
            port_map=back_ports,
            adopt_fabric=False,
            log=log,
        )

    dns = dns_fn or _default_dns
    rebind = rebind_fn or rebind_control_plane
    stop = stop_fn or _compose_stop
    start = start_fn or _compose_start
    certs = cert_fn or reenable_source_certs
    steps = [s for s in (done or []) if s in ("stop", "dns", "rebind", "certs")]

    def _fail(msg: str, *, restart_dest: bool = False) -> None:
        if restart_dest:
            _log(log, "Names were not moved; starting dest again")
            back = start(dest, dest_path)
            if isinstance(back, dict) and back.get("output"):
                _log(log, str(back.get("output") or "")[-800:])
            if not _ok(back):
                _log(log, "Dest was stopped and could not be started again")
            elif "stop" in steps:
                steps.remove("stop")
        raise UndoError(msg, steps=list(steps))

    if "stop" in steps:
        _log(log, "Dest stop already committed on a previous undo")
    else:
        _log(log, f"Stopping dest project {dest_name} at {dest_path} (compose stop, not down -v)")
        stopped = stop(dest, dest_path)
        if isinstance(stopped, dict) and stopped.get("action") == "down":
            raise UndoError("refusing undo: dest down is not allowed", steps=list(steps))
        if isinstance(stopped, dict) and stopped.get("output"):
            _log(log, str(stopped.get("output") or "")[-800:])
        if not _ok(stopped):
            err = ""
            if isinstance(stopped, dict):
                err = str(stopped.get("error") or stopped.get("output") or "")
            raise UndoError(err or "dest compose stop failed", steps=list(steps))
        steps.append("stop")

    if "dns" in steps:
        dns_out = {"ok": True, "skipped": True}
        _log(log, "DNS / NPM revert already committed on a previous undo")
    else:
        _log(log, f"Reverting DNS / NPM for {name} → {source.name}")
        try:
            try:
                dns_out = dns(
                    session,
                    source=dest,
                    dest=source,
                    project=dest_name,
                    dest_project=name,
                    port_map=back_ports,
                    adopt_fabric=False,
                    log=log,
                )
            except TypeError:
                dns_out = dns(session, source=dest, dest=source, project=dest_name, log=log)
        except UndoError:
            raise
        except Exception as exc:
            _fail(str(exc) or "DNS revert failed", restart_dest=True)
        if not _ok(dns_out):
            err = ""
            if isinstance(dns_out, dict):
                err = str(dns_out.get("error") or dns_out.get("output") or "")
            _fail(err or "DNS revert failed", restart_dest=True)
        steps.append("dns")

    if "rebind" in steps:
        rebind_out = {"ok": True, "skipped": True}
        _log(log, "Control-plane rebind already committed on a previous undo")
    else:
        _log(log, "Rebinding control-plane rows back to source")
        try:
            try:
                rebind_out = rebind(
                    session,
                    source=dest,
                    dest=source,
                    project=dest_name,
                    dest_project=name,
                    log=log,
                )
            except TypeError:
                rebind_out = rebind(session, source=dest, dest=source, project=dest_name, log=log)
        except UndoError:
            raise
        except Exception as exc:
            _fail(str(exc) or "rebind failed")
        if not _ok(rebind_out):
            err = ""
            if isinstance(rebind_out, dict):
                err = str(rebind_out.get("error") or "")
            _fail(err or "rebind failed")
        steps.append("rebind")

    if "certs" in steps:
        cert_n = 0
        _log(log, "Certificate re-enable already committed on a previous undo")
    else:
        try:
            try:
                cert_n = certs(session, source, name, dest_name)
            except TypeError:
                cert_n = certs(session, source, name)
        except UndoError:
            raise
        except Exception as exc:
            _fail(str(exc) or "certificate re-enable failed")
        if cert_n:
            _log(log, f"Re-enabled {cert_n} source certificate target(s); dest clone kept")
        steps.append("certs")

    _log(log, f"Starting source project {name} at {source_path}")
    started = start(source, source_path)
    if isinstance(started, dict) and started.get("output"):
        _log(log, str(started.get("output") or "")[-800:])
    if not _ok(started):
        err = ""
        if isinstance(started, dict):
            err = str(started.get("error") or started.get("output") or "")
        raise UndoError(err or "source compose start failed", steps=list(steps))

    _log(log, "Undo complete. Dest directory and volumes were left in place.")
    return {
        "ok": True,
        "project": name,
        "dest_project": dest_name,
        "dest_path": dest_path,
        "source_path": source_path,
        "dns": dns_out if isinstance(dns_out, dict) else {"ok": True},
        "rebind": rebind_out if isinstance(rebind_out, dict) else {"ok": True},
        "certs_reenabled": int(cert_n or 0),
        "dest_removed": False,
    }
