"""Confined host Files (v1.3 Stream F): jailed SFTP list/get/put/mkdir/delete/rename.

Kill switch: PIHERDER_HOST_FILES (default off). Demo never opens real SFTP.
Fleet identity is the default; privileged is break-glass (UI + 2FA only).
"""
from __future__ import annotations

import hashlib
import posixpath
import stat
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, BinaryIO, Iterator, Optional

from ..config import settings
from .ssh import expand_remote_path, get_ssh_client

ROLE_FLEET = "fleet"
ROLE_PRIVILEGED = "privileged"
ROLES = (ROLE_FLEET, ROLE_PRIVILEGED)

LIST_CAP = 500
CHUNK = 64 * 1024
MAX_UPLOAD_DEFAULT = 512 * 1024 * 1024
MAX_UPLOAD_CEILING = 2 * 1024 * 1024 * 1024
NAME_MAX = 255

# Privileged jail is / ; these are never useful as files and stay denied.
VIRTUAL_DENY = ("/proc", "/sys", "/dev", "/run")

# Fleet jail must not sit under these OS trees (except /root as a home).
FLEET_JAIL_FORBIDDEN = (
    "/boot",
    "/dev",
    "/proc",
    "/sys",
    "/run",
    "/etc",
    "/usr",
    "/bin",
    "/sbin",
    "/lib",
    "/lib64",
    "/tmp",
    "/var/run",
)

SECRET_EXACT = frozenset(
    {".env", "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa", "id_rsa.pub"}
)
SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".ppk")


class FilesError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def files_enabled() -> bool:
    if bool(getattr(settings, "PIHERDER_DEMO_MODE", False)):
        return False
    return bool(getattr(settings, "PIHERDER_HOST_FILES", False))


def max_upload_bytes() -> int:
    raw = getattr(settings, "PIHERDER_HOST_FILES_MAX_BYTES", None)
    try:
        n = int(raw) if raw not in (None, "") else MAX_UPLOAD_DEFAULT
    except (TypeError, ValueError):
        n = MAX_UPLOAD_DEFAULT
    if n <= 0:
        n = MAX_UPLOAD_DEFAULT
    return min(n, MAX_UPLOAD_CEILING)


def normalize_role(role: Optional[str]) -> str:
    r = (role or ROLE_FLEET).strip().lower()
    if r not in ROLES:
        raise FilesError("invalid", "Identity must be fleet or privileged")
    return r


def identity_username(server: Any, identity: Any = None) -> str:
    if identity is not None:
        u = (getattr(identity, "username", None) or "").strip()
        if u:
            return u
    return (getattr(server, "ssh_username", None) or "").strip() or "root"


def files_supported(server: Any) -> bool:
    user = (getattr(server, "ssh_username", None) or "").strip()
    has_key = bool(getattr(server, "ssh_private_key_encrypted", None))
    has_pw = bool(getattr(server, "ssh_password_encrypted", None))
    return bool(user and (has_key or has_pw))


def posix_norm(path: str) -> str:
    p = (path or "").replace("\\", "/")
    if "\x00" in p:
        raise FilesError("escape", "Invalid path")
    abs_ = p.startswith("/")
    parts: list[str] = []
    for seg in p.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if parts:
                parts.pop()
            continue
        parts.append(seg)
    if abs_:
        return "/" + "/".join(parts) if parts else "/"
    return "/".join(parts)


def parse_rel(rel: Optional[str]) -> list[str]:
    raw = (rel or "").strip()
    if not raw or raw in (".", "./"):
        return []
    if "\x00" in raw or "\\" in raw:
        raise FilesError("escape", "Invalid path")
    if raw.startswith("/"):
        raise FilesError("escape", "Path must be relative to the jail")
    parts: list[str] = []
    for seg in raw.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            raise FilesError("escape", "Path must not contain ..")
        if len(seg) > NAME_MAX:
            raise FilesError("escape", "Name is too long")
        parts.append(seg)
    return parts


def join_jail(jail: str, rel: Optional[str]) -> str:
    parts = parse_rel(rel)
    if not parts:
        return jail
    if jail == "/":
        return posix_norm("/" + "/".join(parts))
    return posix_norm(jail.rstrip("/") + "/" + "/".join(parts))


def under_jail(path: str, jail: str) -> bool:
    p = posix_norm(path)
    j = posix_norm(jail)
    if j == "/":
        return p.startswith("/")
    return p == j or p.startswith(j + "/")


