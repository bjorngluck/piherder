"""Server backup restore wizard. Reverse rsync from the local tree to the host.

A chosen copy destination is pulled onto this PiHerder first. Dry-run does not
write that pull. The host write still needs the confirm checkbox.

Safety:
- Default dry_run=True
- Path policy checked on remote target
- Requires a local tree, or a selected copy that has that folder
"""
from __future__ import annotations

import json
import logging
import shlex
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import settings
from ..models import Server
from . import backup_profiles
from .backup_path_policy import validate_backup_path, parse_rules
from .ssh import get_private_key_plain, temp_key_file, LEGACY_SSH_OPTS_STR

logger = logging.getLogger(__name__)


_CHANGE_LIMIT = 200


def parse_rsync_changes(text: str, *, limit: int = _CHANGE_LIMIT) -> dict[str, Any]:
    """Itemized rsync lines only. Unchanged files are omitted."""
    changes: list[dict[str, str]] = []
    total = 0
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        action = ""
        path = ""
        if line.startswith("*deleting"):
            parts = line.split(None, 1)
            path = parts[1].strip() if len(parts) > 1 else ""
            action = "delete"
        elif len(line) > 12 and line[11] == " ":
            flags = line[:11]
            path = line[12:].strip()
            if not flags or flags[0] not in "<>ch" or flags[0] == ".":
                continue
            action = "create" if "+" in flags else "update"
        else:
            continue
        if not path or path in (".", "./") or "\n" in path or "\x00" in path:
            continue
        if path.endswith("/"):
            path = path.rstrip("/")
        if not path:
            continue
        total += 1
        if len(changes) < limit:
            changes.append({"path": path, "action": action})
    return {
        "changes": changes,
        "change_count": total,
        "truncated": total > limit,
    }


def ownership_remote_command(source: str) -> str:
    """One remote argv. OpenSSH joins extra arguments with spaces."""
    return "sh -s -- " + shlex.quote(source)


def list_restore_candidates(server: Server) -> List[Dict[str, Any]]:
    """List backup destinations that have local data and can be restored.

    Avoids recursive ``du`` on the Backups page (was multi-second on large trees).
    Top-level entry count is enough to know a dest has content.
    """
    profiles = backup_profiles.get_backup_profiles(server, skip_fs=False)
    out: List[Dict[str, Any]] = []
    for p in profiles:
        dest = Path(p.get("destination") or "")
        exists = dest.is_dir()
        size = 0
        file_count_hint = 0
        if exists:
            try:
                # cheap: top-level only (no recursive du — page-load latency)
                file_count_hint = sum(1 for _ in dest.iterdir())
            except Exception:
                file_count_hint = 0
        out.append({
            "source": p.get("source"),
            "dest_name": p.get("dest_name"),
            "destination": str(dest),
            "enabled": p.get("enabled", True),
            "exists": exists and file_count_hint > 0,
            "size_bytes": size,
            "entry_count": file_count_hint,
            "last_backup_str": p.get("last_backup_str") or "—",
            "rollback": snapshot_matches(dest, p.get("source") or ""),
        })
    return out


def snapshot_root_for(dest: Path) -> Path:
    """Local folder that holds the pre-restore copy of this source."""
    return Path(dest).parent / "_rollback" / Path(dest).name


def _snapshot_paths(dest: Path) -> tuple[Path, Path, Path, Path]:
    root = snapshot_root_for(dest)
    return root, root / "tree", root / "tree.next", root / "meta.json"


def snapshot_matches(dest: Path, source: str) -> bool:
    _root, tree, _staging, meta = _snapshot_paths(dest)
    if not tree.is_dir() or not meta.is_file():
        return False
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(data, dict) and (data.get("source") or "") == (source or "").strip()


def source_copy_rel(server: Server, source: str) -> str:
    """Backup-drive path the copy uses for this source. host-folder/dest-name."""
    from .backup_replicate import host_folder_name

    name = Path(source).name or "root"
    folder = host_folder_name(server)
    return f"{folder}/{name}" if folder else name


def _ssh_argv(key_path: str, port: int, user: str, host: str, remote_cmd: str) -> list[str]:
    return [
        "ssh",
        "-i",
        key_path,
        "-p",
        str(port),
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "ConnectTimeout=15",
        f"{user}@{host}",
        remote_cmd,
    ]


