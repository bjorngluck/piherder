"""Admin user lifecycle helpers (create/delete side-effects)."""
from __future__ import annotations

from sqlmodel import Session, select

from ..models import (
    ApiToken,
    AuditLog,
    Notification,
    PushPreference,
    PushSubscription,
    TotpBackupCode,
    TrustedDevice,
    User,
)
from .avatars import delete_avatar_files


def detach_and_delete_user(session: Session, target: User) -> str:
    """Remove a user row and all dependent FK data.

    PostgreSQL FKs to ``user.id`` are NO ACTION (no cascade). Callers must
    clear related rows before ``DELETE FROM user`` or the transaction fails
    with IntegrityError (seen as HTTP 500 on Users → Delete).

    Policy:
      - 2FA codes, trusted devices, push subscriptions/prefs: **deleted**
      - Notifications, audit logs: **kept**, ``user_id`` set NULL
      - API tokens: keep token, null ``created_by_user_id``
      - Avatar files on disk: best-effort delete

    Returns the deleted email for audit messages.
    """
    uid = int(target.id)
    email = target.email

    for row in session.exec(select(TotpBackupCode).where(TotpBackupCode.user_id == uid)).all():
        session.delete(row)
    for row in session.exec(select(TrustedDevice).where(TrustedDevice.user_id == uid)).all():
        session.delete(row)
    for row in session.exec(select(PushSubscription).where(PushSubscription.user_id == uid)).all():
        session.delete(row)
    for row in session.exec(select(PushPreference).where(PushPreference.user_id == uid)).all():
        session.delete(row)

    for al in session.exec(select(AuditLog).where(AuditLog.user_id == uid)).all():
        al.user_id = None
        session.add(al)
    for n in session.exec(select(Notification).where(Notification.user_id == uid)).all():
        n.user_id = None
        session.add(n)
    for tok in session.exec(select(ApiToken).where(ApiToken.created_by_user_id == uid)).all():
        tok.created_by_user_id = None
        session.add(tok)

    session.delete(target)
    session.flush()

    try:
        delete_avatar_files(uid)
    except Exception:
        pass

    return email
