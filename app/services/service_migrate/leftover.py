"""Source leftover after a green migrate (v1.4 M8 / M-rm).

Default: leave the source stack stopped with data on disk.
``down``: ``compose down`` (volumes kept).
``remove``: compose down, ``docker volume rm`` copied named volumes, delete
the jailed project directory. Never touches dest.
"""
from __future__ import annotations

import logging
import shlex
from typing import Any, Callable, Optional

from sqlmodel import Session, select

from ...models import CertificateTarget, ComposeProjectMeta, Server
from .. import docker_management as docker_svc
from ..ssh import get_ssh_client, run_command
from .facts import docker_base_abs
from .host_lock import compose_project_name
from .preflight import _dns_rows, named_volume_id

logger = logging.getLogger(__name__)

LogFn = Callable[[str], None]

LEFTOVER_MODES = frozenset({"stopped", "down", "remove"})


class LeftoverError(Exception):
    pass


def normalize_leftover(raw: Optional[str]) -> str:
    v = (raw or "stopped").strip().lower()
    if v not in LEFTOVER_MODES:
        return "stopped"
    return v


def _log(log: Optional[LogFn], msg: str) -> None:
    if log:
        log(msg)
    else:
        logger.info("[migrate-leftover] %s", msg)


def named_volumes_from_dataset(dataset: Optional[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in (dataset or {}).get("items") or []:
        if not isinstance(it, dict):
            continue
        vol = (it.get("volume") or "").strip()
        if it.get("kind") == "named":
            vol = vol or named_volume_id(source=str(it.get("source") or "")) or ""
        if not vol or "/" in vol or ".." in vol or vol in seen:
            continue
        seen.add(vol)
        out.append(vol)
    return out


def jailed_source_project_path(source: Server, project: str) -> str:
    name = compose_project_name(project)
    base = docker_base_abs(source).rstrip("/")
    if not base or base == "/":
        raise LeftoverError("refusing leftover wipe: docker base is empty or /")
    path = f"{base}/{name}"
    if path.rstrip("/") in ("", "/", base, "/home", "/var", "/opt", "/usr"):
        raise LeftoverError("refusing leftover wipe: path is not a project dir")
    return path


def _assert_source_only(source: Server, dest: Server) -> None:
    if int(source.id or 0) == int(dest.id or 0):
        raise LeftoverError("refusing leftover wipe: source and dest are the same host")


def _rm_volume(server: Server, volume: str) -> dict[str, Any]:
    name = (volume or "").strip()
    if not name or "/" in name or ".." in name:
        return {"success": False, "error": f"invalid volume name: {volume!r}"}
    client = get_ssh_client(server)
    try:
        st, out, err = run_command(
            client, f"docker volume rm {shlex.quote(name)}", timeout=120
        )
        output = ((out or "") + (err or "")).strip()
        ok = st == 0 or "no such volume" in output.lower()
        return {
            "success": ok,
            "output": output[-2000:] if output else "",
            "error": None if ok else (output[:300] or f"volume rm {name} failed"),
        }
    finally:
        try:
            client.close()
        except Exception:
            pass


def _rm_tree(server: Server, path: str) -> dict[str, Any]:
    p = (path or "").rstrip("/")
    if not p or p == "/" or ".." in p:
        return {"success": False, "error": "refusing rm of unsafe path"}
    client = get_ssh_client(server)
    try:
        st, out, err = run_command(client, f"rm -rf -- {shlex.quote(p)}", timeout=180)
        output = ((out or "") + (err or "")).strip()
        return {
            "success": st == 0,
            "output": output[-2000:] if output else "",
            "error": None if st == 0 else (output[:300] or "project dir remove failed"),
        }
    finally:
        try:
            client.close()
        except Exception:
            pass


def _disable_source_cert_targets(
    session: Session, *, source: Server, dest: Server, project: str
) -> int:
    """Disable source CertificateTarget rows that were cloned onto dest."""
    name = compose_project_name(project)
    cert_ids: set[int] = set()
    for rec in _dns_rows(session, int(dest.id or 0), name):
        if rec.certificate_id:
            cert_ids.add(int(rec.certificate_id))
    if not cert_ids:
        return 0
    n = 0
    sid = int(source.id or 0)
    for row in session.exec(
        select(CertificateTarget).where(
            CertificateTarget.server_id == sid,
            CertificateTarget.certificate_id.in_(list(cert_ids)),
        )
    ).all():
        if row.enabled:
            row.enabled = False
            session.add(row)
            n += 1
    return n


def _drop_source_project_meta(
    session: Session, *, source: Server, project: str
) -> int:
    name = compose_project_name(project)
    row = session.exec(
        select(ComposeProjectMeta).where(
            ComposeProjectMeta.server_id == int(source.id or 0),
            ComposeProjectMeta.compose_project == name,
        )
    ).first()
    if not row:
        return 0
    session.delete(row)
    return 1


def apply_leftover(
    session: Session,
    *,
    source: Server,
    dest: Server,
    project: str,
    leftover: str = "stopped",
    dataset: Optional[dict[str, Any]] = None,
    src_proj: Optional[str] = None,
    dest_project: Optional[str] = None,
    down_fn=None,
    rm_vol_fn=None,
    rm_tree_fn=None,
    log: Optional[LogFn] = None,
) -> dict[str, Any]:
    """Run leftover policy on **source only**. Dest is never mutated here."""
    mode = normalize_leftover(leftover)
    name = compose_project_name(project)
    out: dict[str, Any] = {
        "leftover": mode,
        "volumes_removed": [],
        "project_removed": False,
        "certs_disabled": 0,
        "meta_dropped": 0,
    }
    if mode == "stopped":
        return out

    _assert_source_only(source, dest)
    path = (src_proj or "").rstrip("/") or jailed_source_project_path(source, name)
    want = jailed_source_project_path(source, name)
    if path != want:
        raise LeftoverError(
            f"refusing leftover wipe: project path {path!r} is not {want!r}"
        )

    down = down_fn or (lambda srv, p: docker_svc.compose_action(srv, p, "down"))
    _log(
        log,
        f"compose down on source {source.name}"
        + (" (then wipe project + named volumes)" if mode == "remove" else " (volumes kept)"),
    )
    stopped = down(source, path)
    if isinstance(stopped, dict) and not stopped.get("success", True):
        raise LeftoverError(stopped.get("error") or "source compose down failed")

    if mode == "down":
        return out

    vols = named_volumes_from_dataset(dataset)
    rm_vol = rm_vol_fn or _rm_volume
    for vol in vols:
        _log(log, f"docker volume rm {vol} on {source.name} (not dest)")
        r = rm_vol(source, vol)
        if isinstance(r, dict) and not r.get("success", True):
            raise LeftoverError(r.get("error") or f"volume rm {vol} failed")
        out["volumes_removed"].append(vol)

    _log(log, f"remove source project dir {path}")
    rm_tree = rm_tree_fn or _rm_tree
    tree = rm_tree(source, path)
    if isinstance(tree, dict) and not tree.get("success", True):
        raise LeftoverError(tree.get("error") or "source project dir remove failed")
    out["project_removed"] = True

    out["certs_disabled"] = _disable_source_cert_targets(
        session,
        source=source,
        dest=dest,
        project=dest_project or name,
    )
    out["meta_dropped"] = _drop_source_project_meta(
        session, source=source, project=name
    )
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise
    return out
