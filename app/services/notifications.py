"""In-app notification center — actionable alerts separate from AuditLog."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional, List, Any

from sqlmodel import Session, select

from ..models import Notification, User
from ..config import settings

logger = logging.getLogger(__name__)


def upsert_notification(
    session: Session,
    *,
    fingerprint: str,
    type: str,
    title: str,
    body: Optional[str] = None,
    link_url: Optional[str] = None,
    severity: str = "warning",
    server_id: Optional[int] = None,
    user_id: Optional[int] = None,
    payload: Optional[dict] = None,
) -> Notification:
    """Create or refresh an open notification for this fingerprint.

    If a dismissed/resolved row exists for the same fingerprint and the condition
    is still true, re-open a new open row only when no open row exists (idempotent).
    """
    existing = session.exec(
        select(Notification).where(
            Notification.fingerprint == fingerprint,
            Notification.status == "open",
        )
    ).first()
    now = datetime.utcnow()
    payload_s = json.dumps(payload) if payload is not None else None

    if existing:
        existing.title = title
        existing.body = body
        existing.link_url = link_url
        existing.severity = severity
        existing.server_id = server_id if server_id is not None else existing.server_id
        existing.user_id = user_id if user_id is not None else existing.user_id
        if payload_s is not None:
            existing.payload = payload_s
        existing.updated_at = now
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing

    n = Notification(
        fingerprint=fingerprint,
        type=type,
        title=title,
        body=body,
        link_url=link_url,
        severity=severity,
        server_id=server_id,
        user_id=user_id,
        payload=payload_s,
        status="open",
        created_at=now,
        updated_at=now,
    )
    session.add(n)
    session.commit()
    session.refresh(n)

    # Bridge to legacy webhook for warning+
    if severity in ("warning", "critical"):
        _maybe_webhook(f"[{severity}] {title}" + (f": {body}" if body else ""))

    # Optional Web Push (only on *new* open rows — not fingerprint refreshes)
    _maybe_push(session, n)

    return n


def resolve_by_fingerprint(session: Session, fingerprint: str) -> int:
    """Mark all open notifications with this fingerprint as resolved."""
    rows = list(
        session.exec(
            select(Notification).where(
                Notification.fingerprint == fingerprint,
                Notification.status == "open",
            )
        ).all()
    )
    now = datetime.utcnow()
    for n in rows:
        n.status = "resolved"
        n.resolved_at = now
        n.updated_at = now
        session.add(n)
    if rows:
        session.commit()
    return len(rows)


def dismiss(session: Session, notification_id: int, user: User | None = None) -> bool:
    """Mark an open notification dismissed. Idempotent if already closed."""
    n = session.get(Notification, notification_id)
    if not n:
        return False
    if n.status != "open":
        # Already resolved/dismissed — treat as success so UI forms don't 404
        return True
    n.status = "dismissed"
    n.dismissed_at = datetime.utcnow()
    n.updated_at = n.dismissed_at
    session.add(n)
    session.commit()
    return True


def dismiss_all(session: Session, user: User | None = None) -> int:
    rows = list(
        session.exec(select(Notification).where(Notification.status == "open")).all()
    )
    now = datetime.utcnow()
    for n in rows:
        n.status = "dismissed"
        n.dismissed_at = now
        n.updated_at = now
        session.add(n)
    if rows:
        session.commit()
    return len(rows)


def list_notifications(
    session: Session,
    *,
    status: Optional[str] = "open",
    type: Optional[str] = None,
    server_id: Optional[int] = None,
    limit: int = 100,
) -> List[Notification]:
    q = select(Notification).order_by(Notification.updated_at.desc())
    if status:
        q = q.where(Notification.status == status)
    if type:
        q = q.where(Notification.type == type)
    if server_id is not None:
        q = q.where(Notification.server_id == server_id)
    return list(session.exec(q.limit(limit)).all())


def open_count(session: Session) -> int:
    rows = session.exec(
        select(Notification).where(Notification.status == "open")
    ).all()
    return len(list(rows))


def mark_read(session: Session, notification_id: int) -> bool:
    n = session.get(Notification, notification_id)
    if not n:
        return False
    if not n.read_at:
        n.read_at = datetime.utcnow()
        session.add(n)
        session.commit()
    return True


def _maybe_webhook(message: str) -> None:
    if not settings.WEBHOOK_URL:
        return
    try:
        import httpx
        payload = {
            "message": message,
            "number": settings.WEBHOOK_NUMBER or "",
            "recipients": json.loads(settings.WEBHOOK_RECIPIENTS or "[]"),
        }
        httpx.post(settings.WEBHOOK_URL, json=payload, timeout=8)
    except Exception as e:
        logger.debug(f"Notification webhook failed: {e}")


def _maybe_push(session: Session, notification: Notification) -> None:
    """Best-effort Web Push; never break the in-app notification path."""
    try:
        from .push import send_for_notification

        send_for_notification(session, notification)
    except Exception as e:
        logger.debug("Web push dispatch failed: %s", e)


# --- Domain helpers used by check jobs ---

def notify_os_updates(
    session: Session,
    server_id: int,
    server_name: str,
    updates_count: int,
    reboot_pending: bool,
    phased_count: int = 0,
) -> None:
    """Alert only on *actionable* upgrades (updates_count).

    Ubuntu phased packages (listed but not installable yet) are visibility-only —
    they must not keep a warning open after a successful patch with 0 upgrades.
    """
    fp_os = f"os_updates:server:{server_id}"
    fp_reboot = f"reboot_pending:server:{server_id}"
    link = f"/servers/{server_id}"

    if updates_count and updates_count > 0:
        body = f"{updates_count} package(s) ready to install"
        if phased_count and phased_count > 0:
            body += f" · {phased_count} deferred (phased)"
        upsert_notification(
            session,
            fingerprint=fp_os,
            type="os_updates",
            title=f"OS updates on {server_name}",
            body=body,
            link_url=link,
            severity="warning",
            server_id=server_id,
            payload={
                "updates_count": updates_count,
                "phased_count": phased_count or 0,
            },
        )
    else:
        # Phased-only or clean — clear actionable alert
        resolve_by_fingerprint(session, fp_os)

    if reboot_pending:
        upsert_notification(
            session,
            fingerprint=fp_reboot,
            type="reboot_pending",
            title=f"Reboot pending on {server_name}",
            body="Kernel or system packages require a reboot",
            link_url=link,
            severity="warning",
            server_id=server_id,
        )
    else:
        resolve_by_fingerprint(session, fp_reboot)


def notify_container_updates(
    session: Session,
    server_id: int,
    server_name: str,
    projects: list[str],
) -> None:
    fp = f"container_updates:server:{server_id}"
    link = f"/servers/{server_id}/docker"
    if projects:
        names = ", ".join(projects[:8])
        extra = f" (+{len(projects) - 8} more)" if len(projects) > 8 else ""
        upsert_notification(
            session,
            fingerprint=fp,
            type="container_updates",
            title=f"Container image updates on {server_name}",
            body=f"{len(projects)} project(s): {names}{extra}",
            link_url=link,
            severity="warning",
            server_id=server_id,
            payload={"projects": projects},
        )
    else:
        resolve_by_fingerprint(session, fp)


def notify_backup_failed(
    session: Session,
    server_id: int,
    server_name: str,
    message: str,
) -> None:
    upsert_notification(
        session,
        fingerprint=f"backup_failed:server:{server_id}",
        type="backup_failed",
        title=f"Backup failed: {server_name}",
        body=(message or "Backup job failed")[:400],
        link_url=f"/servers/{server_id}/backups",
        severity="critical",
        server_id=server_id,
    )


def resolve_backup_failed(session: Session, server_id: int) -> None:
    """Close open backup-failed alerts for this server (after a successful run)."""
    resolve_by_fingerprint(session, f"backup_failed:server:{int(server_id)}")
