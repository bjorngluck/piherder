"""G1-lite — email password recovery when SMTP is configured."""
from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional

from sqlmodel import Session, select

from ..models import PasswordResetToken, User
from . import alert_channels as ch

logger = logging.getLogger(__name__)

TOKEN_BYTES = 32
TOKEN_TTL_HOURS = 1
MAX_OPEN_PER_USER = 3


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_reset_token(
    session: Session,
    user: User,
    *,
    request_ip: str = "",
) -> str:
    """Create a reset token; return raw token (show once in email)."""
    # Invalidate excess open tokens
    open_rows = list(
        session.exec(
            select(PasswordResetToken).where(
                PasswordResetToken.user_id == user.id,
                PasswordResetToken.used_at == None,  # noqa: E711
            )
        ).all()
    )
    now = datetime.utcnow()
    for r in open_rows:
        if r.expires_at < now:
            session.delete(r)
    session.flush()
    open_rows = [r for r in open_rows if r.expires_at >= now]
    while len(open_rows) >= MAX_OPEN_PER_USER:
        oldest = min(open_rows, key=lambda x: x.created_at)
        session.delete(oldest)
        open_rows.remove(oldest)

    raw = secrets.token_urlsafe(TOKEN_BYTES)
    row = PasswordResetToken(
        user_id=int(user.id),  # type: ignore[arg-type]
        token_hash=_hash_token(raw),
        expires_at=now + timedelta(hours=TOKEN_TTL_HOURS),
        created_at=now,
        request_ip=(request_ip or "")[:80] or None,
    )
    session.add(row)
    session.commit()
    return raw


def consume_token(session: Session, raw_token: str) -> Optional[User]:
    """Validate token and mark used; return user or None."""
    t = (raw_token or "").strip()
    if not t or len(t) > 200:
        return None
    th = _hash_token(t)
    row = session.exec(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == th)
    ).first()
    if not row or row.used_at is not None:
        return None
    now = datetime.utcnow()
    if row.expires_at < now:
        return None
    user = session.get(User, row.user_id)
    if not user or not user.is_active:
        return None
    row.used_at = now
    session.add(row)
    session.commit()
    return user


def request_reset_email(
    session: Session,
    email: str,
    *,
    base_url: str,
    request_ip: str = "",
) -> dict:
    """Always return generic ok to avoid email enumeration.

    Sends mail only if user exists and SMTP password-reset is available.
    """
    if not ch.password_reset_available():
        return {"ok": False, "error": "email recovery is not enabled"}

    addr = (email or "").strip().lower()
    user = session.exec(select(User).where(User.email == addr)).first()
    if not user or not user.is_active:
        return {"ok": True, "sent": False}

    raw = create_reset_token(session, user, request_ip=request_ip)
    base = (base_url or "").rstrip("/")
    link = f"{base}/auth/reset-password?token={raw}"
    body = (
        f"Reset your PiHerder password\n\n"
        f"Use this link within {TOKEN_TTL_HOURS} hour(s):\n{link}\n\n"
        f"If you did not request this, ignore this email. "
        f"Your password will not change.\n"
    )
    result = ch.send_email(
        to=user.email,
        subject="PiHerder password reset",
        body_text=body,
    )
    if not result.get("ok"):
        logger.warning("password reset email failed: %s", result.get("error"))
        return {"ok": False, "error": result.get("error") or "send failed"}
    return {"ok": True, "sent": True}
