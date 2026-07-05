from ..models import Server
from ..services.ssh import get_private_key_plain, temp_key_file, LEGACY_SSH_OPTS_STR, get_ssh_client, run_command
import subprocess
import os
import json
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional
from ..config import settings
import httpx
import logging
import os
import threading
import shlex
import time
import traceback

# Use app selected TZ for display strings
from .herder_backup import format_datetime_in_app_tz

logger = logging.getLogger(__name__)

_active_backup_procs: dict[str, subprocess.Popen] = {}
_backup_locks: dict[int, threading.Lock] = {}

_redis_client = None

_last_progress_update: dict[str, float] = {}
_progress_cache: dict[str, tuple[float, dict]] = {}   # (timestamp, data) for lightweight caching


GLOBAL_BACKUP_DEFAULTS_FILE = Path(settings.BACKUP_ROOT) / ".global_backup_defaults.json"

def get_global_backup_defaults() -> dict:
    """Stub: return global defaults from file or empty."""
    try:
        if GLOBAL_BACKUP_DEFAULTS_FILE.exists():
            return json.loads(GLOBAL_BACKUP_DEFAULTS_FILE.read_text())
    except Exception:
        pass
    return {}

def save_global_backup_defaults(config: dict):
    """Stub: persist global defaults to file."""
    try:
        GLOBAL_BACKUP_DEFAULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        GLOBAL_BACKUP_DEFAULTS_FILE.write_text(json.dumps(config, indent=2))
    except Exception:
        pass


def _get_redis():
    """Return a Redis client (shared with Celery) or None if unavailable."""
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    try:
        import redis
        url = (
            os.getenv("CELERY_BROKER_URL")
            or os.getenv("CELERY_RESULT_BACKEND")
            or "redis://localhost:6379/0"
        )
        client = redis.from_url(url, decode_responses=True)
        client.ping()
        _redis_client = client
        logger.debug("[backup] Using Redis for cross-process progress tracking")
    except Exception as e:
        logger.debug(f"[backup] Redis not available for progress, using in-memory only: {e}")
        _redis_client = None
    return _redis_client


def _get_backup_lock(server_id: int) -> threading.Lock:
    if server_id not in _backup_locks:
        _backup_locks[server_id] = threading.Lock()
    return _backup_locks[server_id]


def is_backup_running(hostname: str) -> bool:
    return hostname in _active_backup_procs


def stop_backup(hostname: str):
    proc = _active_backup_procs.get(hostname)
    if proc:
        try:
            proc.terminate()
            _set_progress(hostname, log_line="[STOPPED by user]")
        except Exception:
            pass


# Progress tracking - now Redis-backed when available (so Celery worker updates are visible to web UI)
# Falls back to in-memory dict when Redis is unreachable.
_backup_progress: dict[str, dict] = {}  # in-memory fallback

def get_backup_progress(hostname: str) -> dict:
    """Return current backup progress for UI polling / SSE.
    Always includes 'last_updated' (unix timestamp).
    Uses 2s in-process cache so repeated polls (every 2-3s) are almost free.
    Tries Redis first for cross-process visibility (Celery → web).
    """
    now = time.time()

    # Lightweight in-process cache (2s TTL) - makes frequent polling from frontend very cheap
    if hostname in _progress_cache:
        ts, data = _progress_cache[hostname]
        if now - ts < 2.0:
            return data

    r = _get_redis()
    if r:
        try:
            data = r.get(f"piherder:backup_progress:{hostname}")
            if data:
                parsed = json.loads(data)
                if "last_updated" not in parsed:
                    parsed["last_updated"] = now
                _progress_cache[hostname] = (now, parsed)
                return parsed
        except Exception:
            pass

    # Fallback to memory
    data = _backup_progress.get(hostname, {"current": None, "log_lines": [], "last_updated": now})
    if "last_updated" not in data:
        data["last_updated"] = now
    _progress_cache[hostname] = (now, data)
    return data

