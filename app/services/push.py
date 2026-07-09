"""Web Push delivery — optional, VAPID-based, self-hosted friendly.

VAPID key resolution order:
  1. Env VAPID_PUBLIC_KEY + VAPID_PRIVATE_KEY (optional operator override)
  2. DB row PushVapidConfig (private key Fernet-encrypted)
  3. Generate once, store encrypted, reuse forever
"""
from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from sqlmodel import Session, select

from ..config import settings
from ..models import (
    Notification,
    PushPreference,
    PushSubscription,
    PushVapidConfig,
    User,
)
from ..security.encryption import decrypt_str, encrypt_str

logger = logging.getLogger(__name__)

# Notification.type → PushPreference column name
TYPE_PREF_FIELDS = {
    "backup_failed": "backup_failed",
    "os_updates": "os_updates",
    "reboot_pending": "reboot_pending",
    "container_updates": "container_updates",
    "herder_backup_failed": "herder_backup_failed",
}


@dataclass(frozen=True)
class VapidCredentials:
    public_key: str
    private_key: str
    contact: str
    source: str  # env | db | generated


def _default_contact() -> str:
    contact = (settings.VAPID_CONTACT or "").strip()
    if contact:
        return contact if contact.startswith("mailto:") else f"mailto:{contact}"
    host = (settings.PIHERDER_HOSTNAME or "").strip()
    if host and host not in ("localhost", "127.0.0.1"):
        return f"mailto:admin@{host}"
    return "mailto:piherder@localhost"


def _env_credentials() -> Optional[VapidCredentials]:
    pub = (settings.VAPID_PUBLIC_KEY or "").strip()
    priv = (settings.VAPID_PRIVATE_KEY or "").strip()
    if not pub or not priv:
        return None
    # Allow escaped newlines from single-line .env PEM
    priv = priv.replace("\\n", "\n")
    return VapidCredentials(
        public_key=pub,
        private_key=priv,
        contact=_default_contact(),
        source="env",
    )


def _row_to_credentials(row: PushVapidConfig) -> VapidCredentials:
    return VapidCredentials(
        public_key=row.public_key,
        private_key=decrypt_str(row.private_key_encrypted),
        contact=(row.contact or _default_contact()).strip(),
        source=row.source or "db",
    )


def _generate_key_pair() -> tuple[str, str]:
    """Return (public_urlsafe_b64, private_pem)."""
    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid

    v = Vapid()
    v.generate_keys()
    priv = v.private_pem().decode()
    raw = v.public_key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    pub = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return pub, priv


