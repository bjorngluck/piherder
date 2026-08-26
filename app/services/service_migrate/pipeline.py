"""Stop-first migrate job: copy then dest up (v1.4 M3 + dest start)."""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, Callable, Optional

from sqlmodel import Session

from ...config import settings
from ...models import Server
from .. import docker_inventory as inventory_svc
from .. import docker_management as docker_svc
from .copy import (
    copy_named_volume,
    rsync_herder_to_host,
    rsync_host_to_herder,
    staging_tree_summary,
)
from .cutover import CutoverError, retarget_dns_npm
from .facts import docker_base_abs
from .host_lock import compose_project_name
from .leftover import LeftoverError, apply_leftover, normalize_leftover
from .overrides import apply_staging_overrides, remap_named_volume
from .preflight import named_volume_id, run_preflight
from .rebind import rebind_control_plane
from .validate import ValidateError, validate_migrate

logger = logging.getLogger(__name__)

LogFn = Callable[[str], None]


class MigrateError(Exception):
    pass


def _ssh_fail_detail(result: Any, fallback: str) -> str:
    """Compose/SSH dict → error string that includes command output."""
    if not isinstance(result, dict):
        return fallback
    err = str(result.get("error") or "").strip()
    out = str(result.get("output") or "").strip()
    if err and out and err not in out:
        msg = f"{err}\n{out}"
    else:
        msg = out or err or fallback
    return msg[-1500:] or fallback


def staging_root(job_id: int) -> Path:
    root = Path(getattr(settings, "BACKUP_ROOT", None) or "/backups") / "_migrate" / str(int(job_id))
    return root


def _log(log: Optional[LogFn], msg: str) -> None:
    if log:
        log(msg)
    else:
        logger.info("[migrate] %s", msg)