def _count_phrase(files: int, total: int) -> str:
    file_word = "file" if files == 1 else "files"
    byte_word = "byte" if total == 1 else "bytes"
    return f"{files} {file_word}, {total} {byte_word}"


def _capture_owners(key_path: str, port: int, user: str, host: str, source: str) -> list[dict[str, Any]]:
    from .backup_ownership import parse_find_output

    quoted = shlex.quote(source)
    finder = (
        f"find {quoted} \\( -type d -o -type f -o -type l \\) "
        "-printf '%P\\0%U\\0%G\\0%m\\0'"
    )
    errors: list[str] = []
    for cmd in (f"sudo -n {finder}", finder):
        proc = subprocess.run(
            _ssh_argv(key_path, port, user, host, cmd),
            capture_output=True,
            timeout=600,
        )
        raw = proc.stdout or b""
        if isinstance(raw, str):
            raw = raw.encode("utf-8", "surrogateescape")
        if proc.returncode == 0:
            return parse_find_output(raw)
        err = proc.stderr or b""
        if isinstance(err, str):
            err = err.encode("utf-8", "surrogateescape")
        errors.append(err.decode("utf-8", "replace")[:200])
    detail = "; ".join(piece for piece in errors if piece) or "find failed"
    raise RuntimeError(detail[:300])


def _tally_host(key_path: str, port: int, user: str, host: str, source: str) -> tuple[int, int]:
    """Regular-file count and byte count on the host after a restore."""
    from .backup_ownership import parse_size_output, remote_content_command

    finder = remote_content_command(source)
    errors: list[str] = []
    for cmd in (f"sudo -n {finder}", finder):
        proc = subprocess.run(
            _ssh_argv(key_path, port, user, host, cmd),
            capture_output=True,
            timeout=600,
        )
        raw = proc.stdout or b""
        if isinstance(raw, str):
            raw = raw.encode("utf-8", "surrogateescape")
        if proc.returncode == 0:
            return parse_size_output(raw)
        err = proc.stderr or b""
        if isinstance(err, str):
            err = err.encode("utf-8", "surrogateescape")
        errors.append(err.decode("utf-8", "replace")[:200])
    detail = "; ".join(piece for piece in errors if piece) or "find failed"
    raise RuntimeError(detail[:300])


