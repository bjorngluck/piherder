"""Warn when a destination copy is scheduled before the host backups it ships.

The copy reads /backups. If it runs earlier the same day than a host backup
whose folder is ticked, it can ship the previous backup. A direct host is
left out: that copy reads the host, not the folder on /backups.

The check looks 35 days ahead in the Settings timezone. One warning per
destination. It resolves when no ticked folder host is still later that day.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from ..models import BackupDestination, Server

logger = logging.getLogger(__name__)

COPY_ORDER_LOOKAHEAD_DAYS = 35
_FIRE_LIMIT = 400


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _zone(tz_name: str):
    try:
        return ZoneInfo(tz_name or "UTC")
    except Exception:
        return timezone.utc


def fires_between(
    cron: str,
    tz_name: str,
    start: datetime,
    end: datetime,
    *,
    limit: int = _FIRE_LIMIT,
) -> list[datetime]:
    """Cron fires from ``start`` through ``end``, inclusive. Empty when unusable."""
    start_utc = _as_utc(start)
    end_utc = _as_utc(end)
    text = (cron or "").strip()
    if start_utc is None or end_utc is None or not text or end_utc < start_utc:
        return []
    try:
        from .scheduler import _cron_trigger

        trigger = _cron_trigger(text, timezone=(tz_name or "UTC"))
    except Exception:
        return []
    cursor = start_utc
    found: list[datetime] = []
    for _ in range(limit):
        try:
            nxt = trigger.get_next_fire_time(None, cursor)
        except Exception:
            return found
        if nxt is None or nxt > end_utc:
            return found
        if nxt < start_utc:
            cursor = start_utc + timedelta(seconds=1)
            continue
        found.append(nxt)
        cursor = nxt + timedelta(microseconds=1)
    return found


def copy_runs_before_host(
    copy_cron: str,
    host_cron: str,
    tz_name: str,
    now: datetime,
    *,
    lookahead_days: int = COPY_ORDER_LOOKAHEAD_DAYS,
) -> bool:
    """True when some copy fire shares a local day with a strictly later host backup."""
    start = _as_utc(now)
    if start is None:
        return False
    end = start + timedelta(days=lookahead_days)
    copies = fires_between(copy_cron, tz_name, start, end)
    if not copies:
        return False
    hosts = fires_between(host_cron, tz_name, start, end)
    if not hosts:
        return False
    zone = _zone(tz_name)
    by_day: dict = {}
    for fire in hosts:
        by_day.setdefault(fire.astimezone(zone).date(), []).append(fire)
    for fire in copies:
        later = by_day.get(fire.astimezone(zone).date()) or []
        if any(backup > fire for backup in later):
            return True
    return False


def folder_selected(folder: str, checked: list[str], skipped: list[str]) -> bool:
    """True when this host folder, or something inside it, is ticked for the copy."""
    from .backup_replicate import path_included

    name = (folder or "").strip().strip("/")
    if not name:
        return False
    if path_included(name, checked, skipped):
        return True
    prefix = name + "/"
    for item in checked:
        if item == name or str(item).startswith(prefix):
            if path_included(item, checked, skipped):
                return True
    return False


def shipped_folder_hosts(destination, servers) -> list:
    """Backup-enabled hosts whose /backups folder this scheduled copy ships.

    Direct hosts are omitted. Their copy reads the host itself.
    """
    from .backup_replicate import host_folder_name, parse_selection

    if destination is None or not getattr(destination, "enabled", False):
        return []
    if not (getattr(destination, "schedule", None) or "").strip():
        return []
    if not (getattr(destination, "credentials_encrypted", None) or "").strip():
        return []
    checked, skipped = parse_selection(getattr(destination, "selection_json", None))
    if not checked:
        return []
    chosen = []
    for server in servers or []:
        if not getattr(server, "backup_enabled", False):
            continue
        if getattr(server, "backup_direct", False):
            continue
        if not getattr(server, "id", None):
            continue
        folder = host_folder_name(server)
        if folder_selected(folder, checked, skipped):
            chosen.append(server)
    return chosen


def _host_label(server) -> str:
    return (
        (getattr(server, "name", None) or "").strip()
        or (getattr(server, "hostname", None) or "").strip()
        or f"#{getattr(server, 'id', '')}"
    )


def join_host_names(names: list[str]) -> str:
    shown = [name for name in names if name][:4]
    if not shown:
        return ""
    text = ", ".join(shown)
    extra = len([name for name in names if name]) - len(shown)
    if extra > 0:
        text = f"{text}, and {extra} more"
    return text


def early_sentence(destination_name: str, host_names: list[str]) -> str:
    label = (destination_name or "").strip() or "This"
    return (
        f"The {label} copy is scheduled before the host backup for "
        f"{join_host_names(host_names)}. It can ship the previous backup."
    )


def early_hosts_for(
    destination,
    servers,
    *,
    tz_name: str,
    now: datetime,
) -> list:
    from .schedule_watch import backup_cron_for

    copy_cron = (getattr(destination, "schedule", None) or "").strip()
    found = []
    for server in shipped_folder_hosts(destination, servers):
        host_cron = backup_cron_for(server)
        if not host_cron:
            continue
        if copy_runs_before_host(copy_cron, host_cron, tz_name, now):
            found.append(server)
    return found


def _fingerprint(destination_id: int) -> str:
    return f"backup_copy_early:{int(destination_id)}"


def sync_copy_order_alerts(session: Session, now: Optional[datetime] = None) -> int:
    """Open or resolve one warning per destination. Returns how many are open."""
    from .app_settings import get_app_timezone
    from .notifications import resolve_by_fingerprint, upsert_notification

    moment = now or datetime.now(timezone.utc)
    try:
        tz_name = get_app_timezone() or "UTC"
    except Exception:
        tz_name = "UTC"
    servers = list(session.exec(select(Server)).all())
    destinations = list(session.exec(select(BackupDestination)).all())
    opened = 0
    for dest in destinations:
        if not dest.id:
            continue
        early = early_hosts_for(dest, servers, tz_name=tz_name, now=moment)
        if not early:
            resolve_by_fingerprint(session, _fingerprint(int(dest.id)))
            continue
        names = [_host_label(server) for server in early]
        upsert_notification(
            session,
            fingerprint=_fingerprint(int(dest.id)),
            type="backup_copy_early",
            title=f"Copy runs before the backups: {(dest.name or '').strip() or 'destination'}",
            body=early_sentence(dest.name or "", names),
            link_url="/herder-backups?tab=backup",
            severity="warning",
        )
        opened += 1
    return opened


def early_text_by_provider(session: Session, now: Optional[datetime] = None) -> dict[str, str]:
    """Provider → sentence for the Settings copy list. Does not write an alert."""
    from .app_settings import get_app_timezone

    moment = now or datetime.now(timezone.utc)
    try:
        tz_name = get_app_timezone() or "UTC"
    except Exception:
        tz_name = "UTC"
    servers = list(session.exec(select(Server)).all())
    out: dict[str, str] = {}
    for dest in session.exec(select(BackupDestination)).all():
        provider = (getattr(dest, "provider", None) or "").strip().lower()
        if not provider:
            continue
        early = early_hosts_for(dest, servers, tz_name=tz_name, now=moment)
        if not early:
            continue
        out[provider] = early_sentence(dest.name or "", [_host_label(server) for server in early])
    return out
