"""Web Push delivery — optional, VAPID-based, self-hosted friendly."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional

from sqlmodel import Session, select

from ..config import settings
from ..models import Notification, PushPreference, PushSubscription, User

logger = logging.getLogger(__name__)

# Notification.type → PushPreference column name
TYPE_PREF_FIELDS = {
    "backup_failed": "backup_failed",
    "os_updates": "os_updates",
    "reboot_pending": "reboot_pending",
    "container_updates": "container_updates",
    "herder_backup_failed": "herder_backup_failed",
}


def is_push_configured() -> bool:
    return bool(
        (settings.VAPID_PUBLIC_KEY or "").strip()
        and (settings.VAPID_PRIVATE_KEY or "").strip()
        and (settings.VAPID_CONTACT or "").strip()
    )


def vapid_public_key() -> Optional[str]:
    if not is_push_configured():
        return None
    return (settings.VAPID_PUBLIC_KEY or "").strip()


def get_or_create_preference(session: Session, user_id: int) -> PushPreference:
    pref = session.exec(
        select(PushPreference).where(PushPreference.user_id == user_id)
    ).first()
    if pref:
        return pref
    pref = PushPreference(user_id=user_id)
    session.add(pref)
    session.commit()
    session.refresh(pref)
    return pref


def preference_allows(pref: PushPreference, notif_type: str) -> bool:
    if not pref.push_enabled:
        return False
    field = TYPE_PREF_FIELDS.get(notif_type)
    if not field:
        # Unknown types: only send if master switch is on
        return True
    return bool(getattr(pref, field, False))


def save_subscription(
    session: Session,
    user: User,
    *,
    endpoint: str,
    p256dh: str,
    auth: str,
    user_agent: Optional[str] = None,
) -> PushSubscription:
    endpoint = (endpoint or "").strip()
    p256dh = (p256dh or "").strip()
    auth = (auth or "").strip()
    if not endpoint or not p256dh or not auth:
        raise ValueError("endpoint, p256dh, and auth are required")

    existing = session.exec(
        select(PushSubscription).where(PushSubscription.endpoint == endpoint)
    ).first()
    now = datetime.utcnow()
    if existing:
        existing.user_id = user.id  # type: ignore[assignment]
        existing.p256dh = p256dh
        existing.auth = auth
        if user_agent is not None:
            existing.user_agent = user_agent
        existing.disabled_at = None
        existing.last_success_at = now
        session.add(existing)
        session.commit()
        session.refresh(existing)
        sub = existing
    else:
        sub = PushSubscription(
            user_id=user.id,  # type: ignore[arg-type]
            endpoint=endpoint,
            p256dh=p256dh,
            auth=auth,
            user_agent=user_agent,
            created_at=now,
            last_success_at=now,
        )
        session.add(sub)
        session.commit()
        session.refresh(sub)

    # Ensure preference row exists; enable master switch on first subscribe
    pref = get_or_create_preference(session, user.id)  # type: ignore[arg-type]
    if not pref.push_enabled:
        pref.push_enabled = True
        pref.updated_at = now
        session.add(pref)
        session.commit()

    return sub


def remove_subscription(
    session: Session,
    user: User,
    endpoint: Optional[str] = None,
) -> int:
    """Remove one endpoint for user, or all of their subscriptions if endpoint is None."""
    q = select(PushSubscription).where(PushSubscription.user_id == user.id)
    if endpoint:
        q = q.where(PushSubscription.endpoint == endpoint.strip())
    rows = list(session.exec(q).all())
    for r in rows:
        session.delete(r)
    if rows:
        session.commit()
    return len(rows)


def list_subscriptions(session: Session, user_id: int) -> list[PushSubscription]:
    return list(
        session.exec(
            select(PushSubscription)
            .where(
                PushSubscription.user_id == user_id,
                PushSubscription.disabled_at.is_(None),
            )
            .order_by(PushSubscription.created_at.desc())
        ).all()
    )


def update_preferences(
    session: Session,
    user_id: int,
    *,
    push_enabled: bool,
    backup_failed: bool = True,
    os_updates: bool = True,
    reboot_pending: bool = True,
    container_updates: bool = True,
    herder_backup_failed: bool = True,
) -> PushPreference:
    pref = get_or_create_preference(session, user_id)
    pref.push_enabled = push_enabled
    pref.backup_failed = backup_failed
    pref.os_updates = os_updates
    pref.reboot_pending = reboot_pending
    pref.container_updates = container_updates
    pref.herder_backup_failed = herder_backup_failed
    pref.updated_at = datetime.utcnow()
    session.add(pref)
    session.commit()
    session.refresh(pref)
    return pref


def _subscription_info(sub: PushSubscription) -> dict[str, Any]:
    return {
        "endpoint": sub.endpoint,
        "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
    }


def _delete_subscription_id(session: Session, sub_id: int) -> None:
    row = session.get(PushSubscription, sub_id)
    if row:
        session.delete(row)
        session.commit()


def send_for_notification(session: Session, notification: Notification) -> int:
    """Send Web Push for a newly created open notification. Returns send attempts succeeded."""
    if not is_push_configured():
        return 0

    # Load active subscriptions with prefs enabled
    subs = list(
        session.exec(
            select(PushSubscription).where(PushSubscription.disabled_at.is_(None))
        ).all()
    )
    if not subs:
        return 0

    # Prefetch prefs by user
    user_ids = {s.user_id for s in subs}
    prefs = {
        p.user_id: p
        for p in session.exec(
            select(PushPreference).where(PushPreference.user_id.in_(user_ids))
        ).all()
    }

    payload = json.dumps(
        {
            "title": notification.title,
            "body": notification.body or "",
            "url": notification.link_url or "/notifications",
            "tag": notification.fingerprint,
            "severity": notification.severity or "warning",
        }
    )

    vapid_claims = {"sub": (settings.VAPID_CONTACT or "").strip()}
    vapid_private = (settings.VAPID_PRIVATE_KEY or "").strip()
    vapid_public = (settings.VAPID_PUBLIC_KEY or "").strip()

    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        logger.warning("pywebpush not installed — skip push send")
        return 0

    sent = 0
    for sub in subs:
        pref = prefs.get(sub.user_id)
        if not pref or not preference_allows(pref, notification.type):
            continue
        try:
            webpush(
                subscription_info=_subscription_info(sub),
                data=payload,
                vapid_private_key=vapid_private,
                vapid_claims=vapid_claims,
                vapid_public_key=vapid_public,
                timeout=10,
            )
            sub.last_success_at = datetime.utcnow()
            session.add(sub)
            sent += 1
        except WebPushException as e:
            status_code = None
            try:
                status_code = getattr(getattr(e, "response", None), "status_code", None)
            except Exception:
                status_code = None
            if status_code in (404, 410):
                logger.info("Removing dead push subscription %s (%s)", sub.id, status_code)
                _delete_subscription_id(session, sub.id)  # type: ignore[arg-type]
            else:
                logger.warning("Web push failed for sub %s: %s", sub.id, e)
        except Exception as e:
            logger.warning("Web push error for sub %s: %s", sub.id, e)

    if sent:
        try:
            session.commit()
        except Exception:
            pass
    return sent
