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
_active_job_id: dict[str, int] = {}  # hostname -> Job.id (worker feeds DB)
_job_db_last_update: dict[int, float] = {}
_job_details_buffer: dict[int, dict] = {}  # in-worker buffer between DB flushes

# Min seconds between Job.details commits during long rsync runs
_JOB_DB_COMMIT_INTERVAL = 10.0
_PROGRESS_THROTTLE_SEC = 3.0
_MAX_LOG_LINE_LEN = 240
_MAX_LOG_LINES = 15


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

def _truncate_log_line(line: str) -> str:
    line = (line or "").strip()
    if len(line) > _MAX_LOG_LINE_LEN:
        return line[: _MAX_LOG_LINE_LEN - 3] + "..."
    return line


def _is_rsync_progress2_line(line: str) -> bool:
    """--info=progress2 emits one updating status line (xfr#, to-chk=, %, MB/s). Never log these."""
    low = (line or "").lower()
    if "to-chk=" in low or "xfr#" in low:
        return True
    if "%" in line and any(u in low for u in ("mb/s", "kb/s", "gb/s", "bytes/sec", "/s")):
        return True
    return False


def _rsync_line_worth_logging(line: str) -> bool:
    """Only real messages — never progress2 or per-file -v noise."""
    s = _truncate_log_line(line)
    if not s or _is_rsync_progress2_line(s):
        return False
    low = s.lower()
    if any(w in low for w in ("error", "fail", "denied", "warning", "rsync:", "permission")):
        return True
    if s.startswith("Backing up ") or s.startswith("Completed ") or s.startswith("Failed "):
        return True
    if s.startswith("/") or s.startswith("./"):
        return False
    return False


def _merge_progress_buffer(job_id: int, current: str | None, log_line: str | None) -> dict:
    buf = _job_details_buffer.setdefault(
        job_id, {"current": None, "log_lines": [], "last_updated": time.time()}
    )
    if current is not None:
        buf["current"] = current
    if log_line:
        line = _truncate_log_line(log_line)
        if line:
            lines = buf.setdefault("log_lines", [])
            if not lines or lines[-1] != line:
                lines.append(line)
            buf["log_lines"] = lines[-_MAX_LOG_LINES:]
    buf["last_updated"] = time.time()
    return buf


def _flush_job_progress_db(job_id: int, force: bool = False) -> None:
    """Commit buffered progress to Job.details (throttled)."""
    buf = _job_details_buffer.get(job_id)
    if not buf:
        return
    now = time.time()
    last = _job_db_last_update.get(job_id, 0)
    if not force and (now - last) < _JOB_DB_COMMIT_INTERVAL:
        return
    _job_db_last_update[job_id] = now
    try:
        from sqlmodel import Session
        from ..database import engine
        from ..models import Job
        import json as _json

        with Session(engine) as s:
            job = s.get(Job, job_id)
            if not job:
                return
            details = {}
            if job.details:
                try:
                    details = _json.loads(job.details)
                except Exception:
                    pass
            # Preserve metadata (source_filter, started_at, result_summary) — only update progress fields
            details["current"] = buf.get("current")
            details["log_lines"] = list(buf.get("log_lines", []))[-_MAX_LOG_LINES:]
            details["last_updated"] = buf.get("last_updated", now)
            job.details = _json.dumps(details)
            if job.status == "pending":
                job.status = "running"
            if job.started_at is None:
                job.started_at = datetime.utcnow()
            s.add(job)
            s.commit()
    except Exception as e:
        logger.debug(f"[backup] Job.details flush failed for job {job_id}: {e}")


def _update_job_progress_db(job_id: int, current: str | None, log_line: str | None, force: bool = False):
    """Buffer progress in worker memory; flush to DB on interval."""
    _merge_progress_buffer(job_id, current, log_line)
    _flush_job_progress_db(job_id, force=force)


def get_job_backup_progress_from_db(job) -> dict | None:
    """Slim progress read for web poll — avoids parsing huge result_summary when possible."""
    if not job:
        return None
    now = time.time()
    if not job.details:
        if job.status == "pending":
            return {
                "current": "queued",
                "log_lines": ["Waiting for worker…"],
                "last_updated": now,
                "status": job.status,
                "job_id": job.id,
            }
        if job.status == "running":
            return {
                "current": "starting",
                "log_lines": ["Backup starting…"],
                "last_updated": now,
                "status": job.status,
                "job_id": job.id,
            }
        return None
    try:
        details = json.loads(job.details)
    except Exception:
        if job.status in ("pending", "running"):
            return {
                "current": "queued" if job.status == "pending" else "starting",
                "log_lines": ["Waiting for worker…" if job.status == "pending" else "Backup starting…"],
                "last_updated": now,
                "status": job.status,
                "job_id": job.id,
            }
        return None
    return {
        "current": details.get("current"),
        "log_lines": list(details.get("log_lines", []))[-15:],
        "last_updated": details.get("last_updated") or now,
        "status": job.status,
        "job_id": job.id,
        "error": details.get("error"),
    }