def run_copy_and_start(
    session: Session,
    *,
    source: Server,
    dest: Server,
    project: str,
    job_id: int,
    source_facts: Optional[dict[str, Any]] = None,
    dest_facts: Optional[dict[str, Any]] = None,
    herder_free: Optional[int] = None,
    log: Optional[LogFn] = None,
    pull_fn=None,
    push_fn=None,
    vol_fn=None,
    stop_fn=None,
    up_fn=None,
    cutover_fn=None,
    rebind_fn=None,
    validate_fn=None,
    leftover: str = "stopped",
    devices_ack: bool = False,
    dest_project: Optional[str] = None,
    port_map: Optional[dict[str, str]] = None,
    bind_map: Optional[dict[str, str]] = None,
    skip_binds: Optional[list[str]] = None,
    live_inspect: bool = False,
    down_fn=None,
    rm_vol_fn=None,
    rm_tree_fn=None,
) -> dict[str, Any]:
    """Preflight → stop → copy → dest up → DNS/NPM → rebind → validate → leftover."""
    name = compose_project_name(project)
    pf = run_preflight(
        session,
        source=source,
        dest=dest,
        project=name,
        source_facts=source_facts,
        dest_facts=dest_facts,
        herder_free=herder_free,
        live_inspect=live_inspect,
        dest_project=dest_project,
        port_map=port_map,
        bind_overrides=(
            [
                {"source": src, "dest": dest, "skip": False}
                for src, dest in (bind_map or {}).items()
            ]
            + [
                {"source": src, "dest": "", "skip": True}
                for src in (skip_binds or [])
            ]
        ),
        ignore_job_id=job_id,
    )
    if not pf.get("ok"):
        msgs = "; ".join(b.get("message") or b.get("id") for b in pf.get("blocks") or [])
        raise MigrateError(f"preflight blocked: {msgs}")
    if any(w.get("id") == "devices" for w in (pf.get("warns") or [])) and not devices_ack:
        raise MigrateError(
            "Hardware-looking mounts: lock the project or acknowledge the warning"
        )

    pull = pull_fn or rsync_host_to_herder
    push = push_fn or rsync_herder_to_host
    vol = vol_fn or copy_named_volume
    stop = stop_fn or (lambda srv, path: docker_svc.compose_action(srv, path, "stop"))
    up = up_fn or (
        lambda srv, path: docker_svc.redeploy_project(srv, path, pull=True)
    )

    dest_name = str(pf.get("dest_project") or name)
    clean_map = pf.get("port_map") or {}
    src_base = docker_base_abs(source).rstrip("/")
    dst_base = docker_base_abs(dest).rstrip("/")
    src_proj = f"{src_base}/{name}"
    dst_proj = f"{dst_base}/{dest_name}"
    stage = staging_root(job_id)
    stage.mkdir(parents=True, exist_ok=True)
    try:
        stage.chmod(0o700)
    except Exception:
        pass
    proj_stage = stage / "project"
    proj_stage.mkdir(exist_ok=True)

    _log(log, f"Stopping {name} on {source.name}…")
    stopped = stop(source, src_proj)
    if isinstance(stopped, dict) and not stopped.get("success", True):
        raise MigrateError(_ssh_fail_detail(stopped, "compose stop failed"))

    _log(log, f"Copy project tree {src_proj} → staging (verbatim, all files)")
    try:
        pull(source, src_proj, proj_stage, log=log, delete=True)
    except TypeError:
        pull(source, src_proj, proj_stage, log=log)
    _log(log, "Staged project: " + staging_tree_summary(proj_stage))

    dataset = pf.get("dataset") or {}
    seen_vol: set[str] = set()
    for it in dataset.get("items") or []:
        kind = it.get("kind")
        src = (it.get("source") or "").strip()
        vol_name = (it.get("volume") or "").strip() or named_volume_id(source=src) or ""
        if kind == "named":
            vol_name = vol_name or src
            if not vol_name or vol_name in seen_vol:
                continue
            seen_vol.add(vol_name)
            dest_vol = remap_named_volume(vol_name, name, dest_name)
            label = vol_name if dest_vol == vol_name else f"{vol_name} → {dest_vol}"
            _log(log, f"Copy named volume {label}")
            try:
                vol(
                    source,
                    dest,
                    vol_name,
                    stage,
                    dest_volume=dest_vol,
                    log=log,
                )
            except TypeError:
                vol(source, dest, vol_name, stage, log=log)
        elif kind == "bind_absolute":
            from .overrides import is_truncated_host_path

            if is_truncated_host_path(src):
                raise MigrateError(
                    f"refusing truncated inventory bind path: {src}"
                )
            mapped = (pf.get("bind_map") or {}).get(src) or (
                dst_base + src[len(src_base) :]
                if src.startswith(src_base + "/")
                else ""
            )
            if not mapped:
                _log(log, f"Skip bind {src} (not copied)")
                continue
            rel_key = src.lstrip("/").replace("..", "_")
            extra = stage / "binds" / rel_key
            extra.mkdir(parents=True, exist_ok=True)
            _log(log, f"Copy bind {src} → {mapped}")
            try:
                pull(source, src, extra, log=log)
            except TypeError:
                pull(source, src, extra, log=log)
            try:
                push(dest, extra, mapped, log=log, delete=True)
            except TypeError:
                push(dest, extra, mapped, log=log)

    volume_renames = {
        vol_name: remap_named_volume(vol_name, name, dest_name)
        for vol_name in seen_vol
        if remap_named_volume(vol_name, name, dest_name) != vol_name
    }
    compose_binds = dict(pf.get("bind_map") or {})
    for it in dataset.get("items") or []:
        if not isinstance(it, dict) or it.get("kind") != "bind_absolute":
            continue
        src = (it.get("source") or "").strip()
        if src.startswith(src_base + "/") and src not in compose_binds:
            compose_binds[src] = dst_base + src[len(src_base) :]
    rewritten = apply_staging_overrides(
        proj_stage,
        dest_project=dest_name,
        source_project=name,
        port_map=clean_map,
        volume_renames=volume_renames,
        bind_map=compose_binds,
    )
    if rewritten.get("files"):
        _log(log, "Rewrote dest compose/env: " + ", ".join(rewritten["files"][:8]))

    _log(log, f"Push project tree → {dst_proj} (verbatim --delete)")
    try:
        push(dest, proj_stage, dst_proj, log=log, delete=True)
    except TypeError:
        push(dest, proj_stage, dst_proj, log=log)

    _log(log, f"Starting {dest_name} on {dest.name} (compose up -d)…")
    started = up(dest, dst_proj)
    if isinstance(started, dict) and started.get("output"):
        _log(log, str(started.get("output") or "")[-1500:])
    up_ok = True
    if isinstance(started, dict):
        if "up_ok" in started:
            up_ok = bool(started.get("up_ok"))
        elif started.get("success") is False:
            up_ok = False
        if started.get("pull") and started.get("pull_ok") is False and up_ok:
            _log(log, "compose pull had errors; dest up succeeded — continuing")
    if not up_ok:
        raise MigrateError(_ssh_fail_detail(started, "dest up failed"))

    dns_fn = cutover_fn or retarget_dns_npm
    _log(log, "Retargeting DNS / NPM…")
    try:
        try:
            dns_out = dns_fn(
                session,
                source=source,
                dest=dest,
                project=name,
                dest_project=dest_name,
                port_map=clean_map,
                log=log,
            )
        except TypeError:
            dns_out = dns_fn(session, source=source, dest=dest, project=name, log=log)
    except CutoverError as e:
        raise MigrateError(str(e)) from e

    rb_fn = rebind_fn or rebind_control_plane
    _log(log, "Rebinding control-plane rows…")
    try:
        rebind_out = rb_fn(
            session,
            source=source,
            dest=dest,
            project=name,
            dest_project=dest_name,
            log=log,
        )
    except TypeError:
        rebind_out = rb_fn(session, source=source, dest=dest, project=name, log=log)

    val_fn = validate_fn or validate_migrate
    _log(log, "Validating TLS / Kuma…")
    try:
        val_out = val_fn(
            session, source=source, dest=dest, project=dest_name, log=log
        )
    except ValidateError as e:
        raise MigrateError(str(e)) from e

    leftover_mode = normalize_leftover(leftover)
    try:
        leftover_out = apply_leftover(
            session,
            source=source,
            dest=dest,
            project=name,
            leftover=leftover_mode,
            dataset=dataset,
            src_proj=src_proj,
            dest_project=dest_name,
            down_fn=down_fn,
            rm_vol_fn=rm_vol_fn,
            rm_tree_fn=rm_tree_fn,
            log=log,
        )
    except LeftoverError as e:
        raise MigrateError(str(e)) from e

    try:
        inventory_svc.mark_stale(session, source)
        inventory_svc.mark_stale(session, dest)
    except Exception:
        pass

    _log(log, "Migrate complete.")
    return {
        "ok": True,
        "project": name,
        "dest_project": dest_name,
        "source_id": source.id,
        "dest_id": dest.id,
        "staging": str(stage),
        "dns": dns_out if isinstance(dns_out, dict) else {"ok": True},
        "rebind": rebind_out if isinstance(rebind_out, dict) else {"ok": True},
        "validate": val_out if isinstance(val_out, dict) else {"ok": True},
        "leftover": leftover_mode,
        "leftover_detail": leftover_out if isinstance(leftover_out, dict) else {"leftover": leftover_mode},
    }


def wipe_staging(job_id: int) -> None:
    root = staging_root(job_id)
    if root.is_dir() and "_migrate" in str(root):
        shutil.rmtree(root, ignore_errors=True)
