"""Instance-wide operational settings — PostgreSQL source of truth.

Principle: settings that matter for DR live in the DB so a database restore
(or PiHerder self-backup) brings them back with users/servers.

Covers: timezone, force 2FA, fleet check defaults, self-backup schedule.
Does not store secrets (PIHERDER_MASTER_KEY, DB URL) — those stay in env.

API is session-free for call sites (templates, auth, scheduler); each call
opens a short DB session. One-time import from legacy JSON files if present.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from ..config import settings
from ..database import engine
from ..models import AppSetting

logger = logging.getLogger(__name__)

# Process-local cache (compose uses 1 web worker; invalidate on every save).
_cache: Optional[Dict[str, Any]] = None
_migrated_files = False

LEGACY_FILE = "herder-config.json"
LEGACY_DOT = ".herder-backup-config.json"

DEFAULTS: Dict[str, Any] = {
    "keep": 10,
    "schedule_mode": "config_only",
    "timezone": "UTC",
    "schedule_enabled": False,
    "schedule_cron": "0 3 * * *",
    "os_check_global_enabled": True,
    "os_check_cron": "0 0 * * *",
    "container_check_global_enabled": True,
    "container_check_cron": "0 0 * * *",
    "update_check_jitter": True,
    "force_2fa": False,
    # Require TOTP enabled to deploy templates / view deployment secrets
    "template_require_2fa": False,
    # Suggest host/service FQDNs as {slug}.{dns_base_domain}
    "dns_base_domain": "",
}


def clear_cache() -> None:
    """Drop process cache (tests / after external DB restore)."""
    global _cache
    _cache = None


def _legacy_file_paths() -> List[Path]:
    data = Path(settings.DATA_ROOT or "/data")
    roots = [
        data,
        Path(settings.HERDER_BACKUP_ROOT),
        Path("/herder_backups"),
        Path("/backups"),
        Path("/backups/piherder_backups"),
    ]
    seen: Set[str] = set()
    out: List[Path] = []
    for root in roots:
        for name in (LEGACY_FILE, LEGACY_DOT):
            p = root / name
            key = str(p)
            if key not in seen:
                seen.add(key)
                out.append(p)
    return out


def _read_legacy_files() -> dict:
    for path in _legacy_file_paths():
        if not path.exists():
            continue
        try:
            raw = json.loads(path.read_text()) or {}
            if isinstance(raw, dict) and raw:
                logger.info("Found legacy settings file %s", path)
                return raw
        except Exception:
            continue
    return {}


def _parse_row(row: Optional[AppSetting]) -> dict:
    if not row or not row.data_json:
        return {}
    try:
        raw = json.loads(row.data_json) or {}
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _get_or_create_row(session: Session) -> AppSetting:
    row = session.get(AppSetting, 1)
    if row is None:
        row = session.exec(select(AppSetting).limit(1)).first()
    if row is None:
        row = AppSetting(id=1, data_json="{}", updated_at=datetime.utcnow())
        session.add(row)
        session.commit()
        session.refresh(row)
    return row


def _load_raw_from_db() -> dict:
    """Return stored JSON object (no defaults). Empty dict if table missing / empty."""
    global _migrated_files
    try:
        with Session(engine) as session:
            row = session.get(AppSetting, 1) or session.exec(select(AppSetting).limit(1)).first()
            raw = _parse_row(row)
            if raw:
                return raw
            # First boot: import from file once, then persist
            if not _migrated_files:
                _migrated_files = True
                legacy = _read_legacy_files()
                if legacy:
                    row = _get_or_create_row(session)
                    merged = {**DEFAULTS, **legacy}
                    row.data_json = json.dumps(merged)
                    row.updated_at = datetime.utcnow()
                    session.add(row)
                    session.commit()
                    logger.info("Migrated operational settings from file → database")
                    return {k: merged[k] for k in merged if k in DEFAULTS or k in legacy}
            return {}
    except Exception as e:
        logger.warning("app_settings DB read failed: %s", e)
        # Last resort for early boot before migration: file
        return _read_legacy_files()


def _write_raw_to_db(data: dict) -> None:
    with Session(engine) as session:
        row = _get_or_create_row(session)
        row.data_json = json.dumps(data)
        row.updated_at = datetime.utcnow()
        session.add(row)
        session.commit()


def load_settings() -> dict:
    """Return merged defaults + DB settings (process-cached)."""
    global _cache
    if _cache is not None:
        return {**DEFAULTS, **_cache}

    raw = _load_raw_from_db()
    cfg = {**DEFAULTS, **raw}
    env_cron = (settings.HERDER_BACKUP_SCHEDULE or "").strip()
    if env_cron and "schedule_enabled" not in raw:
        cfg["schedule_enabled"] = True
        cfg["schedule_cron"] = env_cron
    _cache = {k: cfg[k] for k in cfg}  # store merged for simple force_2fa reads
    # Keep cache as raw+env overlay without re-applying defaults incorrectly:
    # Store the full merged view; save_settings re-reads DB.
    return dict(cfg)


def save_settings(partial: dict) -> dict:
    """Merge partial updates into DB. Returns full merged config."""
    global _cache
    raw = _load_raw_from_db()
    merged = {**DEFAULTS, **raw, **(partial or {})}
    _write_raw_to_db(merged)
    _cache = dict(merged)
    return dict(merged)


def replace_settings(full: dict) -> dict:
    """Replace settings from a backup payload (keys merge onto defaults)."""
    return save_settings(full or {})


def force_2fa_enabled() -> bool:
    return bool(load_settings().get("force_2fa"))


def get_app_timezone() -> str:
    return load_settings().get("timezone") or "UTC"


def set_app_timezone(tz: str) -> None:
    name = (tz or "UTC").strip() or "UTC"
    try:
        ZoneInfo(name)
    except Exception as e:
        raise ValueError(f"Invalid timezone: {name}") from e
    save_settings({"timezone": name})


def get_available_timezones() -> List[str]:
    try:
        from zoneinfo import available_timezones

        return sorted(available_timezones()) or ["UTC"]
    except Exception:
        return [
            "UTC",
            "Europe/London",
            "Europe/Paris",
            "Europe/Berlin",
            "Europe/Amsterdam",
            "America/New_York",
            "America/Chicago",
            "America/Los_Angeles",
            "Asia/Tokyo",
            "Asia/Singapore",
            "Australia/Sydney",
            "Pacific/Auckland",
        ]


def parse_utc_datetime(value: Any) -> Optional[datetime]:
    """Parse DB/API timestamps as UTC.

    Naive datetimes and ISO strings without offset are treated as UTC (how
    PiHerder stores times via ``datetime.utcnow()``).
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            return dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=ZoneInfo("UTC"))
        except (OverflowError, OSError, ValueError):
            return None
    s = str(value).strip()
    if not s or s.lower() in ("never", "—", "-"):
        return None
    try:
        if s.endswith("Z") or s.endswith("z"):
            s = s[:-1] + "+00:00"
        # Space separator common in some displays
        if " " in s and "T" not in s[:20]:
            s = s.replace(" ", "T", 1)
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt
    except Exception:
        return None