def _prefix_hit(path: str, prefix: str) -> bool:
    p = posix_norm(path)
    d = posix_norm(prefix)
    if not p or not d:
        return False
    if d == "/":
        return p == "/"
    return p == d or p.startswith(d + "/")


def sanitize_basename(name: Optional[str]) -> str:
    raw = (name or "").replace("\\", "/").strip()
    if not raw or "\x00" in raw:
        raise FilesError("escape", "Invalid file name")
    base = raw.rsplit("/", 1)[-1].strip()
    if not base or base in (".", "..") or len(base) > NAME_MAX:
        raise FilesError("escape", "Invalid file name")
    return base


def is_secretish(name: str) -> bool:
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    low = base.lower()
    if low in SECRET_EXACT or low.startswith(".env."):
        return True
    return any(low.endswith(suf) for suf in SECRET_SUFFIXES)


def jail_path(server: Any, *, role: str = ROLE_FLEET, identity: Any = None) -> str:
    role = normalize_role(role)
    user = identity_username(server, identity if role == ROLE_PRIVILEGED else None)
    if role == ROLE_PRIVILEGED:
        return "/"
    if getattr(server, "container_patch_enabled", False):
        raw = getattr(server, "docker_base_dir", None) or "~/docker"
        jail = posix_norm(expand_remote_path(raw, user))
    else:
        jail = posix_norm(expand_remote_path("~", user))
    if not jail or jail == "/":
        raise FilesError("jail", "Fleet jail cannot be filesystem root")
    if not jail.startswith("/"):
        raise FilesError("jail", "Jail must be an absolute path")
    for d in FLEET_JAIL_FORBIDDEN:
        if _prefix_hit(jail, d):
            raise FilesError("jail", f"Fleet jail cannot sit under {d}")
    return jail


def extra_denies(server: Any, *, role: str, identity: Any = None) -> tuple[str, ...]:
    role = normalize_role(role)
    if role == ROLE_PRIVILEGED:
        return VIRTUAL_DENY
    jail = jail_path(server, role=role, identity=identity)
    user = identity_username(server, None)
    ssh_homes = (
        posix_norm(jail.rstrip("/") + "/.ssh"),
        posix_norm(expand_remote_path("~/.ssh", user)),
        "/root/.ssh",
    )
    out: list[str] = list(VIRTUAL_DENY)
    for d in FLEET_JAIL_FORBIDDEN:
        if jail == d or jail.startswith(d + "/"):
            continue
        out.append(d)
    for s in ssh_homes:
        if s and s not in out:
            out.append(s)
    return tuple(out)


def is_denied(path: str, server: Any, *, role: str, identity: Any = None) -> bool:
    for d in extra_denies(server, role=role, identity=identity):
        if _prefix_hit(path, d):
            return True
    return False


def resolve_logical(
    server: Any,
    rel: Optional[str],
    *,
    role: str = ROLE_FLEET,
    identity: Any = None,
) -> tuple[str, str]:
    """Return (jail, absolute path) without talking to the host."""
    role = normalize_role(role)
    jail = jail_path(server, role=role, identity=identity)
    abs_path = join_jail(jail, rel)
    if not under_jail(abs_path, jail):
        raise FilesError("escape", "Path is outside the jail")
    if is_denied(abs_path, server, role=role, identity=identity):
        raise FilesError("denied", "Path is blocked")
    return jail, abs_path


def rel_of(abs_path: str, jail: str) -> str:
    a = posix_norm(abs_path)
    j = posix_norm(jail)
    if a == j:
        return ""
    if j == "/":
        return a.lstrip("/")
    if a.startswith(j + "/"):
        return a[len(j) + 1 :]
    raise FilesError("escape", "Path is outside the jail")


def human_size(n: Optional[int]) -> str:
    try:
        val = float(int(n or 0))
    except (TypeError, ValueError):
        val = 0.0
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if val < 1024 or unit == "TiB":
            if unit == "B":
                return f"{int(val)} B"
            return f"{val:.1f} {unit}"
        val /= 1024.0
    return f"{int(val)} B"


def _mtime_dt(raw: Any) -> Optional[datetime]:
    try:
        n = float(raw)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    return datetime.fromtimestamp(n, tz=timezone.utc)


