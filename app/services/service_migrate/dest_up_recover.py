"""Undo-2: worker died during dest_up.

Dest may already be running, so Start source alone would dual-run. Inspect
dest, then compose stop dest and compose start source. Names, DNS, and NPM
stay. Never dest ``down -v``. A green Move is not this helper.
"""
from __future__ import annotations

import json
import logging
import re
import shlex
from typing import Any, Callable, Optional

from ...models import Job, Server
from .host_lock import compose_project_name
from .leftover import jailed_source_project_path

logger = logging.getLogger(__name__)

LogFn = Callable[[str], None]
JOB_TYPE = "service_migrate_dest_recover"


class DestUpRecoverError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = int(status_code)


def _log(log: Optional[LogFn], msg: str) -> None:
    if log:
        log(msg)
    else:
        logger.info("[dest-up-recover] %s", msg)


def _details(job: Job) -> dict[str, Any]:
    try:
        data = json.loads(job.details or "{}") or {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def dest_up_recover_details(
    *,
    source_id: int,
    dest_id: int,
    project: str,
    dest_project: str | None,
) -> dict[str, Any] | None:
    """Payload stored when a Move dies during dest_up. None if names are missing."""
    try:
        name = compose_project_name(project)
        dest_name = compose_project_name(dest_project or project)
    except Exception:
        return None
    if int(source_id or 0) <= 0 or int(dest_id or 0) <= 0:
        return None
    return {
        "source_id": int(source_id),
        "dest_id": int(dest_id),
        "project": name,
        "dest_project": dest_name,
    }


def eligible_dest_up_recover(job: Job | None) -> dict[str, Any] | None:
    """Failed Move whose worker died during dest_up. Not a green Move. Not Undo-1."""
    if job is None or (job.job_type or "") != "service_migrate":
        return None
    if (job.status or "") != "failed":
        return None
    data = _details(job)
    if data.get("dest_up_recovered"):
        return None
    if data.get("recover_source") or data.get("undo_move"):
        return None
    if str(data.get("failed_step") or "") != "worker_restart":
        return None
    if str(data.get("migrate_step") or "") != "dest_up":
        return None
    raw = data.get("dest_up_recover")
    if not isinstance(raw, dict):
        return None
    if int(raw.get("source_id") or 0) <= 0 or int(raw.get("dest_id") or 0) <= 0:
        return None
    if not raw.get("project"):
        return None
    return raw


def _paths(source: Server, dest: Server, payload: dict[str, Any]) -> tuple[str, str, str, str]:
    name = compose_project_name(str(payload.get("project") or ""))
    dest_name = compose_project_name(str(payload.get("dest_project") or name))
    return (
        name,
        dest_name,
        jailed_source_project_path(source, name),
        jailed_source_project_path(dest, dest_name),
    )


def dest_ps_shows_running(output: str) -> bool:
    """True when compose ps output has a container that is up."""
    text = output or ""
    saw_json = False
    running = False
    for line in text.splitlines():
        raw = line.strip()
        if not raw.startswith("{"):
            continue
        saw_json = True
        try:
            row = json.loads(raw)
        except Exception:
            continue
        state = str(row.get("State") or row.get("Status") or "").lower()
        if state == "running" or state.startswith("up"):
            running = True
    if saw_json:
        return running
    return bool(re.search(r"\bUp\b", text))


def inspect_dest(source: Server, dest: Server, payload: dict[str, Any]) -> dict[str, Any]:
    """Read dest compose ps. Does not stop, start, or change DNS."""
    name, dest_name, source_path, dest_path = _paths(source, dest, payload)
    from ..ssh import get_ssh_client, run_command

    cmd = (
        f"cd {shlex.quote(dest_path)} && "
        "docker compose ps -a --format json 2>&1"
    )
    client = get_ssh_client(dest)
    try:
        status, out, err = run_command(client, cmd, timeout=60)
    finally:
        try:
            client.close()
        except Exception:
            pass
    output = ((out or "") + (err or "")).strip()
    ok = status == 0
    return {
        "project": name,
        "dest_project": dest_name,
        "source_name": source.name,
        "dest_name": dest.name,
        "source_path": source_path,
        "dest_path": dest_path,
        "dest_running": dest_ps_shows_running(output) if ok else False,
        "ps_ok": ok,
        "output": output[-1500:],
        "dns_changed": False,
        "volumes_removed": False,
        "error": None if ok else (output[:300] or "could not list dest containers"),
    }


def _ok(result: Any) -> bool:
    if not isinstance(result, dict):
        return True
    if result.get("success") is False or result.get("ok") is False:
        return False
    return True


def _compose_stop(server: Server, path: str) -> dict[str, Any]:
    from ..docker_management import compose_action

    return compose_action(server, path, "stop", remove_volumes=False)


def _compose_start(server: Server, path: str) -> dict[str, Any]:
    from ..docker_management import compose_action

    return compose_action(server, path, "start")


def run_dest_up_recover(
    *,
    source: Server,
    dest: Server,
    project: str,
    dest_project: str,
    log: Optional[LogFn] = None,
    stop_fn=None,
    start_fn=None,
) -> dict[str, Any]:
    """compose stop dest, then compose start source. No DNS. No down -v.

    A stop failure does not start source.
    """
    name = compose_project_name(project)
    dest_name = compose_project_name(dest_project or project)
    dest_path = jailed_source_project_path(dest, dest_name)
    source_path = jailed_source_project_path(source, name)
    stop = stop_fn or _compose_stop
    start = start_fn or _compose_start

    _log(log, f"Stopping dest project {dest_name} at {dest_path} (compose stop, not down -v)")
    _log(log, "DNS and NPM are not changed")
    stopped = stop(dest, dest_path)
    if isinstance(stopped, dict) and stopped.get("action") == "down":
        raise DestUpRecoverError("refusing: dest down is not allowed")
    if isinstance(stopped, dict) and stopped.get("output"):
        _log(log, str(stopped.get("output") or "")[-800:])
    if not _ok(stopped):
        err = ""
        if isinstance(stopped, dict):
            err = str(stopped.get("error") or stopped.get("output") or "")
        raise DestUpRecoverError(err or "dest compose stop failed")

    _log(log, f"Starting source project {name} at {source_path}")
    started = start(source, source_path)
    if isinstance(started, dict) and started.get("output"):
        _log(log, str(started.get("output") or "")[-800:])
    if not _ok(started):
        err = ""
        if isinstance(started, dict):
            err = str(started.get("error") or started.get("output") or "")
        raise DestUpRecoverError(err or "source compose start failed")

    _log(log, "Dest is stopped and source is started. Dest directory and volumes stay.")
    return {
        "ok": True,
        "project": name,
        "dest_project": dest_name,
        "dest_path": dest_path,
        "source_path": source_path,
        "dns_changed": False,
        "volumes_removed": False,
    }
