"""Confined host Files (v1.3 Stream F): jailed SFTP list/get/put/mkdir/delete/rename.

Also: in-browser text edit (512 KiB, same cap as compose sidecars), zip of a
selection (files and folders), unzip with zip-slip refusal, recursive delete,
chmod / chown (privileged; sudo -n when the identity is not root), name search,
move across folders, recursive folder upload.

Kill switch: PIHERDER_HOST_FILES (default off). Demo never opens real SFTP.
Fleet identity is the default; privileged is break-glass (UI + 2FA only).
"""
from __future__ import annotations

import hashlib
import io
import posixpath
import re
import shlex
import stat
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, BinaryIO, Iterator, Optional

from ..config import settings
from .ssh import expand_remote_path, get_ssh_client, run_command

ROLE_FLEET = "fleet"
ROLE_PRIVILEGED = "privileged"
ROLES = (ROLE_FLEET, ROLE_PRIVILEGED)

LIST_CAP = 500
# 1 MiB Python buffers; Paramiko still splits to SFTP packets (~32 KiB).
CHUNK = 1024 * 1024
SFTP_WINDOW = 32 * 1024 * 1024
SFTP_MAX_PACKET = 32768
POOL_IDLE_SEC = 75
EDIT_MAX = 512 * 1024
ZIP_MEMBERS_MAX = 2000
WALK_DEPTH_MAX = 24
SEARCH_CAP = 200
SEARCH_SCAN_MAX = 2000
SEARCH_Q_MAX = 80
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

_MODE_RE = re.compile(r"^[0-7]{3,4}$")
_ID_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*\$?$")


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


@dataclass
class _SftpLease:
    client: Any
    sftp: Any
    lock: threading.Lock
    last: float
    key: tuple


_pool_lock = threading.Lock()
_pool: dict[tuple, _SftpLease] = {}


def _pool_key(server: Any, identity: Any) -> tuple:
    return (
        getattr(server, "id", None),
        (getattr(identity, "role", None) or ROLE_FLEET),
        getattr(identity, "id", None),
        getattr(server, "hostname", None) or getattr(server, "ip_address", None),
        int(getattr(server, "ssh_port", 22) or 22),
        identity_username(server, identity),
    )


def _transport_alive(client: Any) -> bool:
    try:
        t = client.get_transport() if client is not None else None
        return bool(t is not None and t.is_active())
    except Exception:
        return False


def _close_lease(lease: _SftpLease) -> None:
    try:
        lease.sftp.close()
    except Exception:
        pass
    try:
        lease.client.close()
    except Exception:
        pass


def _tune_transport(client: Any) -> None:
    try:
        t = client.get_transport()
        if t is None:
            return
        t.set_keepalive(20)
        try:
            t.default_window_size = 8 * 1024 * 1024
            t.default_max_packet_size = 32768
        except Exception:
            pass
    except Exception:
        pass


def _open_client(server: Any, identity: Any) -> tuple[Any, Any]:
    import paramiko

    client = get_ssh_client(server, identity)
    _tune_transport(client)
    t = client.get_transport()
    handle = None
    if t is not None:
        try:
            handle = paramiko.SFTPClient.from_transport(
                t,
                window_size=SFTP_WINDOW,
                max_packet_size=SFTP_MAX_PACKET,
            )
        except TypeError:
            handle = None
        except Exception:
            handle = None
    if handle is None:
        handle = client.open_sftp()
    return client, handle


def _sweep_idle_locked(now: float) -> None:
    drop: list[tuple] = []
    for key, lease in list(_pool.items()):
        if now - lease.last <= POOL_IDLE_SEC:
            continue
        if not lease.lock.acquire(blocking=False):
            continue
        try:
            _close_lease(lease)
            drop.append(key)
        finally:
            lease.lock.release()
    for key in drop:
        _pool.pop(key, None)


def drop_sftp_pool() -> None:
    """Test helper — close every pooled session."""
    with _pool_lock:
        for lease in list(_pool.values()):
            _close_lease(lease)
        _pool.clear()


def _tune_sftp_file(fh: Any, *, size: Optional[int] = None, write: bool = False) -> None:
    # Do not prefetch/pipeline whole files. Prefetch of the full size queues every
    # SFTP read and stalls around ~10–12 MiB the same way pipelined writes do.
    return


@contextmanager
def sftp_session(
    server: Any,
    identity: Any = None,
    sftp: Any = None,
    *,
    pooled: bool = True,
    with_client: bool = False,
    client: Any = None,
):
    """Reuse one SFTP session per host/identity for ~75s idle (browse).

    Transfers (get/put) pass ``pooled=False`` so a long download does not lock
    the browse session or fight a listing refresh.

    ``with_client=True`` yields ``(ssh_client, sftp)`` so chmod/chown can
    fall back to ``sudo -n`` on the same connection.
    """
    def _out(cli: Any, handle: Any):
        return (cli, handle) if with_client else handle

    if sftp is not None:
        yield _out(client, sftp)
        return
    if not pooled:
        opened = None
        handle = None
        try:
            opened, handle = _open_client(server, identity)
            yield _out(opened, handle)
            return
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
            if opened is not None:
                try:
                    opened.close()
                except Exception:
                    pass
    key = _pool_key(server, identity)
    with _pool_lock:
        _sweep_idle_locked(time.monotonic())
        lease = _pool.get(key)
    if lease is None:
        try:
            opened, handle = _open_client(server, identity)
        except FilesError:
            raise
        except Exception as e:
            raise FilesError("ssh", f"{type(e).__name__}: {e}"[:240]) from e
        fresh = _SftpLease(
            client=opened, sftp=handle, lock=threading.Lock(), last=time.monotonic(), key=key
        )
        with _pool_lock:
            existing = _pool.get(key)
            if existing is not None and _transport_alive(existing.client):
                _close_lease(fresh)
                lease = existing
            else:
                _pool[key] = fresh
                lease = fresh
    lease.lock.acquire()
    try:
        if not _transport_alive(lease.client):
            try:
                _close_lease(lease)
                lease.client, lease.sftp = _open_client(server, identity)
            except FilesError:
                with _pool_lock:
                    _pool.pop(key, None)
                raise
            except Exception as e:
                with _pool_lock:
                    _pool.pop(key, None)
                raise FilesError("ssh", f"{type(e).__name__}: {e}"[:240]) from e
        try:
            yield _out(lease.client, lease.sftp)
            lease.last = time.monotonic()
        except FilesError as e:
            if e.code == "ssh":
                with _pool_lock:
                    if _pool.get(key) is lease:
                        _close_lease(lease)
                        _pool.pop(key, None)
            raise
    finally:
        lease.lock.release()


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


