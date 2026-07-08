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

# Progress tracking extracted to backup_progress.py for maintainability.
# We re-export the public names so that existing imports continue to work unchanged:
#   from app.services.backup import get_backup_progress, _flush_job_progress_db, ...
from . import backup_progress
from . import backup_profiles

# Re-export for backward compatibility (tasks.py, jobs.py, routers via backup_svc alias, etc.)
get_backup_progress = backup_progress.get_backup_progress
get_job_backup_progress_from_db = backup_progress.get_job_backup_progress_from_db
clear_job_progress_buffer = backup_progress.clear_job_progress_buffer
_set_progress = backup_progress._set_progress
_clear_progress = backup_progress._clear_progress
_flush_job_progress_db = backup_progress._flush_job_progress_db
_update_job_progress_db = backup_progress._update_job_progress_db
_merge_progress_buffer = backup_progress._merge_progress_buffer
_truncate_log_line = backup_progress._truncate_log_line
_is_rsync_progress2_line = backup_progress._is_rsync_progress2_line
_rsync_line_worth_logging = backup_progress._rsync_line_worth_logging
_active_job_id = backup_progress._active_job_id  # direct dict access still works in run_backup

# Profiles / source management re-exports
get_global_backup_defaults = backup_profiles.get_global_backup_defaults
save_global_backup_defaults = backup_profiles.save_global_backup_defaults
get_backup_profiles_db = backup_profiles.get_backup_profiles_db
global_backup_defaults_from_server = backup_profiles.global_backup_defaults_from_server
get_backup_profiles = backup_profiles.get_backup_profiles
get_last_backup_time_for_dest = backup_profiles.get_last_backup_time_for_dest
get_backup_root_for_server = backup_profiles.get_backup_root_for_server
get_destination_for_source = backup_profiles.get_destination_for_source
get_last_backup_time = backup_profiles.get_last_backup_time
get_dir_size = backup_profiles.get_dir_size
human_size = backup_profiles.human_size
add_backup_source = backup_profiles.add_backup_source
remove_backup_source = backup_profiles.remove_backup_source
add_backup_path = backup_profiles.add_backup_path
remove_backup_path = backup_profiles.remove_backup_path

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


# (progress functions moved to backup_progress.py; re-exported at top of this file)

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
# SSH onboarding (deploy key / least-priv user / rotate): app/services/ssh_onboarding.py.
# Still open (SPEC): per-server backup path allow/deny rules before rsync.
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


# (profile/source helpers moved to backup_profiles.py; re-exported at top of file)