def clear_job_progress_buffer(job_id: int | None):
    if job_id:
        _job_details_buffer.pop(job_id, None)
        _job_db_last_update.pop(job_id, None)


def _set_progress(hostname: str, current: str | None = None, log_line: str | None = None, force: bool = False):
    """Update progress — heavily throttled. Job.details is the UI source of truth."""
    now = time.time()
    last = _last_progress_update.get(hostname, 0)

    if hostname not in _backup_progress:
        _backup_progress[hostname] = {"current": None, "log_lines": [], "last_updated": now}

    important_log = False
    if log_line:
        low = log_line.lower()
        important_log = any(
            word in low
            for word in ("error", "fail", "denied", "complete", "finished", "skipped", "warning", "backing up", "still backing", "failed", "preparing")
        )

    if not force and not important_log and (now - last) < _PROGRESS_THROTTLE_SEC:
        return _backup_progress[hostname]

    _last_progress_update[hostname] = now
    p = _backup_progress[hostname]
    if current is not None:
        p["current"] = current
    if log_line and important_log:
        line = _truncate_log_line(log_line)
        if line:
            lines = p.setdefault("log_lines", [])
            if not lines or lines[-1] != line:
                lines.append(line)
            p["log_lines"] = lines[-_MAX_LOG_LINES:]
    p["last_updated"] = now
    _progress_cache[hostname] = (now, p)

    job_id = _active_job_id.get(hostname)
    if job_id:
        _update_job_progress_db(job_id, current, log_line if important_log else None, force=force or important_log)
        return p

    # Legacy Redis path only when no Job (non-Celery fallback)
    r = _get_redis()
    if r:
        try:
            r.set(f"piherder:backup_progress:{hostname}", json.dumps(p), ex=3600)
        except Exception:
            pass
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

# All configured backup sources run rsync via passwordless sudo on the target host.
# Roadmap (SPEC.md § Server onboarding wizard): per-server allow/deny path rules before rsync;
# guided remote setup for key auth, least-privilege backup user + sudoers, and key rotation.
_RSYNC_SUDO = ("sudo", "-n")
_RSYNC_REMOTE_PATH = "sudo -n rsync"


def _remote_rsync_path(client, username: str) -> str:
    """Pick remote --rsync-path: sudo when available, plain rsync for root/HAOS."""
    user = (username or "").strip().lower()
    path_probe = "PATH=/usr/sbin:/usr/bin:/sbin:/bin:/usr/local/bin command -v rsync"

    if user == "root":
        for cmd in (path_probe, "command -v rsync", "which rsync"):
            try:
                status, out, _ = run_command(client, cmd, timeout=10)
                if status == 0 and out.strip():
                    return out.strip().splitlines()[0]
            except Exception:
                pass
        return "rsync"

    probes = (
        (f"sudo -n sh -c '{path_probe}'", True),
        ("sudo -n command -v rsync", True),
        (path_probe, False),
        ("command -v rsync", False),
        ("which rsync", False),
    )
    for cmd, use_sudo in probes:
        try:
            status, out, _ = run_command(client, cmd, timeout=10)
            if status == 0 and out.strip():
                rsync_bin = out.strip().splitlines()[0]
                return f"sudo -n {rsync_bin}" if use_sudo else rsync_bin
        except Exception:
            pass
    return _RSYNC_REMOTE_PATH


def _rsync_error_detail(rsync_stderr: str, rsync_path: str) -> str:
    recent = (rsync_stderr or "")[-1500:].lower()
    if "sudo" in recent and ("not found" in recent or "no such file" in recent):
        return (
            "sudo/rsync not available in non-interactive SSH session. "
            "For HAOS use SSH user root without sudo, or install/configure sudo + rsync PATH."
        )
    if "command not found" in recent or ("rsync" in recent and "not found" in recent):
        if "sudo" in rsync_path.lower():
            return (
                "Remote rsync not found via sudo. HAOS and root SSH often need plain rsync "
                "(PiHerder auto-detects on retry)."
            )
        return "rsync command not found on remote. Install rsync on the target."
    if "permission" in recent or "denied" in recent:
        return (
            "Permission denied on remote. Backups use sudo when available — "
            "configure passwordless sudo for the SSH user, or use root on HAOS."
        )
    if "sudo" in recent and ("password" in recent or "a terminal" in recent):
        return "sudo failed on remote. Allow passwordless sudo (NOPASSWD) for rsync for the SSH user."
    if rsync_stderr.strip():
        return rsync_stderr.strip().splitlines()[-1][:300]
    return "rsync non-zero"