def mode_octal(mode: Optional[int]) -> str:
    if mode is None:
        return ""
    try:
        return f"{stat.S_IMODE(int(mode)):o}"
    except (TypeError, ValueError):
        return ""


def parse_getent(text: str, id_field: int = 2) -> dict[int, str]:
    """Parse ``getent passwd`` / ``getent group`` (name:x:id:…)."""
    out: dict[int, str] = {}
    for line in (text or "").splitlines():
        parts = line.split(":")
        if len(parts) <= id_field:
            continue
        if not parts[id_field].isdigit():
            continue
        name = (parts[0] or "").strip()
        if not name:
            continue
        out[int(parts[id_field])] = name
    return out


_id_maps_cache: dict[tuple, tuple[float, dict[int, str], dict[int, str]]] = {}


def user_group_maps(client: Any, cache_key: Optional[tuple] = None) -> tuple[dict[int, str], dict[int, str]]:
    """uid/gid → name. Cached ~75s. Empty when there is no SSH exec (unit tests)."""
    now = time.monotonic()
    if cache_key:
        hit = _id_maps_cache.get(cache_key)
        if hit and now - hit[0] < POOL_IDLE_SEC:
            return hit[1], hit[2]
    users: dict[int, str] = {}
    groups: dict[int, str] = {}
    if client is not None:
        for cmd, dest, fallback in (
            ("getent passwd", users, "cat /etc/passwd"),
            ("getent group", groups, "cat /etc/group"),
        ):
            raw = ""
            try:
                status, out, _err = run_command(client, cmd, timeout=8)
                if status == 0 and out:
                    raw = out
            except Exception:
                raw = ""
            if not raw:
                try:
                    status, out, _err = run_command(client, fallback, timeout=8)
                    if status == 0 and out:
                        raw = out
                except Exception:
                    raw = ""
            dest.update(parse_getent(raw))
    if cache_key:
        _id_maps_cache[cache_key] = (now, users, groups)
    return users, groups


def _owner_fields(
    uid: Optional[int],
    gid: Optional[int],
    users: Optional[dict[int, str]] = None,
    groups: Optional[dict[int, str]] = None,
) -> tuple[Optional[int], Optional[int], str, str, str]:
    uid_i = int(uid) if uid is not None else None
    gid_i = int(gid) if gid is not None else None
    users = users or {}
    groups = groups or {}
    owner = users.get(uid_i, str(uid_i) if uid_i is not None else "")
    group = groups.get(gid_i, str(gid_i) if gid_i is not None else "")
    if owner and group:
        owner_h = f"{owner}:{group}"
    else:
        owner_h = owner or group
    return uid_i, gid_i, owner, group, owner_h