@contextmanager
def sftp_session(server: Any, identity: Any = None, sftp: Any = None):
    if sftp is not None:
        yield sftp
        return
    client = None
    handle = None
    try:
        client = get_ssh_client(server, identity)
        handle = client.open_sftp()
        yield handle
    except FilesError:
        raise
    except Exception as e:
        raise FilesError("ssh", f"{type(e).__name__}: {e}"[:240]) from e
    finally:
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def _normalize_remote(sftp: Any, path: str) -> str:
    try:
        n = sftp.normalize(path)
    except Exception:
        n = path
    return posix_norm(str(n or path))


def _assert_in_jail(
    abs_path: str,
    server: Any,
    *,
    role: str,
    identity: Any = None,
    sftp: Any = None,
) -> str:
    jail = jail_path(server, role=role, identity=identity)
    path = posix_norm(abs_path)
    if sftp is not None:
        path = _normalize_remote(sftp, path)
    if not under_jail(path, jail):
        raise FilesError("escape", "Path is outside the jail")
    if is_denied(path, server, role=role, identity=identity):
        raise FilesError("denied", "Path is blocked")
    return path


def _kind_from_mode(mode: int) -> str:
    if stat.S_ISLNK(mode):
        return "link"
    if stat.S_ISDIR(mode):
        return "dir"
    return "file"


def _lstat(sftp: Any, path: str) -> Any:
    try:
        return sftp.lstat(path)
    except FileNotFoundError as e:
        raise FilesError("not_found", "Not found") from e
    except OSError as e:
        raise FilesError("not_found", "Not found") from e


def _entry_dict(
    *,
    name: str,
    rel: str,
    kind: str,
    size: Optional[int],
    mtime: Any,
    escaped: bool,
) -> dict[str, Any]:
    return {
        "name": name,
        "rel": rel,
        "kind": kind,
        "size": int(size) if size is not None and kind == "file" else None,
        "size_h": human_size(size) if kind == "file" and size is not None else "",
        "mtime": _mtime_dt(mtime),
        "secretish": is_secretish(name),
        "escaped": bool(escaped),
    }


def list_dir(
    server: Any,
    rel: Optional[str] = "",
    *,
    role: str = ROLE_FLEET,
    identity: Any = None,
    sftp: Any = None,
) -> dict[str, Any]:
    role = normalize_role(role)
    jail, abs_path = resolve_logical(server, rel, role=role, identity=identity)
    with sftp_session(server, identity, sftp) as fs:
        path = _assert_in_jail(abs_path, server, role=role, identity=identity, sftp=fs)
        st = _lstat(fs, path)
        kind = _kind_from_mode(int(getattr(st, "st_mode", 0) or 0))
        if kind == "link":
            target = _normalize_remote(fs, path)
            if not under_jail(target, jail) or is_denied(target, server, role=role, identity=identity):
                raise FilesError("escape", "Symlink leaves the jail")
            st = _lstat(fs, target) if target != path else st
            kind = _kind_from_mode(int(getattr(st, "st_mode", 0) or 0))
            path = target
        if kind != "dir":
            raise FilesError("is_file", "Not a directory")
        try:
            attrs = list(fs.listdir_attr(path))
        except Exception as e:
            raise FilesError("ssh", f"{type(e).__name__}: {e}"[:240]) from e
        truncated = len(attrs) > LIST_CAP
        attrs = attrs[:LIST_CAP]
        entries: list[dict[str, Any]] = []
        for a in attrs:
            name = getattr(a, "filename", None) or getattr(a, "longname", "") or ""
            name = str(name).rsplit("/", 1)[-1]
            if not name or name in (".", ".."):
                continue
            child = posixpath.join(path, name).replace("\\", "/")
            child = posix_norm(child)
            mode = int(getattr(a, "st_mode", 0) or 0)
            ekind = _kind_from_mode(mode)
            escaped = False
            if ekind == "link":
                try:
                    target = _normalize_remote(fs, child)
                    escaped = (not under_jail(target, jail)) or is_denied(
                        target, server, role=role, identity=identity
                    )
                except Exception:
                    escaped = True
            elif not under_jail(child, jail) or is_denied(child, server, role=role, identity=identity):
                escaped = True
            try:
                child_rel = rel_of(child, jail)
            except FilesError:
                child_rel = name
            entries.append(
                _entry_dict(
                    name=name,
                    rel=child_rel,
                    kind=ekind,
                    size=getattr(a, "st_size", None),
                    mtime=getattr(a, "st_mtime", None),
                    escaped=escaped,
                )
            )
        entries.sort(key=lambda e: (0 if e["kind"] == "dir" else 1, e["name"].lower()))
        crumbs = []
        acc: list[str] = []
        for seg in parse_rel(rel_of(path, jail)):
            acc.append(seg)
            crumbs.append({"name": seg, "rel": "/".join(acc)})
        return {
            "jail": jail,
            "rel": rel_of(path, jail),
            "abs": path,
            "truncated": truncated,
            "entries": entries,
            "crumbs": crumbs,
        }