def _set_progress(hostname: str, current: str | None = None, log_line: str | None = None):
    """Update progress with strong throttling for long-running backups.

    - 'current' (which file) updates more frequently for responsive UI feel.
    - Log line appends + Redis writes are throttled (~800ms) unless important (error/complete/etc).
    - Always sets 'last_updated' so frontend can be smart about re-rendering.
    """
    now = time.time()
    last = _last_progress_update.get(hostname, 0)

    # Always keep in-memory 'current' fresh
    if hostname not in _backup_progress:
        _backup_progress[hostname] = {"current": None, "log_lines": [], "last_updated": now}
    if current is not None:
        _backup_progress[hostname]["current"] = current

    # Decide whether to do the expensive work (Redis write + log append)
    force = False
    if log_line:
        low = log_line.lower()
        if any(word in low for word in ("error", "fail", "denied", "complete", "finished", "skipped", "done", "warning")):
            force = True

    THROTTLE_SECONDS = 0.8
    if not force and (now - last) < THROTTLE_SECONDS:
        # Still update last_updated even on throttled calls so UI knows something is happening
        _backup_progress[hostname]["last_updated"] = now
        return _backup_progress[hostname]

    _last_progress_update[hostname] = now

    r = _get_redis()
    if r:
        try:
            key = f"piherder:backup_progress:{hostname}"
            existing = r.get(key)
            p = json.loads(existing) if existing else {"current": None, "log_lines": [], "last_updated": now}
            if current is not None:
                p["current"] = current
            if log_line:
                p["log_lines"].append(log_line)
                if len(p["log_lines"]) > 40:
                    p["log_lines"] = p["log_lines"][-40:]
            p["last_updated"] = now
            r.set(key, json.dumps(p), ex=3600)
            _progress_cache[hostname] = (now, p)
            return p
        except Exception:
            pass

    # In-memory fallback
    p = _backup_progress[hostname]
    if log_line:
        p["log_lines"].append(log_line)
        if len(p["log_lines"]) > 40:
            p["log_lines"] = p["log_lines"][-40:]
    p["last_updated"] = now
    _progress_cache[hostname] = (now, p)
    return p

def _clear_progress(hostname: str):
    r = _get_redis()
    if r:
        try:
            r.delete(f"piherder:backup_progress:{hostname}")
        except Exception:
            pass
    _backup_progress.pop(hostname, None)
    _last_progress_update.pop(hostname, None)
    _progress_cache.pop(hostname, None)

def _build_rsync_ssh_cmd(key_path: str) -> str:
    return f'ssh -i {key_path} {LEGACY_SSH_OPTS_STR}'

def _send_webhook(message: str):
    """Send notification using same shape as the original backup scripts."""
    if not settings.WEBHOOK_URL:
        return
    try:
        payload = {
            "message": message,
            "number": settings.WEBHOOK_NUMBER or "",
            "recipients": json.loads(settings.WEBHOOK_RECIPIENTS or "[]"),
        }
        httpx.post(settings.WEBHOOK_URL, json=payload, timeout=10)
    except Exception:
        pass  # never break the backup on notification failure

def _path_requires_privilege(path: str) -> bool:
    """Heuristic: does this path typically require root/sudo on the remote host?

    Only for system-internal protected paths (mainly Docker volumes) that
    are almost always root-only on the target.
    """
    if not path:
        return False
    p = path.lower()
    privileged = (
        "/var/lib/docker",
        "/var/lib/containers",
        "/root/",
        "/.docker/",
        "/etc/docker",
    )
    return any(item in p for p in privileged)

def _folder_exists_via_ssh(client, folder: str, username: str) -> bool:
    """Check if a folder exists on the remote host over SSH.

    Strategy:
    - Always try a plain 'test -d' first (works for HAOS /ssl /config, normal homes, etc.)
    - Only fall back to 'sudo -n test -d' for paths that are known to need root
      (Docker volumes etc.). This avoids sudo failures on systems like HAOS
      where the SSH user (often root) doesn't need or can't run sudo -n.
    """
    folder_q = shlex.quote(folder)

    def _try_check(use_sudo: bool) -> bool:
        try:
            if use_sudo:
                cmd = f"sudo -n test -d {folder_q} && echo ok || echo missing"
            else:
                cmd = f"test -d {folder_q} && echo ok || echo missing"
            status, out, err = run_command(client, cmd, timeout=15)
            return "ok" in (out or "").lower()
        except Exception:
            return False

    # 1. Try direct (no sudo) — this is what works on most non-Docker paths and HAOS
    if _try_check(False):
        return True

    # 2. Only try sudo for paths we expect to need privilege
    if _path_requires_privilege(folder):
        if _try_check(True):
            return True

    return False