def utc_isoformat(value: Any) -> Optional[str]:
    """Serialize a UTC-stored timestamp for HTML/JS (always ends with Z)."""
    dt = parse_utc_datetime(value)
    if not dt:
        return None
    return dt.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")


def format_datetime_in_app_tz(
    dt: Optional[Any], fmt: str = "%Y-%m-%d %H:%M"
) -> str:
    """Format a UTC-stored timestamp in the operator-selected app timezone."""
    if not dt:
        return "Never"
    parsed = parse_utc_datetime(dt)
    if not parsed:
        return str(dt)
    tz_name = get_app_timezone()
    try:
        return parsed.astimezone(ZoneInfo(tz_name)).strftime(fmt)
    except Exception:
        try:
            return parsed.strftime(fmt)
        except Exception:
            return str(dt)


def validate_cron_expression(cron: str) -> str:
    """Return normalized 5-field cron or raise ValueError."""
    expr = (cron or "").strip()
    if len(expr.split()) != 5:
        raise ValueError(
            "Cron must have 5 fields (minute hour day month weekday). Example: 0 3 * * *"
        )
    try:
        import pycron

        pycron.is_now(expr, datetime.now())
    except ImportError:
        pass
    except Exception as e:
        raise ValueError(f"Invalid cron: {e}") from e
    return expr
