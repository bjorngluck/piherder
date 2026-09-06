"""Rsync trees host ↔ herder for service migrate (v1.4 M3)."""
from __future__ import annotations

import logging
import shlex
import subprocess
from pathlib import Path
from typing import Callable, Optional

from ...models import Server
from ..backup import _build_rsync_ssh_cmd, _remote_rsync_path
from ..ssh import get_private_key_plain, get_ssh_client, run_command, temp_key_file

logger = logging.getLogger(__name__)

LogFn = Callable[[str], None]


class CopyError(Exception):
    pass


def _log(log: Optional[LogFn], msg: str) -> None:
    if log:
        log(msg)
    else:
        logger.info("[migrate-copy] %s", msg)


def _ssh_rsync_cmd(key_path: str, server: Server) -> str:
    port = int(getattr(server, "ssh_port", None) or 22)
    base = _build_rsync_ssh_cmd(key_path)
    if f"-p {port}" in base or "-p" in base.split():
        return base
    return f"{base} -p {port}"


def staging_tree_summary(root: Path, *, limit: int = 40) -> str:
    """Short listing of a copied tree (for the job log)."""
    if not root.is_dir():
        return "(missing)"
    names: list[str] = []
    files = 0
    dirs = 0
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if not rel or rel == ".":
            continue
        if p.is_dir():
            dirs += 1
            names.append(rel + "/")
        else:
            files += 1
            names.append(rel)
    extra = ""
    if len(names) > limit:
        extra = f" … +{len(names) - limit} more"
        names = names[:limit]
    return f"{files} file(s), {dirs} dir(s): " + ", ".join(names) + extra


def _rsync_core_args(*, delete: bool = False) -> list[str]:
    # -a: recurse, perms, times, links, devices, **dotfiles**. No gitignore.
    args = ["rsync", "-aH", "--numeric-ids", "--info=stats1"]
    if delete:
        args.append("--delete")
    return args


def remote_path_kind(server: Server, remote_path: str) -> str:
    """Classify a remote path: dir, file, socket, fifo, chr, blk, missing, other."""
    path = (remote_path or "").strip().rstrip("/")
    if not path or path == "/" or ".." in path.split("/"):
        return "missing"
    q = shlex.quote(path)
    cmd = (
        f"p={q}; "
        'if [ -d "$p" ]; then echo dir; '
        'elif [ -S "$p" ]; then echo socket; '
        'elif [ -p "$p" ]; then echo fifo; '
        'elif [ -b "$p" ]; then echo blk; '
        'elif [ -c "$p" ]; then echo chr; '
        'elif [ -f "$p" ]; then echo file; '
        'elif [ -e "$p" ]; then echo other; '
        "else echo missing; fi"
    )
    client = get_ssh_client(server)
    try:
        st, out, _err = run_command(client, cmd, timeout=15)
        kind = (out or "").strip().splitlines()[-1] if (out or "").strip() else ""
        if st != 0 or not kind:
            return "missing"
        return kind
    finally:
        try:
            client.close()
        except Exception:
            pass


def rsync_host_to_herder(
    server: Server,
    remote_path: str,
    local_dir: Path,
    *,
    log: Optional[LogFn] = None,
    delete: bool = False,
    as_file: bool = False,
) -> None:
    """Pull remote directory contents into local_dir (trailing slash).

    ``as_file=True`` copies a single regular file onto ``local_dir`` as the
    destination file path (no trailing slash). Sockets/devices must not be
    pulled — callers skip those.
    """
    from .overrides import is_host_local_bind, is_truncated_host_path

    if is_truncated_host_path(remote_path or ""):
        raise CopyError(
            f"refusing truncated inventory path (not a real directory): {remote_path}"
        )
    if is_host_local_bind(remote_path or ""):
        raise CopyError(
            f"refusing to rsync host socket/device {remote_path} "
            "(dest should bind the dest host path)"
        )
    if as_file:
        remote = (remote_path or "").rstrip("/")
        dest_path = Path(local_dir)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest = str(dest_path)
    else:
        remote = (remote_path or "").rstrip("/") + "/"
        local_dir.mkdir(parents=True, exist_ok=True)
        dest = str(local_dir).rstrip("/") + "/"
    priv = get_private_key_plain(server)
    if not priv:
        raise CopyError("No SSH private key on source")
    user = server.ssh_username or "pi"
    client = get_ssh_client(server)
    try:
        rsync_path = _remote_rsync_path(client, user)
    finally:
        try:
            client.close()
        except Exception:
            pass
    with temp_key_file(priv) as key_path:
        ssh_cmd = _ssh_rsync_cmd(key_path, server)
        cmd = [
            *_rsync_core_args(delete=delete),
            "-e",
            ssh_cmd,
            "--rsync-path",
            rsync_path,
            f"{user}@{server.hostname}:{remote}",
            dest,
        ]
        _log(log, f"rsync ← {server.name}:{remote}")
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "rsync failed")[:800]
            raise CopyError(f"pull {remote}: {err}")
        stats = (proc.stdout or "").strip()
        if stats:
            _log(log, stats.splitlines()[-1][:240])