def _verify_manifest_counts(
    loaded: dict[str, Any],
    *,
    key_path: str,
    port: int,
    user: str,
    host: str,
    source: str,
) -> dict[str, Any]:
    """Compare the host to the sidecar. No counts means an older archive."""
    if "file_count" not in loaded or "byte_count" not in loaded:
        return {"skipped": True, "reason": "no counts"}
    expected_files = int(loaded["file_count"])
    expected_bytes = int(loaded["byte_count"])
    try:
        got_files, got_bytes = _tally_host(key_path, port, user, host, source)
    except (RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": f"Manifest check failed: {exc}"[:300]}
    mismatch = (
        "host has "
        + _count_phrase(got_files, got_bytes)
        + "; manifest has "
        + _count_phrase(expected_files, expected_bytes)
    )
    if got_files > expected_files and got_bytes >= expected_bytes:
        return {
            "ok": True,
            "warning": mismatch,
            "file_count": got_files,
            "byte_count": got_bytes,
            "expected_file_count": expected_files,
            "expected_byte_count": expected_bytes,
        }
    if got_files != expected_files or got_bytes != expected_bytes:
        return {
            "ok": False,
            "error": mismatch,
            "file_count": got_files,
            "byte_count": got_bytes,
            "expected_file_count": expected_files,
            "expected_byte_count": expected_bytes,
        }
    return {"ok": True, "file_count": got_files, "byte_count": got_bytes}


def _rsync_tree(
    *,
    ssh_cmd: str,
    rsync_path: str,
    src: str,
    dst: str,
    dry_run: bool = False,
) -> subprocess.CompletedProcess:
    from .backup_ownership import MANIFEST_NAME

    cmd = [
        "rsync",
        "-a",
        "--delete",
        "--info=stats2",
        "--exclude",
        MANIFEST_NAME,
        "-e",
        ssh_cmd,
        "--rsync-path",
        rsync_path,
    ]
    if dry_run:
        cmd.append("--dry-run")
    cmd.extend([src, dst])
    return subprocess.run(cmd, capture_output=True, text=True, timeout=3600)


def _tree_bytes(path: Path) -> int:
    total = 0
    if not path.is_dir():
        return 0
    for item in path.rglob("*"):
        if not item.is_file():
            continue
        try:
            total += item.stat().st_size
        except OSError:
            continue
    return total


def _snapshot_space_error(dest_path: Path) -> str | None:
    """None when this machine can hold another copy of the local tree."""
    try:
        anchor = dest_path if dest_path.exists() else dest_path.parent
        free = shutil.disk_usage(anchor).free
        need = _tree_bytes(dest_path)
    except OSError as exc:
        return f"Free space check failed: {exc}"[:300]
    margin = max(1024 * 1024, need // 10)
    if free < need + margin:
        return (
            "Not enough free space for the pre-restore snapshot "
            f"({free} bytes free, about {need} bytes needed)"
        )
    return None


def _take_rollback_snapshot(
    dest_path: Path,
    *,
    source: str,
    remote: str,
    ssh_cmd: str,
    rsync_path: str,
    key_path: str,
    port: int,
    user: str,
    host: str,
) -> dict[str, Any]:
    """Copy the live host folder aside. The previous snapshot stays if this fails."""
    from .backup_ownership import write_manifest

    if snapshot_matches(dest_path, source):
        return {"saved": True, "kept": True}
    root, tree, staging, meta_path = _snapshot_paths(dest_path)
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        proc = _rsync_tree(
            ssh_cmd=ssh_cmd,
            rsync_path=rsync_path,
            src=remote,
            dst=str(staging).rstrip("/") + "/",
        )
        out = ((proc.stdout or "") + (proc.stderr or ""))[-2000:]
        if proc.returncode != 0:
            return {
                "error": "Rollback snapshot failed",
                "rc": proc.returncode or 1,
                "output": out,
            }
        try:
            entries = _capture_owners(key_path, port, user, host, source)
        except (RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
            return {"error": f"Rollback snapshot failed: {exc}"[:300], "rc": 1, "output": out}
        write_manifest(staging, entries)
        if tree.exists():
            shutil.rmtree(tree)
        staging.rename(tree)
        meta_path.write_text(
            json.dumps(
                {"source": source, "taken_at": datetime.utcnow().isoformat() + "Z"},
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        return {"saved": True}
    except subprocess.TimeoutExpired:
        return {"error": "Rollback snapshot timed out", "rc": 124}
    except Exception as exc:
        return {"error": f"Rollback snapshot failed: {exc}"[:300], "rc": 1}
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def restore_backup_source(
    server: Server,
    source: str,
    *,
    dry_run: bool = True,
    pull_from=None,
    itemize: bool = False,
) -> Dict[str, Any]:
    """
    Reverse rsync: local destination → remote source path.

    Returns dict with rc, dry_run, source, destination, output, error.
    """
    # A preview is a read. Itemize never turns into a host write.
    if itemize:
        dry_run = True
    source = (source or "").strip()
    if not source:
        return {"error": "source path required", "rc": 1, "dry_run": dry_run}

    rules = parse_rules(getattr(server, "backup_path_rules", None))
    ok, reason = validate_backup_path(source, rules)
    if not ok:
        return {
            "error": f"Path policy blocked restore target: {reason}",
            "rc": 1,
            "dry_run": dry_run,
            "source": source,
        }

    # Find matching profile / destination
    dest_path: Optional[Path] = None
    for p in backup_profiles.get_backup_profiles_db(server):
        if (p.get("source") or "").rstrip("/") == source.rstrip("/"):
            dest_path = Path(p["destination"])
            break
    if dest_path is None:
        # fallback: compute dest
        dest_name = Path(source).name or "root"
        dest_path = backup_profiles.get_backup_root_for_server(server) / dest_name

    pulled = None
    if pull_from is not None:
        from . import backup_replicate

        rel = source_copy_rel(server, source)
        pulled = backup_replicate.pull_rel(
            pull_from, rel, dry_run=dry_run, local_dir=dest_path
        )
        if not pulled.get("ok"):
            return {
                "error": pulled.get("error") or "Could not read that copy",
                "rc": 1,
                "dry_run": dry_run,
                "source": source,
                "destination": str(dest_path),
                "auth_failed": bool(pulled.get("auth_failed")),
                "destination_id": getattr(pull_from, "id", None),
            }
        if dry_run and not dest_path.is_dir():
            place = pulled.get("place") or "the copy"
            early: Dict[str, Any] = {
                "rc": 0,
                "dry_run": True,
                "source": source,
                "destination": str(dest_path),
                "place": place,
                "error": None,
                "summary": f"Dry-run pull ok · {source} · {place}",
            }
            if itemize:
                early.update(parse_rsync_changes(""))
                early["wrote"] = False
                early["note"] = (
                    "No local copy to compare. The remote folder was checked and not written here."
                )
            return early

    if not dest_path.is_dir():
        return {
            "error": f"No local backup at {dest_path}",
            "rc": 1,
            "dry_run": dry_run,
            "source": source,
            "destination": str(dest_path),
        }

    try:
        key_plain = get_private_key_plain(server)
    except Exception as e:
        return {
            "error": f"SSH key: {e}",
            "rc": 1,
            "dry_run": dry_run,
            "source": source,
        }
    if not key_plain:
        return {
            "error": "No SSH private key configured for this server",
            "rc": 1,
            "dry_run": dry_run,
            "source": source,
        }

    host = server.hostname
    port = int(server.ssh_port or 22)
    user = server.ssh_username or "root"
    remote = f"{user}@{host}:{source.rstrip('/')}/"
    local = str(dest_path).rstrip("/") + "/"

    # Prefer sudo rsync on remote (same as backup path) for non-root
    rsync_path = "rsync"
    if user not in ("root",) and "haos" not in (server.os_type or "").lower():
        rsync_path = "sudo -n rsync"

    with temp_key_file(key_plain) as key_path:
        ssh_cmd = f"ssh -i {shlex.quote(key_path)} -p {port} {LEGACY_SSH_OPTS_STR}"
        from .backup_ownership import MANIFEST_NAME, load_manifest, render_apply_script

        rollback = {"skipped": True, "reason": "dry-run"} if dry_run else None
        if not dry_run and snapshot_matches(dest_path, source):
            rollback = {"saved": True, "kept": True}
        elif not dry_run:
            space = _snapshot_space_error(dest_path)
            if space:
                return {
                    "error": space,
                    "rc": 1,
                    "dry_run": False,
                    "source": source,
                    "destination": str(dest_path),
                    "rollback": {"saved": False},
                }
            snap = _take_rollback_snapshot(
                dest_path,
                source=source,
                remote=remote,
                ssh_cmd=ssh_cmd,
                rsync_path=rsync_path,
                key_path=key_path,
                port=port,
                user=user,
                host=host,
            )
            if snap.get("error"):
                return {
                    "error": snap["error"],
                    "rc": snap.get("rc") or 1,
                    "dry_run": False,
                    "source": source,
                    "destination": str(dest_path),
                    "output": snap.get("output") or "",
                    "rollback": {"saved": False},
                }
            rollback = {"saved": True}

        cmd = [
            "rsync",
            "-a",
            "--info=stats2",
            "--exclude",
            MANIFEST_NAME,
            "-e",
            ssh_cmd,
            "--rsync-path",
            rsync_path,
        ]
        if itemize:
            cmd.append("--itemize-changes")
        if dry_run:
            cmd.append("--dry-run")
        cmd.extend([local, remote])

        logger.info(
            "[restore] %s → %s (dry_run=%s)", local, remote, dry_run
        )
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120 if dry_run else 3600,
            )
            out = (proc.stdout or "") + (proc.stderr or "")
            place = (pulled or {}).get("place")
            summary = (
                f"{'Dry-run restore' if dry_run else 'Restore'} "
                f"{'ok' if proc.returncode == 0 else 'failed'} · {source}"
            )
            if place:
                summary = f"{summary} · {place}"
            result = {
                "rc": proc.returncode,
                "dry_run": dry_run,
                "source": source,
                "destination": str(dest_path),
                "remote": remote,
                "place": place,
                "output": out[-4000:],
                "error": None if proc.returncode == 0 else f"rsync exit {proc.returncode}",
                "summary": summary,
                "destination_id": getattr(pull_from, "id", None) if pull_from is not None else None,
                "rollback": rollback,
            }
            if itemize:
                result.update(parse_rsync_changes(out))
                result["wrote"] = False
                result["ok"] = proc.returncode == 0
            if proc.returncode != 0 or itemize:
                return result
            loaded = load_manifest(dest_path)
            if loaded is None:
                result["ownership"] = {"skipped": True, "reason": "no manifest"}
                return result
            if loaded.get("error"):
                result["rc"] = 1
                result["error"] = loaded["error"]
                result["summary"] = f"Restore failed · {source}"
                return result
            if dry_run:
                result["ownership"] = {
                    "skipped": True,
                    "reason": "dry-run",
                    "entries": len(loaded["entries"]),
                }
                if "file_count" in loaded and "byte_count" in loaded:
                    result["manifest"] = {"skipped": True, "reason": "dry-run"}
                return result
            script = render_apply_script(
                loaded["entries"],
                use_sudo=user not in ("root",),
            )
            own = subprocess.run(
                [
                    "ssh",
                    "-i",
                    key_path,
                    "-p",
                    str(port),
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "StrictHostKeyChecking=accept-new",
                    "-o",
                    "ConnectTimeout=15",
                    f"{user}@{host}",
                    ownership_remote_command(source),
                ],
                input=script,
                capture_output=True,
                text=True,
                timeout=3600,
            )
            own_out = ((own.stdout or "") + (own.stderr or ""))[-2000:]
            if own.returncode != 0:
                result["rc"] = own.returncode or 1
                result["error"] = f"ownership restore failed: {own_out[:300] or 'ssh exit ' + str(own.returncode)}"
                result["summary"] = f"Restore failed · {source}"
                result["ownership"] = {"rc": own.returncode, "output": own_out}
                return result
            result["ownership"] = {"applied": True, "output": own_out.strip()[:300]}
            checked = _verify_manifest_counts(
                loaded,
                key_path=key_path,
                port=port,
                user=user,
                host=host,
                source=source,
            )
            if checked.get("error"):
                result["rc"] = 1
                result["error"] = checked["error"]
                result["summary"] = f"Restore failed · {source} · {checked['error']}"
                if place:
                    result["summary"] = f"{result['summary']} · {place}"
                result["manifest"] = {key: value for key, value in checked.items() if key != "error"}
                return result
            result["manifest"] = checked
            return result
        except subprocess.TimeoutExpired:
            return {
                "error": "Restore timed out",
                "rc": 124,
                "dry_run": dry_run,
                "source": source,
                "destination": str(dest_path),
            }
        except Exception as e:
            return {
                "error": str(e)[:300],
                "rc": 1,
                "dry_run": dry_run,
                "source": source,
                "destination": str(dest_path),
            }


def preview_restore(server: Server, source: str, *, pull_from=None) -> Dict[str, Any]:
    """Dry-run itemized restore. Does not pull files here and does not write to the host."""
    result = restore_backup_source(
        server,
        source,
        dry_run=True,
        pull_from=pull_from,
        itemize=True,
    )
    result["wrote"] = False
    result["dry_run"] = True
    result.pop("remote", None)
    result.pop("output", None)
    result.setdefault("changes", [])
    result.setdefault("change_count", len(result["changes"]))
    result.setdefault("truncated", False)
    error = result.get("error") or ""
    if error.lower().startswith("no local backup"):
        result["error"] = "No local backup for this source"
    return result


def undo_host_restore(server: Server, source: str) -> Dict[str, Any]:
    """Write the pre-restore snapshot back onto the host. Does not take another one."""
    source = (source or "").strip()
    if not source:
        return {"error": "source path required", "rc": 1, "dry_run": False}

    rules = parse_rules(getattr(server, "backup_path_rules", None))
    ok, reason = validate_backup_path(source, rules)
    if not ok:
        return {
            "error": f"Path policy blocked restore target: {reason}",
            "rc": 1,
            "dry_run": False,
            "source": source,
        }

    dest_path: Optional[Path] = None
    for p in backup_profiles.get_backup_profiles_db(server):
        if (p.get("source") or "").rstrip("/") == source.rstrip("/"):
            dest_path = Path(p["destination"])
            break
    if dest_path is None:
        dest_name = Path(source).name or "root"
        dest_path = backup_profiles.get_backup_root_for_server(server) / dest_name

    if not snapshot_matches(dest_path, source):
        return {
            "error": "No rollback snapshot for this path",
            "rc": 1,
            "dry_run": False,
            "source": source,
            "destination": str(dest_path),
        }

    try:
        key_plain = get_private_key_plain(server)
    except Exception as e:
        return {"error": f"SSH key: {e}", "rc": 1, "dry_run": False, "source": source}
    if not key_plain:
        return {
            "error": "No SSH private key configured for this server",
            "rc": 1,
            "dry_run": False,
            "source": source,
        }

    host = server.hostname
    port = int(server.ssh_port or 22)
    user = server.ssh_username or "root"
    remote = f"{user}@{host}:{source.rstrip('/')}/"
    _root, tree, _staging, _meta = _snapshot_paths(dest_path)
    local = str(tree).rstrip("/") + "/"
    rsync_path = "rsync"
    if user not in ("root",) and "haos" not in (server.os_type or "").lower():
        rsync_path = "sudo -n rsync"

    from .backup_ownership import load_manifest, render_apply_script

    with temp_key_file(key_plain) as key_path:
        ssh_cmd = f"ssh -i {shlex.quote(key_path)} -p {port} {LEGACY_SSH_OPTS_STR}"
        try:
            proc = _rsync_tree(
                ssh_cmd=ssh_cmd,
                rsync_path=rsync_path,
                src=local,
                dst=remote,
            )
        except subprocess.TimeoutExpired:
            return {
                "error": "Undo timed out",
                "rc": 124,
                "dry_run": False,
                "source": source,
            }
        out = ((proc.stdout or "") + (proc.stderr or ""))[-4000:]
        if proc.returncode != 0:
            return {
                "error": f"rsync exit {proc.returncode}",
                "rc": proc.returncode or 1,
                "dry_run": False,
                "source": source,
                "destination": str(dest_path),
                "output": out,
                "summary": f"Undo failed · {source}",
            }
        loaded = load_manifest(tree)
        if loaded is None or loaded.get("error"):
            return {
                "error": (loaded or {}).get("error") or "Rollback snapshot has no ownership manifest",
                "rc": 1,
                "dry_run": False,
                "source": source,
                "output": out,
                "summary": f"Undo failed · {source}",
            }
        script = render_apply_script(loaded["entries"], use_sudo=user not in ("root",))
        own = subprocess.run(
            _ssh_argv(key_path, port, user, host, ownership_remote_command(source)),
            input=script,
            capture_output=True,
            text=True,
            timeout=3600,
        )
        own_out = ((own.stdout or "") + (own.stderr or ""))[-2000:]
        if own.returncode != 0:
            return {
                "error": f"ownership restore failed: {own_out[:300] or 'ssh exit ' + str(own.returncode)}",
                "rc": own.returncode or 1,
                "dry_run": False,
                "source": source,
                "output": out,
                "summary": f"Undo failed · {source}",
            }
        return {
            "rc": 0,
            "dry_run": False,
            "source": source,
            "destination": str(dest_path),
            "output": out,
            "error": None,
            "summary": f"Undo ok · {source}",
            "rollback": {"restored": True},
        }


def restore_places(session, server: Server) -> List[Dict[str, Any]]:
    """Enabled copies this restore can read. A direct host only sees its ticked ones."""
    from sqlmodel import select

    from ..models import BackupDestination
    from .backup_direct import parse_direct_targets
    from .backup_replicate import credentials_saved, destination_place

    rows = list(session.exec(select(BackupDestination).where(BackupDestination.enabled == True)).all())  # noqa: E712
    if getattr(server, "backup_direct", False):
        allowed = set(parse_direct_targets(getattr(server, "backup_direct_targets", None)))
        rows = [row for row in rows if (row.provider or "") in allowed]
    out: List[Dict[str, Any]] = []
    for row in rows:
        if not credentials_saved(row):
            continue
        out.append({"id": row.id, "place": destination_place(row)})
    return out