def _source_dir_exists_local(path: str) -> bool:
    """Existence check aligned with sudo-backed rsync."""
    try:
        proc = subprocess.run(
            [*_RSYNC_SUDO, "test", "-d", path],
            capture_output=True,
            timeout=15,
        )
        if proc.returncode == 0:
            return True
    except Exception:
        pass
    return os.path.isdir(path)


def _folder_exists_via_ssh(client, folder: str, username: str) -> bool:
    """Check if a folder exists on the remote host over SSH.

    Prefer sudo (matches rsync). Fall back to plain test for hosts where the SSH
    user is root and sudo -n is unavailable (e.g. some HAOS installs).
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

    if _try_check(True):
        return True
    return _try_check(False)

def backup_source_ok(result: dict) -> bool:
    """True if a single source result is success or intentionally skipped."""
    if result.get("skipped"):
        return True
    if result.get("error"):
        return False
    return int(result.get("rc", 0)) == 0


def backup_succeeded(payload: dict) -> bool:
    """True only when every source completed without rsync/permission errors."""
    if payload.get("error"):
        return False
    results = payload.get("results") or []
    if not results:
        return False
    return all(backup_source_ok(r) for r in results)


def backup_failure_message(payload: dict) -> str:
    """Human-readable reason when backup_succeeded is False."""
    if payload.get("error"):
        return str(payload["error"])[:800]
    for r in payload.get("results") or []:
        if not backup_source_ok(r):
            src = r.get("source", "source")
            if r.get("error"):
                return f"{src}: {r['error']}"
            rc = r.get("rc")
            if rc:
                return f"{src}: rsync exited with code {rc}"
    return "One or more backup sources failed"


def effective_backup_status(status: str, output_snippet: str | dict | None) -> str:
    """Correct legacy rows tagged success when rsync results actually failed."""
    if status != "success" or not output_snippet:
        return status
    data = output_snippet
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            return status
    if isinstance(data, dict) and not backup_succeeded(data):
        return "failed"
    return status


def run_backup(server: Server, user_id: int | None = None, sources_override: Optional[List[dict]] = None, job_id: int | None = None) -> dict:
    """Main entry for a backup job. Replicates original backup_script.sh closely.
    Now supports the richer source format with dest_name and enabled flag.

    rsync is run with delta detection: by default it only transfers files that differ
    in size or modification time (plus --delete for exact mirror). It will skip files
    that already exist and are identical on the destination.

    sources_override: if provided, use this list instead of server.get_backup_sources()
    (used for per-source backup runs without mutating the persisted Server.backup_paths).
    """
    hostname = server.hostname
    if job_id:
        _active_job_id[hostname] = job_id
    sources = sources_override if sources_override is not None else server.get_backup_sources()
    results = []
    backup_root = get_backup_root_for_server(server)
    try:
        backup_root.mkdir(parents=True, exist_ok=True)
    except PermissionError as e:
        _clear_progress(hostname)
        _active_job_id.pop(hostname, None)
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
    remote_rsync_path = _RSYNC_REMOTE_PATH
    if not is_local:
        try:
            ssh_client = get_ssh_client(server)
            remote_rsync_path = _remote_rsync_path(ssh_client, username)
            logger.info(f"[backup] {hostname} remote rsync-path: {remote_rsync_path}")
        except Exception as e:
            _send_webhook(f"Backup failed for {server.hostname}: SSH connection error - {e}")
            _clear_progress(hostname)
            _active_job_id.pop(hostname, None)
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
                # Existence check (sudo-first — same privilege level as rsync below)
                if is_local:
                    exists = _source_dir_exists_local(src)
                else:
                    exists = _folder_exists_via_ssh(ssh_client, src, username)

                if not exists:
                    msg = f"Backup skipped for {server.hostname}: directory {src} does not exist"
                    _send_webhook(msg)
                    results.append({"source": src, "skipped": True, "reason": "missing"})
                    continue

                # Quiet rsync — do NOT read stdout (progress2 floods the worker on large trees).
                rsync_base = ["rsync", "-aHz", "--delete", "--numeric-ids"]
                rc = 1
                rsync_stderr = ""

                def _wait_rsync_quiet(proc: subprocess.Popen, source: str) -> int:
                    """Poll process without parsing rsync output. Heartbeat UI every 10s."""
                    _set_progress(hostname, current=source, log_line=f"Backing up {source}…", force=True)
                    last_ping = time.time()
                    while proc.poll() is None:
                        time.sleep(2)
                        now = time.time()
                        if now - last_ping >= 10:
                            _set_progress(hostname, current=source, log_line=f"Still backing up {source}…")
                            last_ping = now
                    return proc.returncode or 0

                if is_local:
                    cmd = [*_RSYNC_SUDO, *rsync_base, src_rsync, str(dest) + "/"]
                    proc = subprocess.Popen(
                        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
                    )
                    _active_backup_procs[hostname] = proc
                    rc = _wait_rsync_quiet(proc, src)
                    try:
                        rsync_stderr = (proc.stderr.read() or "") if proc.stderr else ""
                    except Exception:
                        pass
                    _active_backup_procs.pop(hostname, None)
                else:
                    rsync_paths_to_try = [remote_rsync_path]
                    if "sudo" in remote_rsync_path.lower():
                        plain = remote_rsync_path.split()[-1] if remote_rsync_path.split() else "rsync"
                        if plain not in rsync_paths_to_try:
                            rsync_paths_to_try.append(plain)
                    with temp_key_file(priv) as key_path:
                        ssh_cmd = _build_rsync_ssh_cmd(key_path)
                        for attempt_path in rsync_paths_to_try:
                            cmd = rsync_base + ["-e", ssh_cmd, "--rsync-path", attempt_path]
                            cmd += [
                                f"{username}@{server.hostname}:{src_rsync}",
                                str(dest) + "/"
                            ]
                            proc = subprocess.Popen(
                                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
                            )
                            _active_backup_procs[hostname] = proc
                            rc = _wait_rsync_quiet(proc, src)
                            try:
                                rsync_stderr = (proc.stderr.read() or "") if proc.stderr else ""
                            except Exception:
                                rsync_stderr = ""
                            _active_backup_procs.pop(hostname, None)
                            if rc == 0:
                                break
                            err_low = (rsync_stderr or "").lower()
                            if attempt_path != rsync_paths_to_try[-1] and (
                                "not found" in err_low or "command not found" in err_low
                            ):
                                _set_progress(
                                    hostname,
                                    log_line=f"Retrying {src} without sudo…",
                                    force=True,
                                )
                                continue
                            break

                if rc == 0:
                    (dest / ".last_backup").touch()
                    # Total size on backup volume (not bytes transferred this run — rsync may skip unchanged files).
                    size = get_dir_size(dest)
                    results.append({
                        "source": src,
                        "dest": str(dest),
                        "rc": rc,
                        "size_bytes": size,
                        "size_human": human_size(size),
                    })
                    _set_progress(hostname, log_line=f"Completed {src}")
                else:
                    error_detail = _rsync_error_detail(rsync_stderr, remote_rsync_path if not is_local else "sudo")
                    _set_progress(hostname, log_line=f"Failed {src}: {error_detail[:120]}", force=True)
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

        success = backup_succeeded({"results": results})
        if success:
            _send_webhook(f"Backup completed for {server.hostname} at {datetime.utcnow()}")
        else:
            err = backup_failure_message({"results": results})
            _send_webhook(f"Backup failed for {server.hostname}: {err[:200]}")

        _clear_progress(hostname)
        jid = _active_job_id.pop(hostname, None)
        if jid:
            _flush_job_progress_db(jid, force=True)
            clear_job_progress_buffer(jid)
        return {
            "server": server.hostname,
            "results": results,
            "ok": success,
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

def get_backup_profiles_db(server: Server) -> List[Dict]:
    """UI-only: build profile list from Server row — no filesystem, no SSH."""
    sources = server.get_backup_sources()
    if not sources:
        sources = [
            {"source": p, "dest_name": None, "enabled": True}
            for p in server.get_backup_paths()
        ]

    root = server.backup_dest_root or settings.BACKUP_ROOT
    folder = server.backup_folder_name or server.hostname.replace("/", "_")
    last = server.last_backup_at

    profiles = []
    for item in sources:
        src = item["source"]
        dest_name = item.get("dest_name") or Path(src).name or "root"
        profiles.append({
            "source": src,
            "dest_name": dest_name,
            "destination": f"{root}/{folder}/{dest_name}",
            "enabled": item.get("enabled", True),
            "last_backup": last,
            "last_backup_str": format_datetime_in_app_tz(last) if last else "Never",
            "folder_name": dest_name,
        })
    return profiles


def global_backup_defaults_from_server(server: Server) -> dict:
    """DB/config only — no JSON file read on web."""
    return {
        "dest_root": server.backup_dest_root or settings.BACKUP_ROOT,
        "folder_name": server.backup_folder_name or server.hostname.replace("/", "_"),
        "sources": [p.get("source") for p in server.get_backup_sources()] or server.get_backup_paths(),
    }


def get_backup_profiles(server: Server, skip_fs: bool = False) -> List[Dict]:
    """Return rich backup profile info for UI."""
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
        last = None if skip_fs else get_last_backup_time_for_dest(server.hostname, dest_name)
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
    """Fast size via du — avoid rglob walk on large backup trees."""
    try:
        proc = subprocess.run(
            ["du", "-sb", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return int(proc.stdout.split()[0])
    except Exception:
        pass
    return 0

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

