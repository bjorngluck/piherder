"""Copy checked paths from the local backup drive to a fleet destination.

Path A: rclone on the herder, after the rsync mirror exists. The token stays
Fernet-encrypted. Selection is checked paths plus skipped children, never a
typed glob from the operator.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from ..config import settings
from ..models import BackupDestination, Job, Server
from ..security.encryption import decrypt_str, encrypt_str

logger = logging.getLogger(__name__)

JOB_TYPE = "backup_replicate"
_REMOTE_NAME = "dest"


def backup_root() -> Path:
    return Path(settings.BACKUP_ROOT or "/backups").resolve()


def parse_selection(raw: str | None) -> tuple[list[str], list[str]]:
    try:
        data = json.loads(raw or "{}")
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    checked = [str(p) for p in (data.get("checked") or []) if str(p).strip()]
    skipped = [str(p) for p in (data.get("skipped") or []) if str(p).strip()]
    return normalize_selection(checked, skipped)


def selection_json(checked: list[str], skipped: list[str]) -> str:
    checked, skipped = normalize_selection(checked, skipped)
    return json.dumps({"checked": checked, "skipped": skipped})


def _clean_rel(path: str) -> str:
    text = (path or "").strip().replace("\\", "/").lstrip("/")
    parts = [p for p in text.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ValueError("path escapes the backup drive")
    return "/".join(parts)


def _under(child: str, parent: str) -> bool:
    return child == parent or child.startswith(parent.rstrip("/") + "/")


def normalize_selection(
    checked: list[str], skipped: list[str]
) -> tuple[list[str], list[str]]:
    clean_checked: list[str] = []
    for raw in checked:
        try:
            rel = _clean_rel(raw)
        except ValueError:
            continue
        if rel and rel not in clean_checked:
            clean_checked.append(rel)
    kept: list[str] = []
    for path in clean_checked:
        if any(_under(path, other) and path != other for other in clean_checked):
            continue
        kept.append(path)
    clean_skipped: list[str] = []
    for raw in skipped:
        try:
            rel = _clean_rel(raw)
        except ValueError:
            continue
        if not rel or rel in kept:
            continue
        if not any(_under(rel, parent) for parent in kept):
            continue
        clean_skipped.append(rel)
    final_skip: list[str] = []
    for path in clean_skipped:
        if any(_under(path, other) and path != other for other in clean_skipped):
            continue
        final_skip.append(path)
    return kept, final_skip


def apply_toggle(
    checked: list[str],
    skipped: list[str],
    path: str,
    on: bool,
) -> tuple[list[str], list[str]]:
    rel = _clean_rel(path)
    if not rel:
        raise ValueError("Choose a folder or file inside the backup drive")
    checked, skipped = normalize_selection(checked, skipped)
    if on:
        skipped = [s for s in skipped if s != rel and not _under(s, rel)]
        checked = [c for c in checked if not _under(c, rel)]
        if not any(_under(rel, parent) for parent in checked):
            checked.append(rel)
    else:
        checked = [c for c in checked if c != rel and not _under(c, rel)]
        skipped = [s for s in skipped if s != rel and not _under(s, rel)]
        if any(_under(rel, parent) for parent in checked):
            skipped.append(rel)
    return normalize_selection(checked, skipped)


def path_included(path: str, checked: list[str], skipped: list[str]) -> bool:
    rel = _clean_rel(path) if path else ""
    if not rel:
        return False
    if any(rel == s or _under(rel, s) for s in skipped):
        return False
    return any(rel == c or _under(rel, c) for c in checked)


def selection_state(path: str, checked: list[str], skipped: list[str]) -> str:
    """on, off, or partial. A checked folder stays on for everything inside it."""
    rel = ""
    if path:
        try:
            rel = _clean_rel(path)
        except ValueError:
            return "off"
    if not rel:
        return "partial" if checked else "off"
    if path_included(rel, checked, skipped):
        if any(_under(skip, rel) and skip != rel for skip in skipped):
            return "partial"
        return "on"
    if any(_under(item, rel) and item != rel for item in checked):
        return "partial"
    return "off"


def resolve_under_root(rel: str) -> Path:
    root = backup_root()
    cleaned = _clean_rel(rel)
    target = (root / cleaned).resolve() if cleaned else root
    if target != root and root not in target.parents:
        raise ValueError("path escapes the backup drive")
    return target


def list_directory(rel: str) -> list[dict[str, Any]]:
    folder = resolve_under_root(rel)
    if not folder.is_dir():
        raise FileNotFoundError(rel or "/")
    rows: list[dict[str, Any]] = []
    try:
        children = sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError as exc:
        raise FileNotFoundError(str(exc)) from exc
    parent = _clean_rel(rel)
    from .backup_profiles import human_size

    for child in children:
        if child.is_symlink():
            continue
        try:
            st = child.stat()
        except OSError:
            continue
        name = child.name
        child_rel = f"{parent}/{name}" if parent else name
        is_dir = child.is_dir()
        rows.append(
            {
                "name": name,
                "path": child_rel,
                "is_dir": is_dir,
                "size": int(st.st_size),
                "size_h": "" if is_dir else human_size(int(st.st_size)),
                "mtime": int(st.st_mtime),
            }
        )
    return rows


def _remote_dir(destination: BackupDestination) -> str:
    try:
        cfg = json.loads(destination.config_json or "{}")
    except Exception:
        cfg = {}
    name = str((cfg or {}).get("remote_dir") or "PiHerder").strip().strip("/")
    return name or "PiHerder"


def sync_plan(
    checked: list[str], skipped: list[str]
) -> list[tuple[str, list[str], bool]]:
    """Return (relative path, excludes relative to that path, is_dir-shaped).

    A checked path that is a prefix of another is the directory sync. File
    copies are checked paths that the caller marks with a trailing flag via
    ``file_paths``. This helper treats every checked path as a directory sync
    and every skipped descendant as an exclude. Callers pass files separately.
    """
    plan: list[tuple[str, list[str], bool]] = []
    for folder in checked:
        excludes: list[str] = []
        prefix = folder.rstrip("/") + "/"
        for skip in skipped:
            if skip.startswith(prefix):
                excludes.append(skip[len(prefix):])
        plan.append((folder, excludes, True))
    return plan


def rclone_sync_cmd(
    config_path: str,
    local_dir: str,
    remote_path: str,
    excludes: list[str],
) -> list[str]:
    cmd = [
        "rclone",
        "sync",
        local_dir,
        f"{_REMOTE_NAME}:{remote_path}",
        "--config",
        config_path,
        "--drive-use-trash",
        "--stats-one-line",
        "--stats",
        "0",
    ]
    for rel in excludes:
        pattern = rel.rstrip("/")
        if not pattern or pattern.startswith("/") or ".." in pattern.split("/"):
            continue
        cmd.extend(["--exclude", pattern if "." in Path(pattern).name else f"{pattern}/**"])
    return cmd


def rclone_copy_file_cmd(config_path: str, local_file: str, remote_path: str) -> list[str]:
    return [
        "rclone",
        "copyto",
        local_file,
        f"{_REMOTE_NAME}:{remote_path}",
        "--config",
        config_path,
        "--drive-use-trash",
        "--stats-one-line",
        "--stats",
        "0",
    ]


def encrypt_account(email: str, private_key: str) -> str:
    return encrypt_str(pack_service_account(email, private_key))


def pack_service_account(email: str, private_key: str) -> str:
    account = (email or "").strip()
    key = (private_key or "").strip().replace("\r\n", "\n")
    if "@" not in account or any(ch.isspace() for ch in account):
        raise ValueError("email")
    if "BEGIN" not in key or "PRIVATE KEY" not in key or "END" not in key:
        raise ValueError("key")
    return json.dumps(
        {"auth": "service_account", "client_email": account, "private_key": key}
    )


def write_rclone_config(token_json: str, *, shared_with_me: bool = False) -> str:
    payload = json.loads(token_json)
    if not isinstance(payload, dict):
        raise ValueError("Google account details are missing")
    lines = [f"[{_REMOTE_NAME}]", "type = drive", "scope = drive"]
    if payload.get("private_key") and payload.get("client_email"):
        creds = {
            "type": "service_account",
            "client_email": payload["client_email"],
            "private_key": payload["private_key"],
            "token_uri": "https://oauth2.googleapis.com/token",
        }
        lines.append(
            "service_account_credentials = " + json.dumps(creds, separators=(",", ":"))
        )
    elif payload.get("refresh_token") or payload.get("access_token"):
        lines.append("token = " + json.dumps(payload, separators=(",", ":")))
    else:
        raise ValueError("Google account details are missing")
    if shared_with_me or payload.get("shared_with_me"):
        lines.append("shared_with_me = true")
    handle = tempfile.NamedTemporaryFile("w", prefix="ph-rclone-", suffix=".conf", delete=False)
    try:
        handle.write("\n".join(lines) + "\n")
        handle.flush()
        os.chmod(handle.name, 0o600)
    finally:
        handle.close()
    return handle.name


def encrypt_token(token_json: str) -> str:
    payload = json.loads(token_json)
    if not isinstance(payload, dict):
        raise ValueError("Drive token must be JSON")
    if not (payload.get("refresh_token") or payload.get("access_token")):
        raise ValueError("Drive token must be the JSON from rclone authorize drive")
    return encrypt_str(json.dumps(payload))


def decrypt_token(destination: BackupDestination) -> str:
    raw = destination.credentials_encrypted or ""
    if not raw:
        raise ValueError("No Google account saved")
    return decrypt_str(raw)


def credential_public(destination: BackupDestination) -> dict[str, Any]:
    """Email and kind for the form. Never the private key."""
    if not (destination.credentials_encrypted or "").strip():
        return {"saved": False, "email": "", "kind": ""}
    try:
        data = json.loads(decrypt_token(destination))
    except Exception:
        return {"saved": True, "email": "", "kind": "saved"}
    if not isinstance(data, dict):
        return {"saved": True, "email": "", "kind": "saved"}
    if data.get("client_email"):
        return {
            "saved": True,
            "email": str(data["client_email"]),
            "kind": "service_account",
        }
    if data.get("refresh_token") or data.get("access_token"):
        return {"saved": True, "email": "", "kind": "connected"}
    return {"saved": True, "email": "", "kind": "saved"}


def drive_folder(destination: BackupDestination) -> str:
    return _remote_dir(destination)


def shared_with_me(destination: BackupDestination) -> bool:
    try:
        cfg = json.loads(destination.config_json or "{}")
    except Exception:
        cfg = {}
    return bool(isinstance(cfg, dict) and cfg.get("shared_with_me"))


def clean_drive_folder(value: str) -> str:
    name = (value or "").strip().strip("/")
    if not name:
        return "PiHerder"
    parts = [part for part in name.split("/") if part and part != "."]
    if any(part == ".." for part in parts):
        raise ValueError("folder")
    return "/".join(parts) or "PiHerder"


def host_folder_name(server: Server) -> str:
    folder = (server.backup_folder_name or server.hostname or "").replace("/", "_").strip()
    return folder


def paths_for_scope(
    checked: list[str], skipped: list[str], scope: str | None
) -> tuple[list[str], list[str]]:
    if not scope:
        return checked, skipped
    prefix = scope.strip().strip("/")
    if not prefix:
        return checked, skipped
    scoped_checked = [p for p in checked if p == prefix or _under(p, prefix) or _under(prefix, p)]
    # If the host folder itself is checked, keep it. If only a child is checked, keep the child.
    # If a parent above the host is checked, narrow the sync to the host folder.
    narrowed: list[str] = []
    for path in scoped_checked:
        if _under(prefix, path) and path != prefix:
            narrowed.append(prefix)
        else:
            narrowed.append(path)
    scoped_skip = [p for p in skipped if p == prefix or _under(p, prefix) or any(_under(p, c) for c in narrowed)]
    return normalize_selection(narrowed, scoped_skip)


def _run_rclone(cmd: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=None)
    except FileNotFoundError:
        return 127, "rclone is not installed in this image"
    err = (proc.stderr or proc.stdout or "").strip()
    if "token" in err.lower() and "ya29" in err:
        err = "rclone failed (token redacted)"
    return proc.returncode, err[-2000:]


def execute(destination: BackupDestination, scope: str | None = None) -> dict[str, Any]:
    from .demo import demo_mode

    if demo_mode():
        return {"ok": False, "error": "Demo does not upload"}
    if destination.provider != "drive":
        return {"ok": False, "error": f"Provider {destination.provider} is not built"}
    checked, skipped = parse_selection(destination.selection_json)
    checked, skipped = paths_for_scope(checked, skipped, scope)
    if not checked:
        return {"ok": False, "error": "Nothing selected on the backup drive"}
    config_path = ""
    try:
        config_path = write_rclone_config(
            decrypt_token(destination),
            shared_with_me=shared_with_me(destination),
        )
        remote_root = _remote_dir(destination)
        errors: list[str] = []
        copied: list[str] = []
        for rel, excludes, _is_dir in sync_plan(checked, skipped):
            local = resolve_under_root(rel)
            if not local.exists():
                errors.append(f"{rel} is not on the backup drive")
                continue
            remote = f"{remote_root}/{rel}"
            if local.is_dir():
                cmd = rclone_sync_cmd(config_path, str(local), remote, excludes)
            else:
                cmd = rclone_copy_file_cmd(config_path, str(local), remote)
            rc, err = _run_rclone(cmd)
            if rc != 0:
                errors.append(f"{rel}: {err or 'rclone failed'}"[:500])
            else:
                copied.append(rel)
        if errors and not copied:
            return {"ok": False, "error": errors[0], "errors": errors}
        if errors:
            return {"ok": False, "error": errors[0], "copied": copied, "errors": errors}
        return {"ok": True, "copied": copied}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:500]}
    finally:
        if config_path:
            try:
                os.remove(config_path)
            except OSError:
                pass


def get_or_create(session: Session) -> BackupDestination:
    row = session.exec(
        select(BackupDestination).where(BackupDestination.provider == "drive")
    ).first()
    if row:
        return row
    now = datetime.utcnow()
    row = BackupDestination(
        name="Google Drive",
        provider="drive",
        enabled=True,
        selection_json=selection_json([], []),
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _active_replicate(session: Session, destination_id: int) -> Job | None:
    rows = session.exec(
        select(Job)
        .where(
            Job.job_type == JOB_TYPE,
            Job.status.in_(["pending", "running"]),
        )
        .order_by(Job.created_at.desc())
    ).all()
    for job in rows:
        try:
            data = json.loads(job.details or "{}")
        except Exception:
            data = {}
        if int(data.get("destination_id") or 0) == int(destination_id):
            return job
    return None


def enqueue(
    session: Session,
    destination: BackupDestination,
    *,
    server_id: int | None = None,
    scope: str | None = None,
    user_id: int | None = None,
) -> Job:
    from .demo import demo_mode
    from .jobs.service import _initial_job_details

    active = _active_replicate(session, int(destination.id or 0))
    if active:
        return active
    job = Job(server_id=server_id, job_type=JOB_TYPE, status="pending")
    job.details = _initial_job_details(
        "Drive copy queued…",
        destination_id=destination.id,
        scope=scope,
        user_id=user_id,
        queued_at=datetime.utcnow().isoformat(),
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    if demo_mode():
        job.status = "failed"
        job.finished_at = datetime.utcnow()
        job.details = json.dumps(
            {"error": "Demo does not upload", "destination_id": destination.id, "done": True}
        )
        session.add(job)
        session.commit()
        return job
    from ..tasks import replicate_backup

    try:
        async_result = replicate_backup.delay(job.id)
        job.celery_task_id = async_result.id
        session.add(job)
        session.commit()
    except Exception as exc:
        job.status = "failed"
        job.finished_at = datetime.utcnow()
        job.details = json.dumps({"error": str(exc)[:500], "destination_id": destination.id})
        session.add(job)
        session.commit()
    return job


def enqueue_after_host_backup(session: Session, server: Server) -> None:
    folder = host_folder_name(server)
    if not folder:
        return
    rows = session.exec(
        select(BackupDestination).where(
            BackupDestination.enabled == True,  # noqa: E712
            BackupDestination.after_host_backup == True,  # noqa: E712
        )
    ).all()
    for dest in rows:
        checked, skipped = parse_selection(dest.selection_json)
        scoped, _ = paths_for_scope(checked, skipped, folder)
        if not scoped:
            continue
        enqueue(session, dest, server_id=server.id, scope=folder)
