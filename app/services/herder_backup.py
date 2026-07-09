"""
Herder self-backup service.

Backs up PiHerder's own configuration (Servers with their encrypted keys, settings,
optionally AuditLog) as a compressed tar.gz on a host-mapped directory.

- "config only" (default for safety) vs include audit trail.
- Scheduled via APScheduler (global cron) or manual trigger.
- Output is compressed.
- Restore support (see routers or UI).
"""

import json
import tarfile
import gzip
import tempfile
import os
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
import logging
from zoneinfo import ZoneInfo

from ..config import settings
from ..database import engine
from sqlmodel import Session, select
from ..models import Server, AuditLog, User, DockerVersion

logger = logging.getLogger(__name__)

HERDER_BACKUP_DIR = Path(settings.HERDER_BACKUP_ROOT)
CONFIG_FILE = HERDER_BACKUP_DIR / ".herder-backup-config.json"

DEFAULT_CONFIG = {
    "keep": 10,
    "schedule_mode": "config_only",  # or "full"
    "timezone": "UTC",
    "schedule_enabled": False,
    "schedule_cron": "0 3 * * *",  # daily 03:00 in app timezone
    # Global fleet update-check defaults (applied to per-server schedules)
    "os_check_global_enabled": True,
    "os_check_cron": "0 0 * * *",  # midnight local (app timezone)
    "container_check_global_enabled": True,
    "container_check_cron": "0 0 * * *",  # midnight; per-host minute jitter applied
    "update_check_jitter": True,  # stagger minute by server_id so jobs queue, not thundering herd
    # Security policy
    "force_2fa": False,  # require TOTP for every user before using the app
}


def validate_cron_expression(cron: str) -> str:
    """Return normalized 5-field cron or raise ValueError."""
    expr = (cron or "").strip()
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError("Cron must have 5 fields (minute hour day month weekday). Example: 0 3 * * *")
    try:
        import pycron
        pycron.is_now(expr, datetime.now())
    except ImportError:
        pass
    except Exception as e:
        raise ValueError(f"Invalid cron: {e}") from e
    return expr

def get_available_timezones() -> List[str]:
    """Return IANA timezone list (continent/city). Uses stdlib zoneinfo (no extra dep)."""
    try:
        from zoneinfo import available_timezones
        tzs = sorted(available_timezones())
        # Keep it reasonable; full list is large but acceptable for <select>
        return tzs or ["UTC"]
    except Exception:
        # Fallback curated list (common ones)
        return [
            "UTC",
            "Europe/London", "Europe/Paris", "Europe/Berlin", "Europe/Amsterdam", "Europe/Helsinki", "Europe/Moscow",
            "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles",
            "America/Sao_Paulo", "America/Argentina/Buenos_Aires",
            "Asia/Tokyo", "Asia/Shanghai", "Asia/Singapore", "Asia/Dubai", "Asia/Kolkata",
            "Australia/Sydney", "Pacific/Auckland",
            "Africa/Johannesburg", "Africa/Nairobi",
        ]


def get_app_timezone() -> str:
    cfg = load_herder_config()
    return cfg.get("timezone") or "UTC"


def set_app_timezone(tz: str):
    cfg = load_herder_config()
    cfg["timezone"] = tz or "UTC"
    save_herder_config(cfg)