def run_backup(server: Server, user_id: int | None = None, sources_override: Optional[List[dict]] = None) -> dict:
    """Main entry for a backup job. Replicates original backup_script.sh closely.
    Now supports the richer source format with dest_name and enabled flag.

    rsync is run with delta detection: by default it only transfers files that differ
    in size or modification time (plus --delete for exact mirror). It will skip files
    that already exist and are identical on the destination.

    sources_override: if provided, use this list instead of server.get_backup_sources()
    (used for per-source backup runs without mutating the persisted Server.backup_paths).
    """
    hostname = server.hostname
    sources = sources_override if sources_override is not None else server.get_backup_sources()
    results = []
    backup_root = get_backup_root_for_server(server)
    try:
        backup_root.mkdir(parents=True, exist_ok=True)
    except PermissionError as e:
        _clear_progress(hostname)
        return {
            "server": hostname,
            "error": f"Permission denied creating {backup_root} (errno 13). "
                     "The destination root must be a directory writable by the container user (piherder). "
                     "Use a bind mount in docker-compose.yml, e.g. - /home/bjorn/backup:/backups , "
                     "and set 'Default destination path root' to /backups in the UI. "
                     f"Original error: {e}",
            "results": []
        }

    _set_progress(hostname, current="preparing")

    priv = get_private_key_plain(server)
    username = server.ssh_username

    # Detect if we're running inside a container (e.g. the PiHerder Docker container).
    # Inside the container, "local" mode would see the container's filesystem, not the host's.
    # For the machine running PiHerder, we recommend adding it as a regular Server
    # using its real hostname/IP + SSH ( "ssh out and back in" ). This keeps
    # folder mapping simple — no extra bind-mounts for sources are needed.
    # Sources in the config use normal host paths; they are read on the host side via SSH.
    def _running_in_container() -> bool:
        try:
            if os.path.exists("/.dockerenv"):
                return True
            with open("/proc/1/cgroup", "r") as f:
                if any(x in f.read() for x in ("docker", "kubepods", "containerd", "lxc")):
                    return True
        except Exception:
            pass
        return False

    is_local = (
        (server.hostname in ("localhost", "127.0.0.1")
         or server.hostname == os.uname().nodename)
        and not _running_in_container()
    )

    ssh_client = None
    if not is_local:
        try:
            ssh_client = get_ssh_client(server)
        except Exception as e:
            _send_webhook(f"Backup failed for {server.hostname}: SSH connection error - {e}")
            _clear_progress(hostname)
            return {"server": server.hostname, "error": str(e), "results": []}

    lock = _get_backup_lock(server.id)
    with lock:
        for item in sources:
            if not item.get("enabled", True):
                continue

            src = item["source"]
            dest_name = item.get("dest_name") or Path(src).name or "root"
            dest = backup_root / dest_name

            # Normalize source to always end with / so we copy *contents* into the named dest folder.
            # This ensures correct rsync behavior (delta detection + no unwanted extra nesting).
            src_rsync = src.rstrip("/") + "/"
            try:
                dest.mkdir(parents=True, exist_ok=True)
            except PermissionError as e:
                _set_progress(hostname, log_line=f"Permission error on {dest}")
                results.append({"source": src, "error": f"Permission denied creating {dest}: {e}"})
                continue
            _set_progress(hostname, current=src, log_line=f"Backing up {src}")

            try:
                # Existence check
                if is_local:
                    if _path_requires_privilege(src):
                        exists = subprocess.call(["sudo", "-n", "test -d", src]) == 0
                    else:
                        exists = os.path.isdir(src)
                else:
                    exists = _folder_exists_via_ssh(ssh_client, src, username)

                if not exists:
                    msg = f"Backup skipped for {server.hostname}: directory {src} does not exist"
                    _send_webhook(msg)
                    results.append({"source": src, "skipped": True, "reason": "missing"})
                    continue

                rsync_base = ["rsync", "-aHvz", "--delete", "--numeric-ids", "-P"]
                rc = 1

                if is_local:
                    cmd = rsync_base.copy()
                    if _path_requires_privilege(src):
                        cmd = ["sudo", "-n"] + cmd
                    cmd += [src_rsync, str(dest) + "/"]
                    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                    _active_backup_procs[hostname] = proc
                    for line in iter(proc.stdout.readline, ''):
                        if line:
                            line = line.strip()
                            _set_progress(hostname, current=src, log_line=line)
                    rc = proc.wait()
                    _active_backup_procs.pop(hostname, None)
                else:
                    with temp_key_file(priv) as key_path:
                        ssh_cmd = _build_rsync_ssh_cmd(key_path)
                        cmd = rsync_base + ["-e", ssh_cmd]
                        if _path_requires_privilege(src):
                            cmd += ["--rsync-path", "sudo -n rsync"]
                        cmd += [
                            f"{username}@{server.hostname}:{src_rsync}",
                            str(dest) + "/"
                        ]
                        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                        _active_backup_procs[hostname] = proc
                        for line in iter(proc.stdout.readline, ''):
                            if line:
                                line = line.strip()
                                _set_progress(hostname, current=src, log_line=line)
                        rc = proc.wait()
                        _active_backup_procs.pop(hostname, None)

                if rc == 0:
                    (dest / ".last_backup").touch()
                    size = get_dir_size(dest)
                    results.append({
                        "source": src,
                        "dest": str(dest),
                        "rc": rc,
                        "size_bytes": size,
                        "size_human": human_size(size)
                    })
                    _set_progress(hostname, log_line=f"Completed {src}")
                else:
                    # Provide better diagnostics for common remote issues (HAOS, minimal systems, etc.)
                    error_detail = "rsync non-zero"
                    try:
                        prog = get_backup_progress(hostname)
                        lines = prog.get("log_lines", [])
                        recent = " ".join(lines[-10:])
                        if "command not found" in recent.lower() or ("rsync" in recent.lower() and "not found" in recent.lower()):
                            error_detail = "rsync command not found on remote. Install rsync on the target (via SSH add-on on HAOS etc.)"
                        elif "permission" in recent.lower() or "denied" in recent.lower():
                            error_detail = "Permission denied on remote. Check SSH user permissions or try sudo for protected paths."
                        else:
                            err_lines = [l for l in lines[-10:] if l and any(k in l.lower() for k in ("error", "rsync:", "failed", "protocol", "closed", "bash:"))]
                            if err_lines:
                                error_detail = err_lines[-1]
                    except Exception:
                        pass
                    results.append({"source": src, "rc": rc, "error": error_detail})
                    _send_webhook(f"Backup failed for {server.hostname}: rsync error on {src}")
                    _set_progress(hostname, log_line=f"Failed {src}")

            except Exception as e:
                logger.error(f"[backup] Error on source {src}: {e}\n{traceback.format_exc()}")
                results.append({"source": src, "error": str(e)})
                _send_webhook(f"Backup error for {server.hostname} on {src}: {e}")
                _set_progress(hostname, log_line=f"Error on {src}: {str(e)[:100]}")

        if ssh_client:
            ssh_client.close()

        success = all(r.get("rc") == 0 or r.get("skipped") for r in results)
        if success:
            _send_webhook(f"Backup completed for {server.hostname} at {datetime.utcnow()}")

        _clear_progress(hostname)
        return {
            "server": server.hostname,
            "results": results,
            "timestamp": datetime.utcnow().isoformat(),
        }