def _store_generated(session: Session, public_key: str, private_pem: str, contact: str) -> PushVapidConfig:
    row = PushVapidConfig(
        public_key=public_key,
        private_key_encrypted=encrypt_str(private_pem),
        contact=contact,
        created_at=datetime.utcnow(),
        source="generated",
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    logger.info("Generated and stored VAPID application server keys (id=%s)", row.id)
    return row


def ensure_vapid_keys(session: Session) -> Optional[VapidCredentials]:
    """Ensure VAPID credentials exist. Env wins; else load/create DB row.

    Safe to call repeatedly — never regenerates an existing DB pair.
    """
    env = _env_credentials()
    if env:
        return env

    try:
        row = session.exec(select(PushVapidConfig).order_by(PushVapidConfig.id)).first()
    except Exception as e:
        logger.warning("Could not load PushVapidConfig: %s", e)
        return None

    if row and row.public_key and row.private_key_encrypted:
        try:
            return _row_to_credentials(row)
        except Exception as e:
            logger.error("Failed to decrypt stored VAPID private key: %s", e)
            return None

    # Generate once
    try:
        pub, priv = _generate_key_pair()
    except Exception as e:
        logger.warning("VAPID key generation failed (py_vapid / cryptography): %s", e)
        return None

    contact = _default_contact()
    try:
        row = _store_generated(session, pub, priv, contact)
        return _row_to_credentials(row)
    except Exception as e:
        # Race: another worker inserted first
        logger.warning("Storing VAPID keys failed (retry load): %s", e)
        session.rollback()
        row = session.exec(select(PushVapidConfig).order_by(PushVapidConfig.id)).first()
        if row:
            try:
                return _row_to_credentials(row)
            except Exception:
                return None
        return None


def resolve_vapid_keys(session: Optional[Session] = None) -> Optional[VapidCredentials]:
    """Resolve credentials; opens a short-lived session if none provided."""
    env = _env_credentials()
    if env:
        return env

    own = False
    if session is None:
        from ..database import engine

        session = Session(engine)
        own = True
    try:
        return ensure_vapid_keys(session)
    finally:
        if own:
            session.close()


def is_push_configured(session: Optional[Session] = None) -> bool:
    """True when VAPID public+private are available (env or DB/auto)."""
    creds = resolve_vapid_keys(session)
    return bool(creds and creds.public_key and creds.private_key and creds.contact)


def vapid_public_key(session: Optional[Session] = None) -> Optional[str]:
    creds = resolve_vapid_keys(session)
    return creds.public_key if creds else None


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
    # Ensure keys exist before accepting subscriptions
    if not ensure_vapid_keys(session):
        raise ValueError("Web Push is not available (VAPID keys could not be created)")

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


def _vapid_private_for_webpush(private_key_pem: str):
    """pywebpush mishandles multi-line PEM strings; pass a Vapid instance instead."""
    from py_vapid import Vapid

    pem = (private_key_pem or "").replace("\\n", "\n").strip()
    if not pem:
        raise ValueError("empty VAPID private key")
    return Vapid.from_pem(pem.encode("utf-8"))


def _public_origin() -> str:
    """Absolute origin for Declarative Web Push `navigate` URLs."""
    url = (settings.PIHERDER_PUBLIC_URL or "").strip().rstrip("/")
    if url:
        return url
    host = (settings.PIHERDER_HOSTNAME or "").strip()
    if host and host not in ("localhost", "127.0.0.1"):
        return f"https://{host}"
    return ""


def _absolute_url(path_or_url: str) -> str:
    raw = (path_or_url or "/notifications").strip() or "/notifications"
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    if not raw.startswith("/"):
        raw = "/" + raw
    origin = _public_origin()
    return f"{origin}{raw}" if origin else raw


def build_push_payload(
    *,
    title: str,
    body: str = "",
    url: str = "/notifications",
    tag: str = "piherder",
    severity: str = "warning",
) -> dict[str, Any]:
    """Build a dual-compatible push body.

    Classic fields (`title`, `body`, `url`, …) keep the existing service worker working.
    Declarative Web Push shape (`web_push: 8030` + `notification`) lets Safari 18.4+ /
    iOS 18.4+ show a user-visible notification even if SW JS is delayed or cleared.
    """
    title = (title or "PiHerder").strip() or "PiHerder"
    body = body or ""
    path = (url or "/notifications").strip() or "/notifications"
    navigate = _absolute_url(path)
    return {
        # Classic (SW-driven) fields
        "title": title,
        "body": body,
        "url": path,
        "tag": tag or "piherder",
        "severity": severity or "warning",
        # Declarative Web Push (RFC 8030 homage + Notifications navigate)
        "web_push": 8030,
        "notification": {
            "title": title,
            "lang": "en",
            "dir": "ltr",
            "body": body,
            "navigate": navigate,
            "silent": False,
            "tag": tag or "piherder",
        },
    }


def _deliver_payload(
    session: Session,
    *,
    subs: list[PushSubscription],
    payload: dict[str, Any],
    creds: VapidCredentials,
) -> int:
    """Send a JSON payload to the given subscriptions. Returns successful sends."""
    if not subs:
        return 0

    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        logger.warning("pywebpush not installed — skip push send")
        return 0

    try:
        vapid_key = _vapid_private_for_webpush(creds.private_key)
    except Exception as e:
        logger.error("Invalid VAPID private key for webpush: %s", e)
        return 0

    data = json.dumps(payload)
    vapid_claims = {"sub": creds.contact}
    sent = 0
    for sub in subs:
        try:
            # pywebpush 2.x: no vapid_public_key kwarg; pass Vapid instance (not raw PEM str)
            # Urgency high: preferred for user-visible alerts (helps iOS delivery priority)
            webpush(
                subscription_info=_subscription_info(sub),
                data=data,
                vapid_private_key=vapid_key,
                vapid_claims=vapid_claims,
                timeout=10,
                ttl=86400,
                headers={"Urgency": "high"},
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


def send_for_notification(session: Session, notification: Notification) -> int:
    """Send Web Push for a newly created open notification. Returns send attempts succeeded."""
    creds = ensure_vapid_keys(session)
    if not creds:
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

    targets = [
        s
        for s in subs
        if (pref := prefs.get(s.user_id)) and preference_allows(pref, notification.type)
    ]
    payload = build_push_payload(
        title=notification.title,
        body=notification.body or "",
        url=notification.link_url or "/notifications",
        tag=notification.fingerprint or "piherder",
        severity=notification.severity or "warning",
    )
    return _deliver_payload(session, subs=targets, payload=payload, creds=creds)


def send_test_to_user(session: Session, user: User) -> dict[str, Any]:
    """Send a test push to this user's devices only (ignores event prefs).

    Returns {ok, sent, devices, error?}.
    """
    creds = ensure_vapid_keys(session)
    if not creds:
        return {"ok": False, "sent": 0, "devices": 0, "error": "vapid_unavailable"}

    subs = list_subscriptions(session, user.id)  # type: ignore[arg-type]
    if not subs:
        return {"ok": False, "sent": 0, "devices": 0, "error": "no_subscription"}

    payload = build_push_payload(
        title="PiHerder test notification",
        body="Push is working on this device. You can dismiss this.",
        url="/auth/account",
        tag=f"test-push:user:{user.id}",
        severity="info",
    )
    sent = _deliver_payload(session, subs=subs, payload=payload, creds=creds)
    return {
        "ok": sent > 0,
        "sent": sent,
        "devices": len(subs),
        "error": None if sent > 0 else "send_failed",
    }
