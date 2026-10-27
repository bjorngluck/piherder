"""Sidecar owner, group, and mode for a backed-up tree.

Cloud copies and a restore pull land files as the account that runs rclone.
The manifest records numeric ids from the source. Restore applies them on the
host and strips setuid and setgid. A tree with no manifest is left unchanged.

A new sidecar also records how many regular files the tree has and how many
bytes those files hold. Directories and symlinks are not part of that count.
"""
from __future__ import annotations

import json
import os
import shlex
import stat
from pathlib import Path
from typing import Any, Optional

MANIFEST_NAME = ".piherder-ownership.json"
SCHEMA = 1


def manifest_path(dest: Path) -> Path:
    return Path(dest) / MANIFEST_NAME


def canonical_mode(value: Any, *, strip_special: bool = False) -> Optional[str]:
    """Four-digit octal. strip_special keeps only the 0o1777 bits."""
    try:
        number = int(str(value), 8) if isinstance(value, str) else int(value)
    except (TypeError, ValueError):
        return None
    if number < 0 or number > 0o7777:
        return None
    if strip_special:
        number &= 0o1777
    return format(number, "04o")


def safe_rel(raw: Any) -> Optional[str]:
    """Relative path inside the restore target. Empty string is the target itself."""
    if raw is None:
        return None
    text = str(raw).replace("\\", "/")
    if text in ("", "."):
        return ""
    if text.startswith("/") or "\x00" in text or "\n" in text:
        return None
    parts = text.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None
    return "/".join(parts)


def _classify(st: os.stat_result) -> tuple[str, Optional[int]]:
    """Regular files carry their size. A symlink size is the target name, so it is omitted."""
    mode = st.st_mode
    if stat.S_ISLNK(mode):
        return "link", None
    if stat.S_ISREG(mode):
        return "file", int(st.st_size)
    if stat.S_ISDIR(mode):
        return "dir", None
    return "other", None


def _entry(path: Path, rel: str) -> dict[str, Any]:
    st = path.lstat()
    kind, size = _classify(st)
    row: dict[str, Any] = {
        "path": rel,
        "uid": int(st.st_uid),
        "gid": int(st.st_gid),
        "mode": format(stat.S_IMODE(st.st_mode), "04o"),
        "kind": kind,
    }
    if size is not None:
        row["bytes"] = size
    return row


def _nonneg_int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def content_counts(entries: list[dict[str, Any]]) -> Optional[dict[str, int]]:
    """Regular files only. None when the listing has no kind, or a file size is unusable."""
    saw_kind = False
    files = 0
    total = 0
    for raw in entries:
        if not isinstance(raw, dict) or "kind" not in raw:
            continue
        saw_kind = True
        if raw.get("kind") != "file":
            continue
        size = _nonneg_int(raw.get("bytes"))
        if size is None:
            return None
        files += 1
        total += size
    if not saw_kind:
        return None
    return {"file_count": files, "byte_count": total}


def manifest_document(
    entries: list[dict[str, Any]],
    *,
    file_count: Optional[int] = None,
    byte_count: Optional[int] = None,
) -> dict[str, Any]:
    """Sidecar body. Counts are stored only when this listing can prove them."""
    payload: dict[str, Any] = {"schema": SCHEMA, "entries": entries}
    if file_count is None and byte_count is None:
        counts = content_counts(entries)
        if counts is not None:
            payload.update(counts)
        return payload
    files = _nonneg_int(file_count)
    total = _nonneg_int(byte_count)
    if files is None or total is None:
        raise ValueError("file_count and byte_count must both be zero or more")
    payload["file_count"] = files
    payload["byte_count"] = total
    return payload


def collect_local_tree(root: Path | str) -> list[dict[str, Any]]:
    """uid, gid, and mode for a local tree. Symlinks are recorded, not followed."""
    base = Path(root)
    if not base.is_dir():
        raise FileNotFoundError(str(base))
    entries = [_entry(base, "")]
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        kept: list[str] = []
        for name in dirnames:
            path = Path(dirpath) / name
            rel = path.relative_to(base).as_posix()
            if "\n" in rel or "\x00" in rel:
                continue
            entries.append(_entry(path, rel))
            if not path.is_symlink():
                kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            path = Path(dirpath) / name
            rel = path.relative_to(base).as_posix()
            if "\n" in rel or "\x00" in rel:
                continue
            entries.append(_entry(path, rel))
    return entries