def rsync_herder_to_host(
    server: Server,
    local_dir: Path,
    remote_path: str,
    *,
    log: Optional[LogFn] = None,
    delete: bool = False,
) -> None:
    """Push local_dir contents to remote_path."""
    local = str(local_dir).rstrip("/") + "/"
    if not Path(local_dir).is_dir():
        raise CopyError(f"staging missing: {local_dir}")
    remote = (remote_path or "").rstrip("/") + "/"
    priv = get_private_key_plain(server)
    if not priv:
        raise CopyError("No SSH private key on dest")
    user = server.ssh_username or "pi"
    client = get_ssh_client(server)
    try:
        rsync_path = _remote_rsync_path(client, user)
        q = shlex.quote(remote.rstrip("/"))
        run_command(client, f"mkdir -p {q}", timeout=30)
    finally:
        try:
            client.close()
        except Exception:
            pass
    with temp_key_file(priv) as key_path:
        ssh_cmd = _ssh_rsync_cmd(key_path, server)
        cmd = [
            *_rsync_core_args(delete=delete),
            "-e",
            ssh_cmd,
            "--rsync-path",
            rsync_path,
            local,
            f"{user}@{server.hostname}:{remote}",
        ]
        _log(log, f"rsync → {server.name}:{remote}" + (" (--delete)" if delete else ""))
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "rsync failed")[:800]
            raise CopyError(f"push {remote}: {err}")
        stats = (proc.stdout or "").strip()
        if stats:
            _log(log, stats.splitlines()[-1][:240])


def chown_remote_tree(
    server: Server,
    remote_path: str,
    *,
    owner_from: Optional[str] = None,
    log: Optional[LogFn] = None,
) -> None:
    """Make dest project files owned like dest docker root (not root / fleet SSH).

    Herder ``sudo rsync --numeric-ids`` lands ``root:root``. Dest SSH user is often
    ``piherder`` while ``/home/bjorn/docker`` is ``bjorn`` — use the docker-root
    owner, else ``/home/<user>``.
    Named Docker volume mountpoints are not passed here.
    """
    import os

    path = os.path.normpath((remote_path or "").strip()).rstrip("/")
    ref = os.path.normpath((owner_from or os.path.dirname(path) or "").strip()).rstrip("/")
    if not path or path == "/" or ".." in path.split("/"):
        raise CopyError(f"refusing chown of {remote_path}")
    if path in ("/home", "/var", "/opt", "/usr", "/etc", "/root", "/boot"):
        raise CopyError(f"refusing chown of {path}")
    if not ref or ref == "/" or ".." in ref.split("/"):
        ref = os.path.dirname(path)
    qpath = shlex.quote(path)
    qref = shlex.quote(ref)
    cmd = (
        f"ref={qref}; path={qpath}; "
        "og=$(stat -c '%U:%G' \"$ref\" 2>/dev/null || true); "
        "u=${og%%:*}; g=${og#*:}; "
        "if [ -z \"$u\" ] || [ \"$u\" = root ]; then "
        "home=$(echo \"$ref\" | awk -F/ '$2==\"home\" && NF>=3 {print \"/\" $2 \"/\" $3; exit}'); "
        "if [ -n \"$home\" ]; then og=$(stat -c '%U:%G' \"$home\" 2>/dev/null || true); "
        "u=${og%%:*}; g=${og#*:}; fi; fi; "
        "if [ -z \"$u\" ] || [ \"$u\" = root ]; then echo no_dest_owner >&2; exit 1; fi; "
        "if sudo -n true >/dev/null 2>&1; then sudo -n chown -R \"$u:$g\" \"$path\"; "
        "else chown -R \"$u:$g\" \"$path\"; fi; "
        "stat -c '%U:%G' \"$path\"; "
        "if [ -f \"$path/docker-compose.yml\" ]; then stat -c '%U:%G' \"$path/docker-compose.yml\"; fi"
    )
    client = get_ssh_client(server)
    try:
        st, out, err = run_command(client, cmd, timeout=60)
        who = (out or "").strip().splitlines()[-1] if (out or "").strip() else ""
        if st != 0:
            raise CopyError(
                f"chown {path} from {ref}: {(err or out or 'failed')[:300]}"
            )
        _log(log, f"Dest ownership {path} → {who or 'ok'} (from {ref})")
    finally:
        try:
            client.close()
        except Exception:
            pass


def _volume_mountpoint(server: Server, volume: str) -> str:
    client = get_ssh_client(server)
    try:
        st, out, err = run_command(
            client,
            "docker volume inspect "
            + shlex.quote(volume)
            + " --format '{{.Mountpoint}}'",
            timeout=30,
        )
        mp = (out or "").strip()
        if st != 0 or not mp or ".." in mp:
            raise CopyError(f"volume inspect {volume}: {(err or out or 'no mountpoint')[:300]}")
        return mp
    finally:
        try:
            client.close()
        except Exception:
            pass


def copy_named_volume(
    source: Server,
    dest: Server,
    volume: str,
    staging: Path,
    *,
    dest_volume: Optional[str] = None,
    log: Optional[LogFn] = None,
) -> None:
    """Copy a named Docker volume via herder staging (rsync volume Mountpoint)."""
    name = (volume or "").strip()
    if not name or "/" in name or ".." in name:
        raise CopyError(f"invalid volume name: {volume!r}")
    dest_name = (dest_volume or name).strip()
    if not dest_name or "/" in dest_name or ".." in dest_name:
        raise CopyError(f"invalid dest volume name: {dest_volume!r}")
    local = staging / "volumes" / name
    local.mkdir(parents=True, exist_ok=True)
    src_mp = _volume_mountpoint(source, name)
    _log(log, f"volume {name}: pull {src_mp}")
    rsync_host_to_herder(source, src_mp, local, log=log)
    dest_client = get_ssh_client(dest)
    try:
        st, out, err = run_command(
            dest_client, f"docker volume create {shlex.quote(dest_name)}", timeout=60
        )
        if st != 0:
            raise CopyError(
                f"volume create {dest_name}: {(err or out or 'failed')[:300]}"
            )
    finally:
        try:
            dest_client.close()
        except Exception:
            pass
    dest_mp = _volume_mountpoint(dest, dest_name)
    label = name if dest_name == name else f"{name} → {dest_name}"
    _log(log, f"volume {label}: push {dest_mp}")
    rsync_herder_to_host(dest, local, dest_mp, log=log)