def _entry_dict(
    *,
    name: str,
    rel: str,
    kind: str,
    size: Optional[int],
    mtime: Any,
    escaped: bool,
    mode: Optional[int] = None,
    uid: Optional[int] = None,
    gid: Optional[int] = None,
    users: Optional[dict[int, str]] = None,
    groups: Optional[dict[int, str]] = None,
) -> dict[str, Any]:
    uid_i, gid_i, owner, group, owner_h = _owner_fields(uid, gid, users, groups)
    return {
        "name": name,
        "rel": rel,
        "kind": kind,
        "size": int(size) if size is not None and kind == "file" else None,
        "size_h": human_size(size) if kind == "file" and size is not None else "",
        "mtime": _mtime_dt(mtime),
        "secretish": is_secretish(name),
        "escaped": bool(escaped),
        "mode": stat.S_IMODE(int(mode)) if mode is not None else None,
        "mode_h": mode_octal(mode),
        "uid": uid_i,
        "gid": gid_i,
        "owner": owner,
        "group": group,
        "owner_h": owner_h,
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
    with sftp_session(server, identity, sftp, with_client=True) as pair:
        cli, fs = pair
        users, groups = user_group_maps(cli, _pool_key(server, identity))
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
            uid = getattr(a, "st_uid", None)
            gid = getattr(a, "st_gid", None)
            try:
                uid = int(uid) if uid is not None else None
            except (TypeError, ValueError):
                uid = None
            try:
                gid = int(gid) if gid is not None else None
            except (TypeError, ValueError):
                gid = None
            entries.append(
                _entry_dict(
                    name=name,
                    rel=child_rel,
                    kind=ekind,
                    size=getattr(a, "st_size", None),
                    mtime=getattr(a, "st_mtime", None),
                    escaped=escaped,
                    mode=mode,
                    uid=uid,
                    gid=gid,
                    users=users,
                    groups=groups,
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
    """Yield file bytes on one SFTP session (stat + read). Hash while iterating."""
    role = normalize_role(role)
    jail, abs_path = resolve_logical(server, rel, role=role, identity=identity)
    with sftp_session(server, identity, sftp, pooled=False) as fs:
        path = _assert_in_jail(abs_path, server, role=role, identity=identity, sftp=fs)
        st = _lstat(fs, path)
        kind = _kind_from_mode(int(getattr(st, "st_mode", 0) or 0))
        if kind == "link":
            target = _normalize_remote(fs, path)
            if not under_jail(target, jail) or is_denied(target, server, role=role, identity=identity):
                raise FilesError("escape", "Symlink leaves the jail")
            path = target
            st = _lstat(fs, path)
            kind = _kind_from_mode(int(getattr(st, "st_mode", 0) or 0))
        if kind == "dir":
            raise FilesError("is_dir", "Not a file")
        size = int(getattr(st, "st_size", 0) or 0)
        with fs.open(path, "rb") as fh:
            _tune_sftp_file(fh, size=size or None)
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
    progress: Any = None,
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
    with sftp_session(server, identity, sftp, pooled=False) as fs:
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
                _tune_sftp_file(fh, write=True)
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
                    if progress is not None:
                        try:
                            progress(written, int(size) if size else written)
                        except Exception:
                            pass
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


def ensure_dir(
    server: Any,
    rel: str,
    *,
    role: str = ROLE_FLEET,
    identity: Any = None,
    sftp: Any = None,
) -> dict[str, Any]:
    """mkdir -p a jail-relative path. Existing directories are left alone."""
    role = normalize_role(role)
    parts = parse_rel(rel)
    jail, acc = resolve_logical(server, "", role=role, identity=identity)
    created = 0
    with sftp_session(server, identity, sftp) as fs:
        acc = _assert_in_jail(acc, server, role=role, identity=identity, sftp=fs)
        for seg in parts:
            base = sanitize_basename(seg)
            nxt = posix_norm(posixpath.join(acc, base))
            nxt = _assert_in_jail(nxt, server, role=role, identity=identity, sftp=fs)
            if not under_jail(nxt, jail) or is_denied(nxt, server, role=role, identity=identity):
                raise FilesError("escape" if not under_jail(nxt, jail) else "denied", "Path is blocked")
            try:
                st = _lstat(fs, nxt)
                kind = _kind_from_mode(int(getattr(st, "st_mode", 0) or 0))
                if kind != "dir":
                    raise FilesError("is_file", f"{base} is a file")
            except FilesError as e:
                if e.code != "not_found":
                    raise
                try:
                    fs.mkdir(nxt)
                    created += 1
                except Exception as ex:
                    try:
                        st = _lstat(fs, nxt)
                        if _kind_from_mode(int(getattr(st, "st_mode", 0) or 0)) != "dir":
                            raise FilesError("is_file", f"{base} is a file") from ex
                    except FilesError:
                        raise FilesError("ssh", f"{type(ex).__name__}: {ex}"[:240]) from ex
            acc = nxt
    return {"jail": jail, "rel": rel_of(acc, jail), "abs": acc, "created": created}


def parse_nested_rel(raw: str) -> list[str]:
    """Relative path from a folder picker / drag-drop (same rules as zip members)."""
    return parse_rel(_safe_zip_name(raw))


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


def looks_like_text(name: str, sample: bytes) -> bool:
    if b"\x00" in (sample or b""):
        return False
    base = (name or "").rsplit(".", 1)
    ext = ("." + base[-1].lower()) if len(base) == 2 else ""
    if ext in {
        ".yml",
        ".yaml",
        ".json",
        ".toml",
        ".ini",
        ".conf",
        ".cfg",
        ".txt",
        ".md",
        ".env",
        ".sh",
        ".py",
        ".js",
        ".ts",
        ".css",
        ".html",
        ".xml",
        ".log",
        ".csv",
        ".service",
        ".desktop",
        ".gitignore",
        ".sql",
    }:
        return True
    try:
        (sample or b"").decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def read_text(
    server: Any,
    rel: str,
    *,
    role: str = ROLE_FLEET,
    identity: Any = None,
    sftp: Any = None,
) -> dict[str, Any]:
    """Read a text file for the in-app editor (same 512 KiB cap as compose sidecars)."""
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
            path = target
            st = _lstat(fs, path)
            kind = _kind_from_mode(int(getattr(st, "st_mode", 0) or 0))
        if kind == "dir":
            raise FilesError("is_dir", "Not a file")
        size = int(getattr(st, "st_size", 0) or 0)
        if size > EDIT_MAX:
            raise FilesError("too_large", f"File is larger than {human_size(EDIT_MAX)} — download instead")
        with fs.open(path, "rb") as fh:
            raw = fh.read(EDIT_MAX + 1)
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        raw = bytes(raw or b"")
        if not looks_like_text(path.rsplit("/", 1)[-1], raw[:8000]):
            raise FilesError("binary", "This does not look like text — download instead")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as e:
            raise FilesError("binary", "File is not UTF-8 text") from e
        return {
            "rel": rel_of(path, jail),
            "name": path.rsplit("/", 1)[-1],
            "size": size,
            "text": text,
        }


def write_text(
    server: Any,
    rel: str,
    text: str,
    *,
    role: str = ROLE_FLEET,
    identity: Any = None,
    sftp: Any = None,
) -> dict[str, Any]:
    data = (text if isinstance(text, str) else str(text or "")).encode("utf-8")
    if len(data) > EDIT_MAX:
        raise FilesError("too_large", f"Save exceeds {human_size(EDIT_MAX)}")
    rel_dir, _, name = (rel or "").replace("\\", "/").rpartition("/")
    return put_file(
        server,
        rel_dir,
        name or rel,
        io.BytesIO(data),
        size=len(data),
        role=role,
        identity=identity,
        sftp=sftp,
    )


def _listdir_names(fs: Any, path: str) -> list[str]:
    try:
        names = list(fs.listdir(path) or [])
    except Exception as e:
        raise FilesError("ssh", f"{type(e).__name__}: {e}"[:240]) from e
    return [n for n in names if n not in (".", "..")]


def remove_tree(
    server: Any,
    rel: str,
    *,
    role: str = ROLE_FLEET,
    identity: Any = None,
    sftp: Any = None,
) -> dict[str, Any]:
    """Delete a file or a directory tree (folders and contents)."""
    role = normalize_role(role)
    jail, abs_path = resolve_logical(server, rel, role=role, identity=identity)
    removed = {"files": 0, "dirs": 0}

    def _rm(fs: Any, path: str, depth: int) -> None:
        if depth > WALK_DEPTH_MAX:
            raise FilesError("too_many", "Folder is too deep to delete")
        path = _assert_in_jail(path, server, role=role, identity=identity, sftp=fs)
        if path == jail:
            raise FilesError("denied", "Cannot delete the jail root")
        st = _lstat(fs, path)
        kind = _kind_from_mode(int(getattr(st, "st_mode", 0) or 0))
        if kind == "dir":
            for name in _listdir_names(fs, path):
                _rm(fs, posix_norm(posixpath.join(path, name)), depth + 1)
            fs.rmdir(path)
            removed["dirs"] += 1
        else:
            fs.remove(path)
            removed["files"] += 1

    with sftp_session(server, identity, sftp) as fs:
        try:
            _rm(fs, abs_path, 0)
        except FilesError:
            raise
        except Exception as e:
            raise FilesError("ssh", f"{type(e).__name__}: {e}"[:240]) from e
    return {"jail": jail, "rel": rel, **removed}


def _safe_zip_name(member: str) -> str:
    raw = (member or "").replace("\\", "/").strip()
    if not raw or "\x00" in raw:
        raise FilesError("escape", "Invalid path in zip")
    if raw.startswith("/") or raw.startswith("\\"):
        raise FilesError("escape", "Zip path must not be absolute")
    parts = [p for p in raw.split("/") if p and p != "."]
    if any(p == ".." for p in parts):
        raise FilesError("escape", "Zip path escapes the folder")
    if len(parts) > WALK_DEPTH_MAX:
        raise FilesError("escape", "Zip path is too deep")
    return "/".join(parts)


def _walk_files(
    fs: Any,
    server: Any,
    *,
    jail: str,
    abs_path: str,
    rel: str,
    role: str,
    identity: Any,
    depth: int,
    budget: list[int],
) -> Iterator[tuple[str, str, int]]:
    if depth > WALK_DEPTH_MAX:
        raise FilesError("too_many", "Folder is too deep to zip")
    path = _assert_in_jail(abs_path, server, role=role, identity=identity, sftp=fs)
    st = _lstat(fs, path)
    kind = _kind_from_mode(int(getattr(st, "st_mode", 0) or 0))
    if kind == "link":
        target = _normalize_remote(fs, path)
        if not under_jail(target, jail) or is_denied(target, server, role=role, identity=identity):
            return
        path = target
        st = _lstat(fs, path)
        kind = _kind_from_mode(int(getattr(st, "st_mode", 0) or 0))
    if kind != "dir":
        budget[0] += 1
        if budget[0] > ZIP_MEMBERS_MAX:
            raise FilesError("too_many", f"Zip is limited to {ZIP_MEMBERS_MAX} files")
        yield (rel or path.rsplit("/", 1)[-1], path, int(getattr(st, "st_size", 0) or 0))
        return
    arc_dir = (rel.rstrip("/") + "/") if rel else ""
    if arc_dir:
        yield (arc_dir, path, 0)
    for name in sorted(_listdir_names(fs, path)):
        child_abs = posix_norm(posixpath.join(path, name))
        child_rel = (rel + "/" + name) if rel else name
        yield from _walk_files(
            fs,
            server,
            jail=jail,
            abs_path=child_abs,
            rel=child_rel,
            role=role,
            identity=identity,
            depth=depth + 1,
            budget=budget,
        )


def zip_basename(suggested: Optional[str], rels: Optional[list[str]] = None) -> str:
    raw = (suggested or "").strip()
    if raw:
        base = sanitize_basename(raw)
        if not base.lower().endswith(".zip"):
            base += ".zip"
        return base
    names = [r.strip().strip("/") for r in (rels or []) if str(r).strip()]
    if len(names) == 1:
        return sanitize_basename(names[0].rsplit("/", 1)[-1] or "files") + ".zip"
    return "files.zip"


def _zip_buffer(
    server: Any,
    rels: list[str],
    *,
    role: str = ROLE_FLEET,
    identity: Any = None,
    sftp: Any = None,
    dest_name: Optional[str] = None,
) -> tuple[str, Any]:
    """Build a zip in a spooled temp file. Caller must close the file."""
    import zipfile

    names = [r.strip().strip("/") for r in (rels or []) if str(r).strip()]
    if not names:
        raise FilesError("invalid", "Nothing selected to zip")
    role = normalize_role(role)
    fname = zip_basename(dest_name, names)
    buf = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
    total = 0
    try:
        with sftp_session(server, identity, sftp, pooled=False) as fs:
            with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
                budget = [0]
                for rel in names:
                    if rel.rsplit("/", 1)[-1] == fname:
                        continue
                    jail, abs_path = resolve_logical(server, rel, role=role, identity=identity)
                    arc_root = rel.rsplit("/", 1)[-1] if rel else "files"
                    for arc, src, sz in _walk_files(
                        fs,
                        server,
                        jail=jail,
                        abs_path=abs_path,
                        rel=arc_root,
                        role=role,
                        identity=identity,
                        depth=0,
                        budget=budget,
                    ):
                        total += sz
                        if total > max_upload_bytes():
                            raise FilesError("too_large", "Zip contents exceed the transfer cap")
                        if arc.endswith("/"):
                            zf.writestr(arc, b"")
                            continue
                        info = zipfile.ZipInfo(filename=arc)
                        info.compress_type = zipfile.ZIP_DEFLATED
                        with zf.open(info, "w") as dest:
                            with fs.open(src, "rb") as fh:
                                while True:
                                    block = fh.read(CHUNK)
                                    if not block:
                                        break
                                    if isinstance(block, str):
                                        block = block.encode("utf-8")
                                    dest.write(bytes(block))
        buf.seek(0)
        return fname, buf
    except Exception:
        try:
            buf.close()
        except Exception:
            pass
        raise


def build_zip(
    server: Any,
    rels: list[str],
    *,
    role: str = ROLE_FLEET,
    identity: Any = None,
    sftp: Any = None,
    dest_name: Optional[str] = None,
) -> tuple[str, Iterator[bytes]]:
    """Zip one or more jail-relative files/folders. Returns (filename, byte iterator)."""
    fname, buf = _zip_buffer(
        server, rels, role=role, identity=identity, sftp=sftp, dest_name=dest_name
    )

    def chunks() -> Iterator[bytes]:
        try:
            while True:
                block = buf.read(CHUNK)
                if not block:
                    break
                yield block
        finally:
            try:
                buf.close()
            except Exception:
                pass

    return fname, chunks()


def save_zip(
    server: Any,
    rels: list[str],
    dest_dir: str,
    dest_name: Optional[str] = None,
    *,
    role: str = ROLE_FLEET,
    identity: Any = None,
    sftp: Any = None,
) -> dict[str, Any]:
    """Write a zip of ``rels`` into ``dest_dir`` on the host."""
    fname, buf = _zip_buffer(
        server, rels, role=role, identity=identity, sftp=sftp, dest_name=dest_name
    )
    try:
        buf.seek(0, 2)
        size = buf.tell()
        buf.seek(0)
        return put_file(
            server,
            dest_dir,
            fname,
            buf,
            size=size,
            role=role,
            identity=identity,
            sftp=sftp,
        )
    finally:
        try:
            buf.close()
        except Exception:
            pass


def unzip_into(
    server: Any,
    zip_rel: str,
    dest_rel: str = "",
    *,
    role: str = ROLE_FLEET,
    identity: Any = None,
    sftp: Any = None,
) -> dict[str, Any]:
    """Extract a zip in the jail. Rejects zip-slip and oversize archives."""
    import zipfile

    role = normalize_role(role)
    dest_rel = (dest_rel or "").strip().strip("/")
    written = 0
    files_n = 0
    dirs_n = 0
    cap = max_upload_bytes()
    tmp_zip = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
    try:
        with sftp_session(server, identity, sftp, pooled=False) as fs:
            _jail, zabs = resolve_logical(server, zip_rel, role=role, identity=identity)
            zpath = _assert_in_jail(zabs, server, role=role, identity=identity, sftp=fs)
            dest_jail, dest_abs = resolve_logical(server, dest_rel, role=role, identity=identity)
            dest_abs = _assert_in_jail(dest_abs, server, role=role, identity=identity, sftp=fs)
            with fs.open(zpath, "rb") as fh:
                while True:
                    block = fh.read(CHUNK)
                    if not block:
                        break
                    if isinstance(block, str):
                        block = block.encode("utf-8")
                    tmp_zip.write(bytes(block))
            tmp_zip.seek(0)
            try:
                zf = zipfile.ZipFile(tmp_zip)
            except zipfile.BadZipFile as e:
                raise FilesError("invalid", "Not a valid zip archive") from e
            try:
                infos = zf.infolist()
                if len(infos) > ZIP_MEMBERS_MAX:
                    raise FilesError("too_many", f"Zip has more than {ZIP_MEMBERS_MAX} entries")
                uncompressed = sum(max(0, int(i.file_size or 0)) for i in infos)
                if uncompressed > cap:
                    raise FilesError("too_large", "Unzipped size exceeds the transfer cap")

                def _mkdirs(abs_dir: str) -> None:
                    abs_dir = posix_norm(abs_dir)
                    if abs_dir in ("", "/") or abs_dir == dest_abs:
                        return
                    parent = posixpath.dirname(abs_dir)
                    if parent and parent != abs_dir:
                        _mkdirs(parent)
                    if not under_jail(abs_dir, dest_jail):
                        raise FilesError("escape", "Unzip would leave the jail")
                    try:
                        fs.mkdir(abs_dir)
                    except Exception:
                        pass

                for info in infos:
                    rel = _safe_zip_name(info.filename)
                    if not rel:
                        continue
                    target = posix_norm(posixpath.join(dest_abs, rel))
                    if not under_jail(target, dest_jail) or is_denied(
                        target, server, role=role, identity=identity
                    ):
                        raise FilesError("escape", f"Refused zip path {info.filename!r}")
                    if info.is_dir() or info.filename.endswith("/"):
                        _mkdirs(target)
                        dirs_n += 1
                        continue
                    _mkdirs(posixpath.dirname(target))
                    staging = target + ".tmp"
                    with zf.open(info, "r") as src, fs.open(staging, "wb") as out:
                        while True:
                            chunk = src.read(CHUNK)
                            if not chunk:
                                break
                            out.write(chunk)
                            written += len(chunk)
                            if written > cap:
                                raise FilesError("too_large", "Unzipped size exceeds the transfer cap")
                    try:
                        fs.remove(target)
                    except Exception:
                        pass
                    fs.rename(staging, target)
                    files_n += 1
            finally:
                zf.close()
    finally:
        try:
            tmp_zip.close()
        except Exception:
            pass
    return {"files": files_n, "dirs": dirs_n, "bytes": written, "rel": dest_rel}


def parse_mode(raw: Any) -> int:
    s = str(raw or "").strip().lower()
    if s.startswith("0o"):
        s = s[2:]
    if s.startswith("0") and len(s) > 4:
        s = s.lstrip("0") or "0"
    if not _MODE_RE.match(s):
        raise FilesError("invalid", "Mode must be octal like 644 or 0755")
    return int(s, 8)


def parse_id_name(raw: Any, *, kind: str) -> str:
    s = str(raw or "").strip()
    if not s:
        return ""
    if s.isdigit():
        n = int(s)
        if n < 0 or n > 2_147_483_647:
            raise FilesError("invalid", f"Invalid {kind} id")
        return str(n)
    if not _ID_NAME_RE.match(s):
        raise FilesError("invalid", f"Invalid {kind} name")
    return s


def _is_perm_denied(exc: BaseException) -> bool:
    if isinstance(exc, PermissionError):
        return True
    msg = str(exc).lower()
    return any(tok in msg for tok in ("permission", "denied", "eacces", "eperm"))


def _remote_is_root(client: Any, server: Any, identity: Any) -> bool:
    if identity_username(server, identity) == "root":
        return True
    if client is None:
        return False
    try:
        status, out, _err = run_command(client, "id -u", timeout=8)
        return status == 0 and (out or "").strip() == "0"
    except Exception:
        return False


def _run_elevated(
    client: Any,
    argv: str,
    *,
    as_root: bool,
    allow_sudo: bool,
) -> str:
    """Run chmod/chown on the host. Privileged non-root uses ``sudo -n``.

    Never prompts for a password. HAOS root often has no sudo — try plain too.
    """
    if client is None:
        raise FilesError(
            "denied",
            "Need privileged Files (and passwordless sudo if that user is not root)",
        )
    attempts: list[tuple[str, str]] = []
    if as_root:
        attempts.append(("plain", argv))
        if allow_sudo:
            attempts.append(("sudo", f"sudo -n {argv}"))
    elif allow_sudo:
        attempts.append(("sudo", f"sudo -n {argv}"))
        attempts.append(("plain", argv))
    else:
        attempts.append(("plain", argv))
    last_err = "failed"
    for how, cmd in attempts:
        try:
            status, out, err = run_command(client, cmd, timeout=30)
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"[:200]
            continue
        if status == 0:
            return how
        last_err = ((err or out or f"exit {status}") or "").strip()[:200]
        if how == "sudo" and re.search(
            r"sudo:\s*(command )?not found|a terminal is required|password is required|not in the sudoers",
            last_err,
            re.I,
        ):
            continue
    raise FilesError("denied", f"chmod/chown failed: {last_err}")


def _walk_perm_targets(
    fs: Any,
    server: Any,
    *,
    jail: str,
    abs_path: str,
    role: str,
    identity: Any,
    recursive: bool,
    depth: int,
    budget: list[int],
) -> Iterator[str]:
    if depth > WALK_DEPTH_MAX:
        raise FilesError("too_many", "Folder is too deep to change permissions")
    path = _assert_in_jail(abs_path, server, role=role, identity=identity, sftp=fs)
    if path == jail or path == "/":
        raise FilesError("denied", "Cannot change permissions on the jail root")
    st = _lstat(fs, path)
    kind = _kind_from_mode(int(getattr(st, "st_mode", 0) or 0))
    if kind == "link":
        target = _normalize_remote(fs, path)
        if not under_jail(target, jail) or is_denied(target, server, role=role, identity=identity):
            return
        path = target
        st = _lstat(fs, path)
        kind = _kind_from_mode(int(getattr(st, "st_mode", 0) or 0))
        if path == jail or path == "/":
            raise FilesError("denied", "Cannot change permissions on the jail root")
    budget[0] += 1
    if budget[0] > ZIP_MEMBERS_MAX:
        raise FilesError("too_many", f"Permission change is limited to {ZIP_MEMBERS_MAX} paths")
    yield path
    if not recursive or kind != "dir":
        return
    for name in _listdir_names(fs, path):
        child = posix_norm(posixpath.join(path, name))
        yield from _walk_perm_targets(
            fs,
            server,
            jail=jail,
            abs_path=child,
            role=role,
            identity=identity,
            recursive=True,
            depth=depth + 1,
            budget=budget,
        )


def _sftp_chmod(fs: Any, path: str, mode: int) -> bool:
    try:
        fs.chmod(path, mode)
        return True
    except Exception as e:
        if _is_perm_denied(e):
            return False
        raise FilesError("ssh", f"{type(e).__name__}: {e}"[:240]) from e


def _sftp_chown(fs: Any, path: str, uid: Optional[int], gid: Optional[int]) -> bool:
    if uid is None and gid is None:
        return True
    try:
        st = fs.lstat(path)
        use_uid = int(uid) if uid is not None else int(getattr(st, "st_uid", 0) or 0)
        use_gid = int(gid) if gid is not None else int(getattr(st, "st_gid", 0) or 0)
        fs.chown(path, use_uid, use_gid)
        return True
    except Exception as e:
        if _is_perm_denied(e) or "need names" in str(e).lower():
            return False
        # Some servers lack chown — treat as elevate
        msg = str(e).lower()
        if "not implemented" in msg or "unsupported" in msg or "chown" in msg:
            return False
        raise FilesError("ssh", f"{type(e).__name__}: {e}"[:240]) from e


def apply_perms(
    server: Any,
    rels: list[str],
    *,
    mode: Optional[str] = None,
    owner: Optional[str] = None,
    group: Optional[str] = None,
    recursive: bool = False,
    role: str = ROLE_FLEET,
    identity: Any = None,
    sftp: Any = None,
    client: Any = None,
) -> dict[str, Any]:
    """chmod and/or chown jail-relative paths.

    Fleet may chmod files it owns (SFTP). Ownership and files you don't own
    require privileged Files; if that identity is not root, ``sudo -n``.
    """
    names = [r.strip().strip("/") for r in (rels or []) if str(r).strip()]
    if not names:
        raise FilesError("invalid", "Nothing selected")
    role = normalize_role(role)
    mode_raw = str(mode or "").strip()
    owner_s = parse_id_name(owner, kind="owner")
    group_s = parse_id_name(group, kind="group")
    mode_i = parse_mode(mode_raw) if mode_raw else None
    if mode_i is None and not owner_s and not group_s:
        raise FilesError("invalid", "Set a mode, owner, or group")
    if (owner_s or group_s) and role != ROLE_PRIVILEGED:
        raise FilesError(
            "privileged_forbidden",
            "Ownership requires privileged Files (Connect as…)",
        )
    allow_sudo = role == ROLE_PRIVILEGED
    used_sudo = False
    changed = 0
    with sftp_session(
        server, identity, sftp, pooled=True, with_client=True, client=client
    ) as pair:
        cli, fs = pair
        as_root = _remote_is_root(cli, server, identity) if allow_sudo else False
        budget = [0]
        targets: list[str] = []
        for rel in names:
            jail, abs_path = resolve_logical(server, rel, role=role, identity=identity)
            targets.extend(
                _walk_perm_targets(
                    fs,
                    server,
                    jail=jail,
                    abs_path=abs_path,
                    role=role,
                    identity=identity,
                    recursive=bool(recursive),
                    depth=0,
                    budget=budget,
                )
            )
        for path in targets:
            if mode_i is not None:
                if not _sftp_chmod(fs, path, mode_i):
                    if not allow_sudo:
                        raise FilesError(
                            "denied",
                            "Permission denied — Connect as privileged to chmod files you do not own",
                        )
                    how = _run_elevated(
                        cli,
                        f"chmod {mode_i:o} -- {shlex.quote(path)}",
                        as_root=as_root,
                        allow_sudo=True,
                    )
                    used_sudo = used_sudo or how == "sudo"
            if owner_s or group_s:
                uid_n = int(owner_s) if owner_s.isdigit() else None
                gid_n = int(group_s) if group_s.isdigit() else None
                sftp_ok = False
                if (not owner_s or owner_s.isdigit()) and (not group_s or group_s.isdigit()):
                    sftp_ok = _sftp_chown(fs, path, uid_n, gid_n)
                if not sftp_ok:
                    spec = f"{owner_s}:{group_s}" if owner_s and group_s else (
                        f"{owner_s}:" if owner_s else f":{group_s}"
                    )
                    how = _run_elevated(
                        cli,
                        f"chown {shlex.quote(spec)} -- {shlex.quote(path)}",
                        as_root=as_root,
                        allow_sudo=True,
                    )
                    used_sudo = used_sudo or how == "sudo"
            changed += 1
    return {
        "changed": changed,
        "sudo": used_sudo,
        "mode": f"{mode_i:o}" if mode_i is not None else "",
        "owner": owner_s,
        "group": group_s,
        "recursive": bool(recursive),
    }


def search(
    server: Any,
    query: str,
    *,
    rel: str = "",
    role: str = ROLE_FLEET,
    identity: Any = None,
    sftp: Any = None,
) -> dict[str, Any]:
    """Case-insensitive name search under ``rel`` (default: current folder)."""
    q = (query or "").strip()
    if not q:
        raise FilesError("invalid", "Type something to search")
    if len(q) > SEARCH_Q_MAX:
        raise FilesError("invalid", "Search is too long")
    needle = q.lower()
    role = normalize_role(role)
    hits: list[dict[str, Any]] = []
    truncated = False
    scanned = 0
    users: dict[int, str] = {}
    groups: dict[int, str] = {}

    def _walk(fs: Any, abs_path: str, child_rel: str, depth: int) -> None:
        nonlocal truncated, scanned
        if truncated or len(hits) >= SEARCH_CAP:
            truncated = True
            return
        if depth > WALK_DEPTH_MAX or scanned >= SEARCH_SCAN_MAX:
            truncated = True
            return
        path = _assert_in_jail(abs_path, server, role=role, identity=identity, sftp=fs)
        st = _lstat(fs, path)
        kind = _kind_from_mode(int(getattr(st, "st_mode", 0) or 0))
        if kind == "link":
            target = _normalize_remote(fs, path)
            if not under_jail(target, jail) or is_denied(target, server, role=role, identity=identity):
                return
            path = target
            st = _lstat(fs, path)
            kind = _kind_from_mode(int(getattr(st, "st_mode", 0) or 0))
        if kind != "dir":
            return
        for name in _listdir_names(fs, path):
            if truncated or len(hits) >= SEARCH_CAP:
                truncated = True
                return
            scanned += 1
            if scanned > SEARCH_SCAN_MAX:
                truncated = True
                return
            child_abs = posix_norm(posixpath.join(path, name))
            rel_child = (child_rel + "/" + name) if child_rel else name
            try:
                cst = _lstat(fs, child_abs)
            except FilesError:
                continue
            ckind = _kind_from_mode(int(getattr(cst, "st_mode", 0) or 0))
            escaped = False
            if ckind == "link":
                try:
                    target = _normalize_remote(fs, child_abs)
                    escaped = (not under_jail(target, jail)) or is_denied(
                        target, server, role=role, identity=identity
                    )
                except Exception:
                    escaped = True
            elif not under_jail(child_abs, jail) or is_denied(
                child_abs, server, role=role, identity=identity
            ):
                escaped = True
            if needle in name.lower():
                uid = getattr(cst, "st_uid", None)
                gid = getattr(cst, "st_gid", None)
                try:
                    uid = int(uid) if uid is not None else None
                except (TypeError, ValueError):
                    uid = None
                try:
                    gid = int(gid) if gid is not None else None
                except (TypeError, ValueError):
                    gid = None
                hits.append(
                    _entry_dict(
                        name=name,
                        rel=rel_child,
                        kind=ckind,
                        size=getattr(cst, "st_size", None),
                        mtime=getattr(cst, "st_mtime", None),
                        escaped=escaped,
                        mode=int(getattr(cst, "st_mode", 0) or 0),
                        uid=uid,
                        gid=gid,
                        users=users,
                        groups=groups,
                    )
                )
            if ckind == "dir" and not escaped:
                _walk(fs, child_abs, rel_child, depth + 1)

    jail, abs_path = resolve_logical(server, rel, role=role, identity=identity)
    with sftp_session(server, identity, sftp, with_client=True) as pair:
        cli, fs = pair
        u, g = user_group_maps(cli, _pool_key(server, identity))
        users.update(u)
        groups.update(g)
        _walk(fs, abs_path, (rel or "").strip("/"), 0)
    crumbs = []
    acc: list[str] = []
    for seg in parse_rel(rel_of(abs_path, jail)):
        acc.append(seg)
        crumbs.append({"name": seg, "rel": "/".join(acc)})
    return {
        "jail": jail,
        "rel": rel_of(abs_path, jail),
        "query": q,
        "truncated": truncated,
        "entries": hits,
        "crumbs": crumbs,
        "search": True,
    }


def move_many(
    server: Any,
    src_rels: list[str],
    dest_dir_rel: str,
    *,
    overwrite: bool = False,
    role: str = ROLE_FLEET,
    identity: Any = None,
    sftp: Any = None,
) -> dict[str, Any]:
    """Move files/folders to another directory in the same jail (SFTP rename)."""
    names = [r.strip().strip("/") for r in (src_rels or []) if str(r).strip()]
    if not names:
        raise FilesError("invalid", "Nothing selected to move")
    role = normalize_role(role)
    moved = 0
    with sftp_session(server, identity, sftp) as fs:
        jail, dest_dir = resolve_logical(server, dest_dir_rel, role=role, identity=identity)
        dest_dir = _assert_in_jail(dest_dir, server, role=role, identity=identity, sftp=fs)
        st = _lstat(fs, dest_dir)
        if _kind_from_mode(int(getattr(st, "st_mode", 0) or 0)) != "dir":
            raise FilesError("is_file", "Destination must be a folder")
        for rel in names:
            _jail, src = resolve_logical(server, rel, role=role, identity=identity)
            src = _assert_in_jail(src, server, role=role, identity=identity, sftp=fs)
            if src == jail or src == "/":
                raise FilesError("denied", "Cannot move the jail root")
            if is_denied(src, server, role=role, identity=identity):
                raise FilesError("denied", "Path is blocked")
            base = src.rsplit("/", 1)[-1]
            dest = posix_norm(posixpath.join(dest_dir, base))
            if not under_jail(dest, jail) or is_denied(dest, server, role=role, identity=identity):
                raise FilesError("escape" if not under_jail(dest, jail) else "denied", "Path is blocked")
            if dest == src:
                continue
            if under_jail(dest_dir, src):
                raise FilesError("denied", "Cannot move a folder into itself")
            existed = False
            try:
                dst_st = _lstat(fs, dest)
                existed = True
                dkind = _kind_from_mode(int(getattr(dst_st, "st_mode", 0) or 0))
                if dkind == "dir":
                    raise FilesError("exists", f"{base}/ already exists in the destination")
                if not overwrite:
                    raise FilesError("exists", f"{base} already exists in the destination")
            except FilesError as e:
                if e.code != "not_found":
                    raise
            try:
                if existed:
                    fs.remove(dest)
                fs.rename(src, dest)
            except FilesError:
                raise
            except Exception as e:
                raise FilesError("ssh", f"{type(e).__name__}: {e}"[:240]) from e
            moved += 1
    return {
        "jail": jail,
        "dest": rel_of(dest_dir, jail),
        "moved": moved,
    }