def parse_find_output(raw: bytes) -> list[dict[str, Any]]:
    """GNU find -printf '%P\\0%U\\0%G\\0%m\\0'."""
    parts = raw.split(b"\0")
    if parts and parts[-1] == b"":
        parts.pop()
    if len(parts) % 4 != 0:
        raise ValueError("truncated ownership listing")
    entries: list[dict[str, Any]] = []
    for index in range(0, len(parts), 4):
        rel = parts[index].decode("utf-8", "surrogateescape")
        cleaned = safe_rel(rel)
        if cleaned is None:
            continue
        mode = canonical_mode(parts[index + 3].decode("ascii", "replace"))
        if mode is None:
            continue
        uid = int(parts[index + 1])
        gid = int(parts[index + 2])
        if uid < 0 or gid < 0 or uid > 0xFFFFFFFF or gid > 0xFFFFFFFF:
            continue
        entries.append({"path": cleaned, "uid": uid, "gid": gid, "mode": mode})
    return entries


def collect_remote_tree(client: Any, source: str, *, timeout: int = 600) -> list[dict[str, Any]]:
    """List owners on the host. sudo find is preferred so root-owned files are visible."""
    quoted = shlex.quote(source)
    finder = (
        f"find {quoted} \\( -type d -o -type f -o -type l \\) "
        "-printf '%P\\0%U\\0%G\\0%m\\0'"
    )
    errors: list[str] = []
    for cmd in (f"sudo -n {finder}", finder):
        status, out, err = _exec_bytes(client, cmd, timeout=timeout)
        if status == 0:
            return parse_find_output(out)
        errors.append((err or out or b"").decode("utf-8", "replace")[:200])
    detail = "; ".join(piece for piece in errors if piece) or "find failed"
    raise RuntimeError(detail[:300])


def _exec_bytes(client: Any, cmd: str, *, timeout: int) -> tuple[int, bytes, bytes]:
    _stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read() or b""
    err = stderr.read() or b""
    if isinstance(out, str):
        out = out.encode("utf-8", "surrogateescape")
    if isinstance(err, str):
        err = err.encode("utf-8", "surrogateescape")
    return int(stdout.channel.recv_exit_status()), out, err


def remote_content_command(source: str) -> str:
    """GNU find sizes of regular files. Symlinks and directories are omitted."""
    quoted = shlex.quote(source)
    return f"find {quoted} -type f -printf '%s\\0'"


def parse_size_output(raw: bytes) -> tuple[int, int]:
    """One decimal size per regular file, NUL separated. An empty listing is zero."""
    parts = raw.split(b"\0")
    if parts and parts[-1] == b"":
        parts.pop()
    total = 0
    count = 0
    for part in parts:
        if not part:
            raise ValueError("truncated size listing")
        text = part.decode("ascii", "strict")
        if not text.isdigit():
            raise ValueError("truncated size listing")
        total += int(text)
        count += 1
    return count, total


def collect_remote_counts(client: Any, source: str, *, timeout: int = 600) -> tuple[int, int]:
    """File count and byte count on the host. sudo find is preferred."""
    finder = remote_content_command(source)
    errors: list[str] = []
    for cmd in (f"sudo -n {finder}", finder):
        status, out, err = _exec_bytes(client, cmd, timeout=timeout)
        if status == 0:
            return parse_size_output(out)
        errors.append((err or out or b"").decode("utf-8", "replace")[:200])
    detail = "; ".join(piece for piece in errors if piece) or "find failed"
    raise RuntimeError(detail[:300])


def write_manifest(
    dest: Path | str,
    entries: list[dict[str, Any]],
    *,
    file_count: Optional[int] = None,
    byte_count: Optional[int] = None,
) -> Path:
    path = manifest_path(Path(dest))
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = manifest_document(entries, file_count=file_count, byte_count=byte_count)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    return path


def load_manifest(dest: Path | str) -> Optional[dict[str, Any]]:
    """None when this archive has no sidecar. A broken sidecar returns an error."""
    path = manifest_path(Path(dest))
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"error": f"ownership manifest is unreadable: {exc}"[:200]}
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return {"error": "ownership manifest schema is not supported"}
    entries = data.get("entries")
    if not isinstance(entries, list):
        return {"error": "ownership manifest has no entries"}
    loaded: dict[str, Any] = {"entries": entries}
    if "file_count" in data or "byte_count" in data:
        files = _nonneg_int(data.get("file_count"))
        total = _nonneg_int(data.get("byte_count"))
        if files is None or total is None:
            return {"error": "ownership manifest counts are not usable"}
        loaded["file_count"] = files
        loaded["byte_count"] = total
    return loaded


def _within_target(root: Path, rel: str) -> Optional[Path]:
    """Lexical walk. An intermediate symlink is refused so it cannot escape the target."""
    current = root
    if rel == "":
        return current
    parts = rel.split("/")
    for index, part in enumerate(parts):
        nxt = current / part
        try:
            st = nxt.lstat()
        except OSError:
            return None
        last = index == len(parts) - 1
        if stat.S_ISLNK(st.st_mode):
            if not last:
                return None
            return nxt
        if not last and not stat.S_ISDIR(st.st_mode):
            return None
        current = nxt
    try:
        current.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    return current


