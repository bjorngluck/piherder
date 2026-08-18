"""Admin user lifecycle helpers (create/delete/credential recovery)."""
from __future__ import annotations

from datetime import datetime

from sqlmodel import Session, select

from ..models import (
    ApiToken,
    AuditLog,
    Notification,
    OidcIdentity,
    PasswordResetToken,
    PortAnnotation,
    PushPreference,
    PushSubscription,
    RuntimeEdge,
    TotpBackupCode,
    TrustedDevice,
    User,
    UserFavourite,
    WebAuthnCredential,
)
from ..security.auth import get_password_hash, revoke_all_trusted_devices, user_session_version
from .avatars import delete_avatar_files


def bump_session_version(session: Session, user: User) -> int:
    """Invalidate all interactive session JWTs for this user. Returns new version."""
    new_v = user_session_version(user) + 1
    user.session_version = new_v
    user.updated_at = datetime.utcnow()
    session.add(user)
    return new_v


def clear_user_2fa(session: Session, user: User) -> None:
    """Wipe TOTP secret, passkeys, disable 2FA, delete backup codes (trusted devices separate)."""
    user.totp_enabled = False
    user.totp_secret_encrypted = None
    user.totp_confirmed_at = None
    user.updated_at = datetime.utcnow()
    session.add(user)
    uid = int(user.id)
    for row in session.exec(select(TotpBackupCode).where(TotpBackupCode.user_id == uid)).all():
        session.delete(row)
    for row in session.exec(
        select(WebAuthnCredential).where(WebAuthnCredential.user_id == uid)
    ).all():
        session.delete(row)


def set_temporary_password(session: Session, user: User, password: str) -> None:
    """Set hashed password and force change on next full login."""
    user.hashed_password = get_password_hash(password)
    user.must_change_password = True
    user.updated_at = datetime.utcnow()
    session.add(user)


def admin_reset_password(
    session: Session,
    user: User,
    password: str,
    *,
    clear_2fa: bool = False,
) -> None:
    """Admin recovery: temp password, force change, kill sessions (+ optional clear 2FA)."""
    set_temporary_password(session, user, password)
    if clear_2fa:
        clear_user_2fa(session, user)
    revoke_all_trusted_devices(session, int(user.id))
    bump_session_version(session, user)
    session.flush()


def admin_clear_2fa_only(session: Session, user: User) -> None:
    """Admin clears 2FA + trusted devices + sessions (password unchanged)."""
    clear_user_2fa(session, user)
    revoke_all_trusted_devices(session, int(user.id))
    bump_session_version(session, user)
    session.flush()


def admin_sign_out_sessions(session: Session, user: User) -> int:
    """Force-logout all browsers for user (JWT session_version bump)."""
    revoke_all_trusted_devices(session, int(user.id))
    v = bump_session_version(session, user)
    session.flush()
    return v


def detach_and_delete_user(session: Session, target: User) -> str:
    """Remove a user row and all dependent FK data.

    PostgreSQL FKs to ``user.id`` are NO ACTION (no cascade). Callers must
    clear related rows before ``DELETE FROM user`` or the transaction fails
    with IntegrityError (seen as HTTP 500 on Users → Delete).

    Policy:
      - 2FA codes, passkeys, trusted devices, push, pins, OIDC links, reset tokens: **deleted**
      - Notifications, audit logs: **kept**, ``user_id`` set NULL
      - API tokens / map edges / port notes: keep row, null creator user_id
      - Avatar files on disk: best-effort delete

    Returns the deleted email for audit messages.
    """
    uid = int(target.id)
    email = target.email

    for row in session.exec(select(TotpBackupCode).where(TotpBackupCode.user_id == uid)).all():
        session.delete(row)
    for row in session.exec(
        select(WebAuthnCredential).where(WebAuthnCredential.user_id == uid)
    ).all():
        session.delete(row)
    for row in session.exec(select(TrustedDevice).where(TrustedDevice.user_id == uid)).all():
        session.delete(row)
    for row in session.exec(select(PushSubscription).where(PushSubscription.user_id == uid)).all():
        session.delete(row)
    for row in session.exec(select(PushPreference).where(PushPreference.user_id == uid)).all():
        session.delete(row)
    for row in session.exec(select(UserFavourite).where(UserFavourite.user_id == uid)).all():
        session.delete(row)
    for row in session.exec(select(OidcIdentity).where(OidcIdentity.user_id == uid)).all():
        session.delete(row)
    for row in session.exec(
        select(PasswordResetToken).where(PasswordResetToken.user_id == uid)
    ).all():
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
    for edge in session.exec(
        select(RuntimeEdge).where(RuntimeEdge.created_by_user_id == uid)
    ).all():
        edge.created_by_user_id = None
        session.add(edge)
    for pa in session.exec(
        select(PortAnnotation).where(PortAnnotation.created_by_user_id == uid)
    ).all():
        pa.created_by_user_id = None
        session.add(pa)

    session.delete(target)
    session.flush()

    try:
        delete_avatar_files(uid)
    except Exception:
        pass

    return email