def stat_file(
    server: Any,
    rel: str,
    *,
    role: str = ROLE_FLEET,
    identity: Any = None,
    sftp: Any = None,
) -> dict[str, Any]:
    role = normalize_role(role)
    jail, abs_path = resolve_logical(server, rel, role=role, identity=identity)
    with sftp_session(server, identity, sftp) as fs:
        path = _assert_in_jail(abs_path, server, role=role, identity=identity, sftp=fs)
        st = _lstat(fs, path)
        kind = _kind_from_mode(int(getattr(st, "st_mode", 0) or 0))
        escaped = False
        if kind == "link":
            target = _normalize_remote(fs, path)
            escaped = (not under_jail(target, jail)) or is_denied(
                target, server, role=role, identity=identity
            )
            if escaped:
                raise FilesError("escape", "Symlink leaves the jail")
            path = target
            st = _lstat(fs, path)
            kind = _kind_from_mode(int(getattr(st, "st_mode", 0) or 0))
        if kind == "dir":
            raise FilesError("is_dir", "Not a file")
        return {
            "jail": jail,
            "rel": rel_of(path, jail),
            "abs": path,
            "kind": kind,
            "size": int(getattr(st, "st_size", 0) or 0),
            "mtime": _mtime_dt(getattr(st, "st_mtime", None)),
            "secretish": is_secretish(path.rsplit("/", 1)[-1]),
        }


def iter_file(
    server: Any,
    rel: str,
    *,
    role: str = ROLE_FLEET,
    identity: Any = None,
    sftp: Any = None,
) -> Iterator[bytes]:
    """Yield file bytes. Caller should hash while iterating for audit."""
    info = stat_file(server, rel, role=role, identity=identity, sftp=sftp)
    path = info["abs"]
    with sftp_session(server, identity, sftp) as fs:
        path = _assert_in_jail(path, server, role=role, identity=identity, sftp=fs)
        with fs.open(path, "rb") as fh:
            while True:
                chunk = fh.read(CHUNK)
                if not chunk:
                    break
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8")
                yield bytes(chunk)


def put_file(
    server: Any,
    rel_dir: str,
    filename: str,
    stream: BinaryIO,
    *,
    size: Optional[int] = None,
    role: str = ROLE_FLEET,
    identity: Any = None,
    sftp: Any = None,
) -> dict[str, Any]:
    name = sanitize_basename(filename)
    role = normalize_role(role)
    cap = max_upload_bytes()
    if size is not None and int(size) > cap:
        raise FilesError("too_large", f"Upload exceeds {human_size(cap)}")
    jail, dest_dir = resolve_logical(server, rel_dir, role=role, identity=identity)
    dest = join_jail(jail, (rel_of(dest_dir, jail) + "/" + name).strip("/"))
    if is_denied(dest, server, role=role, identity=identity):
        raise FilesError("denied", "Path is blocked")
    tmp = dest + ".tmp"
    hasher = hashlib.sha256()
    written = 0
    with sftp_session(server, identity, sftp) as fs:
        dest_dir = _assert_in_jail(dest_dir, server, role=role, identity=identity, sftp=fs)
        st = _lstat(fs, dest_dir)
        if _kind_from_mode(int(getattr(st, "st_mode", 0) or 0)) != "dir":
            raise FilesError("is_file", "Not a directory")
        dest = posix_norm(posixpath.join(dest_dir, name))
        if not under_jail(dest, jail):
            raise FilesError("escape", "Path is outside the jail")
        tmp = dest + ".tmp"
        existed = False
        try:
            _lstat(fs, dest)
            existed = True
        except FilesError as e:
            if e.code != "not_found":
                raise
            existed = False
        try:
            with fs.open(tmp, "wb") as fh:
                while True:
                    chunk = stream.read(CHUNK)
                    if not chunk:
                        break
                    if isinstance(chunk, str):
                        chunk = chunk.encode("utf-8")
                    chunk = bytes(chunk)
                    written += len(chunk)
                    if written > cap:
                        try:
                            fs.remove(tmp)
                        except Exception:
                            pass
                        raise FilesError("too_large", f"Upload exceeds {human_size(cap)}")
                    hasher.update(chunk)
                    fh.write(chunk)
            try:
                fs.remove(dest)
            except Exception:
                pass
            fs.rename(tmp, dest)
        except FilesError:
            try:
                fs.remove(tmp)
            except Exception:
                pass
            raise
        except Exception as e:
            try:
                fs.remove(tmp)
            except Exception:
                pass
            raise FilesError("ssh", f"{type(e).__name__}: {e}"[:240]) from e
    return {
        "jail": jail,
        "rel": rel_of(dest, jail),
        "abs": dest,
        "bytes": written,
        "sha256": hasher.hexdigest(),
        "overwrite": existed,
        "name": name,
    }