def apply_manifest_tree(root: Path | str, entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply numeric owner, group, and mode. setuid and setgid are cleared."""
    base = Path(root)
    applied = 0
    skipped = 0
    errors: list[str] = []
    for raw in entries:
        if not isinstance(raw, dict):
            skipped += 1
            continue
        rel = safe_rel(raw.get("path"))
        mode = canonical_mode(raw.get("mode"), strip_special=True)
        try:
            uid = int(raw.get("uid"))
            gid = int(raw.get("gid"))
        except (TypeError, ValueError):
            skipped += 1
            continue
        if rel is None or mode is None or uid < 0 or gid < 0 or uid > 0xFFFFFFFF or gid > 0xFFFFFFFF:
            skipped += 1
            continue
        path = _within_target(base, rel)
        if path is None:
            skipped += 1
            continue
        try:
            os.lchown(path, uid, gid)
            if not path.is_symlink():
                os.chmod(path, int(mode, 8))
        except OSError as exc:
            errors.append(f"{rel or '.'}: {exc.strerror or exc}")
            continue
        applied += 1
    return {"applied": applied, "skipped": skipped, "errors": errors}


def render_apply_script(entries: list[dict[str, Any]], *, use_sudo: bool) -> str:
    """POSIX sh for the host. chown -h does not follow a final symlink."""
    if use_sudo:
        # Least-privilege sudoers names these paths. A plain [ -e ] runs as
        # the SSH user and skips a 0700 directory that user cannot search.
        chown_fn = (
            'chown_cmd() { sudo -n /usr/bin/chown "$@"; }\n'
            'chmod_cmd() { sudo -n /usr/bin/chmod "$@"; }\n'
            'path_exists() { sudo -n /usr/bin/test -e "$1" || sudo -n /usr/bin/test -h "$1"; }\n'
            'path_link() { sudo -n /usr/bin/test -h "$1"; }\n'
            'path_dir() { sudo -n /usr/bin/test -d "$1"; }\n'
            "sudo -n /usr/bin/true\n"
        )
    else:
        chown_fn = (
            'chown_cmd() { chown "$@"; }\n'
            'chmod_cmd() { chmod "$@"; }\n'
            'path_exists() { [ -e "$1" ] || [ -L "$1" ]; }\n'
            'path_link() { [ -L "$1" ]; }\n'
            'path_dir() { [ -d "$1" ]; }\n'
        )
    calls: list[str] = []
    for raw in entries:
        if not isinstance(raw, dict):
            continue
        rel = safe_rel(raw.get("path"))
        mode = canonical_mode(raw.get("mode"), strip_special=True)
        try:
            uid = int(raw.get("uid"))
            gid = int(raw.get("gid"))
        except (TypeError, ValueError):
            continue
        if rel is None or mode is None or uid < 0 or gid < 0 or uid > 0xFFFFFFFF or gid > 0xFFFFFFFF:
            continue
        shown = "." if rel == "" else rel
        calls.append(
            "apply_rel "
            + " ".join(shlex.quote(part) for part in (shown, str(uid), str(gid), mode))
        )
    body = r"""#!/bin/sh
set -eu
root=$1
case $root in
  /*) ;;
  *) echo "restore target must be absolute" >&2; exit 2 ;;
esac
cd "$root"
__CHOWN_FN__
apply_rel() {
  rel=$1
  uid=$2
  gid=$3
  mode=$4
  case $uid in ''|*[!0-9]*) return 0 ;; esac
  case $gid in ''|*[!0-9]*) return 0 ;; esac
  case $mode in
    [01][0-7][0-7][0-7]) ;;
    *) return 0 ;;
  esac
  if [ "$rel" = "." ]; then
    chown_cmd -h "$uid:$gid" .
    chmod_cmd "$mode" .
    applied=$((applied + 1))
    return 0
  fi
  case $rel in
    /*|..|../*|*/..|*/../*) return 0 ;;
  esac
  rest=$rel
  dir=.
  while :; do
    comp=${rest%%/*}
    case $comp in ''|.|..) return 0 ;; esac
    path=$dir/$comp
    if [ "$rest" = "$comp" ]; then
      if ! path_exists "$path"; then
        return 0
      fi
      chown_cmd -h "$uid:$gid" "$path"
      if ! path_link "$path"; then
        chmod_cmd "$mode" "$path"
      fi
      applied=$((applied + 1))
      return 0
    fi
    if path_link "$path" || ! path_dir "$path"; then
      return 0
    fi
    dir=$path
    rest=${rest#*/}
  done
}
applied=0
__CALLS__
echo "applied $applied"
"""
    return body.replace("__CHOWN_FN__", chown_fn).replace(
        "__CALLS__", "\n".join(calls) if calls else ":"
    )
