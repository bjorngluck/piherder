"""Mute delivery for one host or one alert type.

A matching open alert stays in the inbox. Webhook, email, and push do not fire.
Schedules are not touched. Auth failures, OAuth problems, and backup failures
cannot be muted.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from ..models import AlertMuteRule, Server
from . import alert_policy as apol
from .app_settings import get_app_timezone

# Auth failures, OAuth problems, and backup failures.
UNMUTABLE_TYPES = frozenset({
    "backup_failed",
    "herder_backup_failed",
    "backup_destination_auth_failed",
    "drive_oauth_refresh_failed",
    "drive_oauth_token_aging",
})


class MuteRejected(ValueError):
    """The rule is not allowed. The message is safe to show."""


def unmutable(alert_type: str) -> bool:
    return (alert_type or "").strip() in UNMUTABLE_TYPES


def _naive_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(timezone.utc).replace(tzinfo=None)


def parse_instant(raw: object) -> Optional[datetime]:
    """None when empty. ValueError when the text is not a timestamp."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except Exception as exc:
        raise ValueError("time") from exc
    return _naive_utc(moment)


def parse_local_instant(raw: object) -> Optional[datetime]:
    """A datetime-local value in the app timezone, stored as naive UTC."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except Exception as exc:
        raise ValueError("time") from exc
    if moment.tzinfo is not None:
        return _naive_utc(moment)
    name = (get_app_timezone() or "UTC").strip() or "UTC"
    try:
        zone = ZoneInfo(name)
    except Exception:
        zone = ZoneInfo("UTC")
    return moment.replace(tzinfo=zone).astimezone(timezone.utc).replace(tzinfo=None)


def local_input_value(moment: Optional[datetime]) -> str:
    if moment is None:
        return ""
    name = (get_app_timezone() or "UTC").strip() or "UTC"
    try:
        zone = ZoneInfo(name)
    except Exception:
        zone = ZoneInfo("UTC")
    aware = moment.replace(tzinfo=timezone.utc).astimezone(zone)
    return aware.strftime("%Y-%m-%dT%H:%M")


def _check_window(starts_at: Optional[datetime], ends_at: Optional[datetime]) -> None:
    if starts_at is not None and ends_at is None:
        raise MuteRejected("A time window must have an end")
    if starts_at is not None and ends_at is not None and ends_at <= starts_at:
        raise MuteRejected("The window end is before the start")


def is_active(rule: AlertMuteRule, now: Optional[datetime] = None) -> bool:
    moment = now or datetime.utcnow()
    if rule.starts_at is not None and moment < rule.starts_at:
        return False
    if rule.ends_at is not None and moment >= rule.ends_at:
        return False
    return True


def _rules(session: Session) -> list[AlertMuteRule]:
    return list(session.exec(select(AlertMuteRule)).all())


def list_rules(session: Session) -> list[AlertMuteRule]:
    return list(session.exec(select(AlertMuteRule).order_by(AlertMuteRule.id)).all())


def active_rules(session: Session, now: Optional[datetime] = None) -> list[AlertMuteRule]:
    moment = now or datetime.utcnow()
    return [row for row in _rules(session) if is_active(row, moment)]


def active_count(session: Session, now: Optional[datetime] = None) -> int:
    return len(active_rules(session, now))


def is_muted(
    session: Session,
    *,
    alert_type: str,
    server_id: Optional[int],
    now: Optional[datetime] = None,
) -> bool:
    if unmutable(alert_type):
        return False
    moment = now or datetime.utcnow()
    kind_type = (alert_type or "").strip()
    for rule in _rules(session):
        if not is_active(rule, moment):
            continue
        if rule.kind == "host" and server_id is not None and rule.server_id == int(server_id):
            return True
        if rule.kind == "type" and (rule.alert_type or "") == kind_type:
            return True
    return False


def _duplicate(session: Session, *, kind: str, server_id: Optional[int], alert_type: Optional[str], skip_id: Optional[int]) -> bool:
    for row in _rules(session):
        if skip_id is not None and row.id == skip_id:
            continue
        if kind == "host" and row.kind == "host" and row.server_id == server_id:
            return True
        if kind == "type" and row.kind == "type" and (row.alert_type or "") == (alert_type or ""):
            return True
    return False


def _validate_target(
    session: Session,
    *,
    kind: str,
    server_id: Optional[int],
    alert_type: Optional[str],
    skip_id: Optional[int],
) -> tuple[Optional[int], Optional[str]]:
    if kind == "host":
        if server_id is None:
            raise MuteRejected("Choose a host")
        if alert_type:
            raise MuteRejected("A rule is one host or one alert type")
        host = session.get(Server, int(server_id))
        if host is None:
            raise MuteRejected("Host not found")
        if _duplicate(session, kind="host", server_id=int(server_id), alert_type=None, skip_id=skip_id):
            raise MuteRejected("That host already has a mute rule")
        return int(server_id), None
    if kind == "type":
        name = (alert_type or "").strip()
        if not name:
            raise MuteRejected("Choose an alert type")
        if server_id is not None:
            raise MuteRejected("A rule is one host or one alert type")
        if name not in apol.CATALOG_BY_ID:
            raise MuteRejected("Unknown alert type")
        if unmutable(name):
            raise MuteRejected("That alert cannot be muted")
        if _duplicate(session, kind="type", server_id=None, alert_type=name, skip_id=skip_id):
            raise MuteRejected("That alert type already has a mute rule")
        return None, name
    raise MuteRejected("A rule is one host or one alert type")


def create_rule(
    session: Session,
    *,
    kind: str,
    server_id: Optional[int],
    alert_type: Optional[str],
    starts_at: Optional[datetime],
    ends_at: Optional[datetime],
) -> AlertMuteRule:
    _check_window(starts_at, ends_at)
    host_id, type_name = _validate_target(
        session,
        kind=kind,
        server_id=server_id,
        alert_type=alert_type,
        skip_id=None,
    )
    now = datetime.utcnow()
    row = AlertMuteRule(
        kind=kind,
        server_id=host_id,
        alert_type=type_name,
        starts_at=starts_at,
        ends_at=ends_at,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def update_window(
    session: Session,
    rule_id: int,
    *,
    starts_at: Optional[datetime],
    ends_at: Optional[datetime],
    starts_set: bool,
    ends_set: bool,
) -> AlertMuteRule:
    row = session.get(AlertMuteRule, int(rule_id))
    if row is None:
        raise MuteRejected("Rule not found")
    new_start = starts_at if starts_set else row.starts_at
    new_end = ends_at if ends_set else row.ends_at
    _check_window(new_start, new_end)
    row.starts_at = new_start
    row.ends_at = new_end
    row.updated_at = datetime.utcnow()
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def delete_rule(session: Session, rule_id: int) -> AlertMuteRule:
    row = session.get(AlertMuteRule, int(rule_id))
    if row is None:
        raise MuteRejected("Rule not found")
    session.delete(row)
    session.commit()
    return row


def audit_details(row: AlertMuteRule) -> str:
    start = row.starts_at.isoformat() + "Z" if row.starts_at else ""
    end = row.ends_at.isoformat() + "Z" if row.ends_at else ""
    if row.kind == "host":
        target = f"server_id={row.server_id}"
    else:
        target = f"alert_type={row.alert_type or ''}"
    return f"kind={row.kind} {target} starts_at={start} ends_at={end}"


def mutable_types() -> list[tuple[str, str]]:
    return [
        (spec.id, spec.label)
        for spec in apol.CATALOG
        if spec.id not in UNMUTABLE_TYPES
    ]