def mkdir(
    server: Any,
    rel_dir: str,
    name: str,
    *,
    role: str = ROLE_FLEET,
    identity: Any = None,
    sftp: Any = None,
) -> dict[str, Any]:
    base = sanitize_basename(name)
    role = normalize_role(role)
    jail, dest_dir = resolve_logical(server, rel_dir, role=role, identity=identity)
    dest = join_jail(jail, (rel_of(dest_dir, jail) + "/" + base).strip("/"))
    if is_denied(dest, server, role=role, identity=identity):
        raise FilesError("denied", "Path is blocked")
    with sftp_session(server, identity, sftp) as fs:
        dest_dir = _assert_in_jail(dest_dir, server, role=role, identity=identity, sftp=fs)
        dest = posix_norm(posixpath.join(dest_dir, base))
        if not under_jail(dest, jail):
            raise FilesError("escape", "Path is outside the jail")
        try:
            fs.mkdir(dest)
        except Exception as e:
            raise FilesError("ssh", f"{type(e).__name__}: {e}"[:240]) from e
    return {"jail": jail, "rel": rel_of(dest, jail), "abs": dest, "name": base}


def remove(
    server: Any,
    rel: str,
    *,
    role: str = ROLE_FLEET,
    identity: Any = None,
    sftp: Any = None,
) -> dict[str, Any]:
    role = normalize_role(role)
    jail, abs_path = resolve_logical(server, rel, role=role, identity=identity)
    with sftp_session(server, identity, sftp) as fs:
        path = _assert_in_jail(abs_path, server, role=role, identity=identity, sftp=fs)
        if path == jail:
            raise FilesError("denied", "Cannot delete the jail root")
        st = _lstat(fs, path)
        kind = _kind_from_mode(int(getattr(st, "st_mode", 0) or 0))
        if kind == "link":
            target = _normalize_remote(fs, path)
            if not under_jail(target, jail) or is_denied(target, server, role=role, identity=identity):
                raise FilesError("escape", "Symlink leaves the jail")
        try:
            if kind == "dir":
                names = [n for n in (fs.listdir(path) or []) if n not in (".", "..")]
                if names:
                    raise FilesError("not_empty", "Directory is not empty")
                fs.rmdir(path)
            else:
                fs.remove(path)
        except FilesError:
            raise
        except Exception as e:
            raise FilesError("ssh", f"{type(e).__name__}: {e}"[:240]) from e
    return {"jail": jail, "rel": rel_of(abs_path, jail), "abs": abs_path, "kind": kind}


def rename(
    server: Any,
    rel_dir: str,
    src_name: str,
    dest_name: str,
    *,
    role: str = ROLE_FLEET,
    identity: Any = None,
    sftp: Any = None,
) -> dict[str, Any]:
    src_base = sanitize_basename(src_name)
    dest_base = sanitize_basename(dest_name)
    role = normalize_role(role)
    jail, dest_dir = resolve_logical(server, rel_dir, role=role, identity=identity)
    with sftp_session(server, identity, sftp) as fs:
        dest_dir = _assert_in_jail(dest_dir, server, role=role, identity=identity, sftp=fs)
        src = posix_norm(posixpath.join(dest_dir, src_base))
        dest = posix_norm(posixpath.join(dest_dir, dest_base))
        for p in (src, dest):
            if not under_jail(p, jail) or is_denied(p, server, role=role, identity=identity):
                raise FilesError("escape" if not under_jail(p, jail) else "denied", "Path is blocked")
        if posixpath.dirname(src) != posixpath.dirname(dest):
            raise FilesError("escape", "Rename must stay in the same directory")
        try:
            fs.rename(src, dest)
        except Exception as e:
            raise FilesError("ssh", f"{type(e).__name__}: {e}"[:240]) from e
    return {
        "jail": jail,
        "from": rel_of(src, jail),
        "to": rel_of(dest, jail),
        "abs": dest,
    }