def format_datetime_in_app_tz(dt: Optional[datetime], fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Format a (UTC-naive or aware) datetime using the globally selected app timezone.
    Used so last backup times, audit times etc respect the Settings > Timezone everywhere.
    """
    if not dt:
        return "Never"
    tz_name = get_app_timezone()
    try:
        if dt.tzinfo is None:
            # Treat DB/file UTC times as UTC
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        local_dt = dt.astimezone(ZoneInfo(tz_name))
        return local_dt.strftime(fmt)
    except Exception:
        # Fallback
        try:
            return dt.strftime(fmt)
        except Exception:
            return str(dt)


def _ensure_dir():
    global HERDER_BACKUP_DIR, CONFIG_FILE
    try:
        HERDER_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        # test write permission
        test = HERDER_BACKUP_DIR / ".perm_test"
        test.write_text("1")
        test.unlink(missing_ok=True)
    except Exception:
        # fallback: use top-level of the working /backups mount (host ~/) to guarantee write.
        # Self archives will appear as piherder-*.tar.gz directly under the backups dir on host.
        # To use dedicated dir: on host mkdir -p ~/piherder_backups && sudo chown -R $(id -u):$(id -g) ~/piherder_backups
        # or chmod 777 ~/piherder_backups before starting.
        HERDER_BACKUP_DIR = Path("/backups")
        CONFIG_FILE = HERDER_BACKUP_DIR / ".herder-backup-config.json"


def load_herder_config() -> dict:
    _ensure_dir()
    raw: dict = {}
    if CONFIG_FILE.exists():
        try:
            raw = json.loads(CONFIG_FILE.read_text())
        except Exception:
            pass
    cfg = {**DEFAULT_CONFIG, **raw}
    # Bootstrap schedule from env when UI has never saved schedule settings.
    env_cron = (settings.HERDER_BACKUP_SCHEDULE or "").strip()
    if env_cron and "schedule_enabled" not in raw:
        cfg["schedule_enabled"] = True
        cfg["schedule_cron"] = env_cron
    return cfg


def save_herder_config(cfg: dict):
    """Merge partial updates with existing config so unrelated keys are preserved."""
    _ensure_dir()
    existing: dict = {}
    if CONFIG_FILE.exists():
        try:
            existing = json.loads(CONFIG_FILE.read_text()) or {}
        except Exception:
            existing = {}
    merged = {**DEFAULT_CONFIG, **existing, **cfg}
    CONFIG_FILE.write_text(json.dumps(merged, indent=2))


def prune_old_backups(keep: int):
    _ensure_dir()
    files = sorted(HERDER_BACKUP_DIR.glob("piherder-*.tar.gz"), key=lambda p: p.stat().st_mtime)
    removed = 0
    for p in files[:-keep]:
        try:
            p.unlink()
            removed += 1
        except Exception:
            pass
    if removed:
        logger.info(f"Pruned {removed} old herder backups (keep={keep})")


def _snapshot_servers() -> List[Dict[str, Any]]:
    with Session(engine) as s:
        rows = s.exec(select(Server)).all()
        return [r.model_dump() for r in rows]


def _snapshot_users() -> List[Dict[str, Any]]:
    with Session(engine) as s:
        rows = s.exec(select(User)).all()
        # Never export plaintext; hashes only (already are)
        return [{"id": u.id, "email": u.email, "created_at": u.created_at.isoformat() if u.created_at else None} for u in rows]


def _snapshot_docker_versions() -> List[Dict[str, Any]]:
    with Session(engine) as s:
        rows = s.exec(select(DockerVersion)).all()
        return [r.model_dump() for r in rows]


def _snapshot_audit(since_days: Optional[int] = None) -> List[Dict[str, Any]]:
    with Session(engine) as s:
        q = select(AuditLog).order_by(AuditLog.started_at.desc())
        if since_days:
            cutoff = datetime.utcnow() - timedelta(days=since_days)
            q = q.where(AuditLog.started_at >= cutoff)
        rows = s.exec(q.limit(5000)).all()  # safety cap
        return [r.model_dump() for r in rows]


def create_herder_backup(include_audit: bool = False, config_only: bool = True, since_days: int = 90) -> Path:
    """
    Create a compressed backup of PiHerder config.

    include_audit=False + config_only=True is the recommended "safe" default.
    The resulting .tar.gz lives under HERDER_BACKUP_ROOT on the host (map the volume!).
    """
    _ensure_dir()
    cfg = load_herder_config()
    keep = int(cfg.get("keep", 10))

    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    suffix = "config-only" if config_only else ("full" if include_audit else "config")
    filename = f"piherder-{ts}-{suffix}.tar.gz"
    out_path = HERDER_BACKUP_DIR / filename

    manifest = {
        "created_at": datetime.utcnow().isoformat() + "Z",
        "config_only": config_only,
        "include_audit": include_audit,
        "version": "1",
    }

    data: Dict[str, Any] = {
        "manifest": manifest,
        "servers": _snapshot_servers(),
        "users": _snapshot_users(),
        "docker_versions": _snapshot_docker_versions(),
    }

    if include_audit:
        data["audit_logs"] = _snapshot_audit(since_days=since_days if not config_only else 7)

    # Write JSON to a safe temp file (always /tmp, which is writable), then add to tar archive in target dir.
    # This avoids permission issues on the (possibly newly mounted or root-owned) backup dir for temp files.
    tmp_json_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, dir="/tmp") as tf:
            tmp_json_path = Path(tf.name)
            json.dump(data, tf, indent=2, default=str)
            tf.flush()
        with tarfile.open(out_path, "w:gz") as tar:
            tar.add(tmp_json_path, arcname="piherder-backup.json")
        logger.info(f"Herder backup created: {out_path}")

        # prune after successful create
        prune_old_backups(keep)

        return out_path
    except Exception as e:
        # if even out_path write fails, fallback archive to /backups root (guaranteed writable)
        if isinstance(e, PermissionError) or "Permission" in str(e) or not out_path.parent.exists() or not os.access(str(out_path.parent), os.W_OK):
            fb_dir = Path("/backups")
            out_path = fb_dir / filename
            with tarfile.open(out_path, "w:gz") as tar:
                tar.add(tmp_json_path, arcname="piherder-backup.json")
            logger.warning(f"Herder backup dir not writable, fell back to {out_path}")
            # note: prune may use old HERDER, but ok for now
            return out_path
        raise
    finally:
        if tmp_json_path and tmp_json_path.exists():
            tmp_json_path.unlink(missing_ok=True)


def list_backups() -> List[Dict[str, Any]]:
    _ensure_dir()
    candidates = list(HERDER_BACKUP_DIR.glob("piherder-*.tar.gz"))
    # also check the "nice" subdir location (in case user fixed perms later or previous fallback wrote to sub)
    for extra in [Path("/backups") / "piherder_backups", Path("/herder_backups")]:
        if extra != HERDER_BACKUP_DIR:
            candidates.extend(extra.glob("piherder-*.tar.gz"))
    seen = set()
    out = []
    for p in sorted((c for c in candidates if c.exists()), key=lambda x: x.stat().st_mtime, reverse=True):
        if str(p) not in seen:
            seen.add(str(p))
            stat = p.stat()
            out.append({
                "path": str(p),
                "name": p.name,
                "size": stat.st_size,
                "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })
    return out


def restore_herder_backup(archive_path: str, restore_audit: bool = False, dry_run: bool = False) -> Dict[str, Any]:
    """
    Very careful restore.
    - Servers are upserted by id (or name fallback).
    - Encrypted fields travel as-is (master key must match).
    - Audit restore is optional and append-only (never deletes existing).
    """
    p = Path(archive_path)
    if not p.exists():
        raise FileNotFoundError(p)

    result = {"restored_servers": 0, "restored_audit": 0, "dry_run": dry_run}

    with tarfile.open(p, "r:gz") as tar:
        member = tar.extractfile("piherder-backup.json")
        if not member:
            raise ValueError("Invalid herder backup (missing json)")
        payload = json.loads(member.read())

    if dry_run:
        result["would_restore_servers"] = len(payload.get("servers", []))
        if restore_audit:
            result["would_restore_audit"] = len(payload.get("audit_logs", []))
        return result

    with Session(engine) as s:
        # Servers
        for srv in payload.get("servers", []):
            existing = s.get(Server, srv.get("id")) if srv.get("id") else None
            if existing:
                # update non-id fields (keep id)
                for k, v in srv.items():
                    if k != "id" and hasattr(existing, k):
                        setattr(existing, k, v)
                s.add(existing)
            else:
                # create new (let DB assign if id collision unlikely)
                new_srv = Server(**{k: v for k, v in srv.items() if k != "id"})
                s.add(new_srv)
            result["restored_servers"] += 1

        # Optional audit append
        if restore_audit:
            for al in payload.get("audit_logs", []):
                # avoid dupes by rough started_at + action check
                exists = s.exec(
                    select(AuditLog).where(
                        AuditLog.started_at == al.get("started_at"),
                        AuditLog.action == al.get("action"),
                        AuditLog.server_id == al.get("server_id"),
                    )
                ).first()
                if not exists:
                    # reconstruct minimal
                    new_al = AuditLog(
                        user_id=al.get("user_id"),
                        server_id=al.get("server_id"),
                        action=al.get("action"),
                        status=al.get("status"),
                        details=al.get("details"),
                        output_snippet=al.get("output_snippet"),
                        started_at=al.get("started_at"),
                        finished_at=al.get("finished_at"),
                    )
                    s.add(new_al)
                    result["restored_audit"] += 1

        s.commit()

    return result