def run_retention(server: Server) -> dict:
    """Basic retention (matches spirit of backup_cleanup.sh). Full prune logic can be expanded."""
    paths = server.get_backup_paths()
    backup_root = get_backup_root_for_server(server)
    pruned = []

    for folder in paths:
        folder_name = Path(folder).name or "root"
        dest = backup_root / folder_name
        marker = dest / ".last_backup"
        if not marker.exists():
            continue

        # Very simplified: just touch a note. Real version would do dry-run rsync + rm + find -empty
        # For now we keep the marker logic and note the intent.
        pruned.append(folder_name)

    return {
        "server": server.hostname,
        "pruned_folders": pruned,
        "note": "Retention stub. Expand with dry-run rsync + file deletion to match cleanup script exactly."
    }


# === Backup Profiles / Flexibility helpers ===

def get_backup_profiles(server: Server) -> List[Dict]:
    """Return rich backup profile info for UI.
    Includes timing logs for diagnostics (Phase 1).
    """
    import time
    start = time.time()

    sources = server.get_backup_sources()
    if not sources:
        g = get_global_backup_defaults()
        gsrc = g.get("sources", []) if isinstance(g, dict) else g
        if gsrc:
            sources = [{"source": s, "dest_name": None, "enabled": True} for s in gsrc]

    profiles = []

    for item in sources:
        src = item["source"]
        dest_name = item.get("dest_name") or Path(src).name or "root"
        enabled = item.get("enabled", True)
        dest = str(get_backup_root_for_server(server) / dest_name)
        last = get_last_backup_time_for_dest(server.hostname, dest_name)
        profiles.append({
            "source": src,
            "dest_name": dest_name,
            "destination": dest,
            "enabled": enabled,
            "last_backup": last,
            "last_backup_str": format_datetime_in_app_tz(last) if last else "Never",
            "folder_name": dest_name
        })

    took = time.time() - start
    if took > 0.8:
        logger.warning(f"[get_backup_profiles] Slow for {server.hostname}: {took:.2f}s")
    else:
        logger.debug(f"[get_backup_profiles] {server.hostname} took {took:.2f}s")

    return profiles

def get_last_backup_time_for_dest(hostname: str, dest_name: str) -> Optional[datetime]:
    """Check .last_backup for a specific dest subfolder.
    mtime is stored as unix time; we treat it as UTC for consistent display under selected TZ.
    Phase 1: Added timing + warning log for slow FS operations.
    """
    import time
    start = time.time()

    dest = Path(get_backup_root_for_server(hostname)) / dest_name
    marker = dest / ".last_backup"
    result = None
    if marker.exists():
        try:
            ts = marker.stat().st_mtime
            result = datetime.utcfromtimestamp(ts)
        except Exception:
            pass

    took = time.time() - start
    if took > 0.5:
        logger.warning(f"[get_last_backup_time] Slow FS check for {hostname}/{dest_name}: {took:.2f}s")

    return result

def get_backup_root_for_server(server_or_hostname) -> Path:
    """Return the root path for backups.
    Accepts Server object (for per-host overrides) or hostname str.
    """
    if isinstance(server_or_hostname, Server):
        s = server_or_hostname
        g = get_global_backup_defaults()
        root = s.backup_dest_root or g.get("dest_root") or settings.BACKUP_ROOT
        folder = s.backup_folder_name or g.get("folder_name") or s.hostname.replace("/", "_")
        return Path(root) / folder
    else:
        hostname = server_or_hostname
        g = get_global_backup_defaults()
        folder = g.get("folder_name") or hostname.replace("/", "_")
        root = g.get("dest_root") or settings.BACKUP_ROOT
        return Path(root) / folder

def get_destination_for_source(hostname: str, source: str) -> str:
    """Compute the destination folder name (matches original rsync behavior)."""
    folder_name = Path(source).name or "root"
    root = get_backup_root_for_server(hostname)
    return str(root / folder_name)

def get_last_backup_time(hostname: str, source: str) -> Optional[datetime]:
    """Read the .last_backup marker written by the backup script (most accurate, matches legacy scripts)."""
    dest = Path(get_destination_for_source(hostname, source))
    marker = dest / ".last_backup"
    if marker.exists():
        try:
            mtime = marker.stat().st_mtime
            return datetime.fromtimestamp(mtime)
        except Exception:
            return None
    return None

def get_dir_size(path: Path) -> int:
    """Calculate total size of directory in bytes."""
    total = 0
    try:
        for entry in path.rglob('*'):
            if entry.is_file():
                total += entry.stat().st_size
    except Exception:
        pass
    return total

def human_size(num_bytes: int) -> str:
    """Convert a byte count to a human-readable string (KB/MB/GB etc.)."""
    if num_bytes <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    unit_index = 0
    size = float(num_bytes)
    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1
    if unit_index == 0:
        return f"{int(size)} {units[unit_index]}"
    return f"{size:.1f} {units[unit_index]}"

def add_backup_source(server: Server, source: str, dest_name: Optional[str] = None, session=None) -> bool:
    """Add a new flexible backup source. Supports custom dest folder name."""
    if not source or not source.strip():
        return False
    sources = server.get_backup_sources()
    source = source.strip()
    # check for duplicate source
    if any(s["source"] == source for s in sources):
        return False
    sources.append({
        "source": source,
        "dest_name": dest_name.strip() if dest_name else None,
        "enabled": True
    })
    server.set_backup_sources(sources)
    if hasattr(server, 'backup_paths'):
        server.backup_paths = json.dumps(sources)
    if session:
        session.add(server)
        session.commit()
    return True

def remove_backup_source(server: Server, source: str, session=None) -> bool:
    sources = server.get_backup_sources()
    original_len = len(sources)
    sources = [s for s in sources if s["source"] != source]
    if len(sources) == original_len:
        return False
    if hasattr(server, 'backup_paths'):
        server.backup_paths = json.dumps(sources)
    if session:
        session.add(server)
        session.commit()
    return True


# Legacy compat shims (used by older code)
def add_backup_path(server: Server, new_path: str, session) -> bool:
    return add_backup_source(server, new_path, None, session)

def remove_backup_path(server: Server, path_to_remove: str, session) -> bool:
    return remove_backup_source(server, path_to_remove, session)

