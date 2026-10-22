"""Admin inbox alerts for a new user, a new API token, a scope gain, a promotion to admin, or a 2FA policy change.

One catalog type (``admin_change``, warning). Each event keeps its own
fingerprint so a later change does not replace an open row. The body names
the actor and the client IP. It never includes a password or a token.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from sqlmodel import Session

logger = logging.getLogger(__name__)

TYPE_ID = "admin_change"
_MAX_BODY = 300


def actor_label(user: Any) -> str:
    """Display name and email, or the email alone. Never a secret."""
    if user is None:
        return "unknown"
    email = str(getattr(user, "email", "") or "").strip()
    name = str(getattr(user, "display_name", "") or "").strip()
    if name and email and name.casefold() != email.casefold():
        label = f"{name} ({email})"
    else:
        label = email or name
    if not label:
        uid = getattr(user, "id", None)
        label = f"user {uid}" if uid else "unknown"
    return " ".join(label.split())[:120]


def client_ip_label() -> str:
    """Same IP the audit row would store, or ``unknown`` when there is none."""
    try:
        from .audit_write import resolve_client_ip

        ip = resolve_client_ip()
    except Exception:
        ip = None
    text = (ip or "").strip()
    return text or "unknown"


def _with_actor(detail: str, actor: str, ip: str) -> str:
    """Keep the actor and the client IP even when the detail is long."""
    suffix = f" Actor: {actor}. Client IP: {ip}."
    text = " ".join((detail or "").split())
    if text and not text.endswith("."):
        text += "."
    room = _MAX_BODY - len(suffix)
    if room < 1:
        return suffix.strip()[:_MAX_BODY]
    if not text:
        return suffix.strip()[:_MAX_BODY]
    return text[:room].rstrip() + suffix


def _open(
    session: Session,
    *,
    fingerprint: str,
    title: str,
    detail: str,
    actor: Any,
    link_url: str,
) -> None:
    """Write one open alert. A failure here must not undo the admin action."""
    try:
        from .notifications import upsert_notification

        who = actor_label(actor)
        upsert_notification(
            session,
            fingerprint=fingerprint[:200],
            type=TYPE_ID,
            title=title,
            body=_with_actor(detail, who, client_ip_label()),
            link_url=link_url,
            severity="warning",
            user_id=getattr(actor, "id", None) if actor is not None else None,
        )
    except Exception:
        logger.debug("admin alert skipped", exc_info=True)


def notify_user_created(
    session: Session,
    *,
    actor: Any,
    email: str,
    role: str,
    user_id: int,
    how: str = "created",
) -> None:
    """Alert when an account is created, registered, or provisioned by SSO."""
    safe_email = " ".join((email or "").split())[:120] or "unknown"
    safe_role = " ".join((role or "").split())[:40] or "unknown"
    if how == "registered":
        detail = f"Registered {safe_email} as {safe_role}."
    elif how == "provisioned":
        detail = f"SSO provisioned {safe_email} as {safe_role}."
    else:
        detail = f"Created {safe_email} as {safe_role}."
    _open(
        session,
        fingerprint=f"admin_user_created:{int(user_id)}",
        title="User created",
        detail=detail,
        actor=actor,
        link_url="/auth/users",
    )


def notify_api_token_created(
    session: Session,
    *,
    actor: Any,
    token_id: int,
    name: str,
) -> None:
    """Alert for a new API token. The name is the label, never the secret."""
    safe_name = " ".join((name or "unnamed").split())[:80] or "unnamed"
    _open(
        session,
        fingerprint=f"admin_api_token_created:{int(token_id)}",
        title="API token created",
        detail=f"API token #{int(token_id)} {safe_name!r} was created.",
        actor=actor,
        link_url="/herder-backups?tab=api&api_panel=tokens",
    )


def notify_api_token_scopes_gained(
    session: Session,
    *,
    actor: Any,
    token_id: int,
    name: str,
    gained: Any,
) -> None:
    """Alert when an existing token gains scopes. Never the secret or prefix."""
    added: list[str] = []
    for raw in gained or []:
        text = " ".join(str(raw).split()).lower()
        if text and text not in added:
            added.append(text)
    added.sort()
    if not added:
        return
    safe_name = " ".join((name or "unnamed").split())[:80] or "unnamed"
    bits = [
        f"API token #{int(token_id)} {safe_name!r} gained scopes: {', '.join(added)}"
    ]
    if "admin" in added:
        bits.append("The admin scope was added")
    if "files" in added:
        bits.append("The files scope was added")
    _open(
        session,
        fingerprint=f"admin_api_token_scopes:{int(token_id)}:{time.time_ns()}",
        title="API token scopes gained",
        detail=". ".join(bits) + ".",
        actor=actor,
        link_url="/herder-backups?tab=api&api_panel=tokens",
    )


def notify_user_promoted_to_admin(
    session: Session,
    *,
    actor: Any,
    email: str,
    user_id: int,
) -> None:
    """Alert when an existing user becomes an admin."""
    safe_email = " ".join((email or "").split())[:120] or "unknown"
    _open(
        session,
        fingerprint=f"admin_user_promoted:{int(user_id)}:{time.time_ns()}",
        title="User promoted to admin",
        detail=f"Promoted {safe_email} to admin.",
        actor=actor,
        link_url="/auth/users",
    )


def notify_2fa_setting_change(
    session: Session,
    *,
    actor: Any,
    prev_scope: str,
    new_scope: str,
    prev_template: bool,
    new_template: bool,
) -> None:
    """Alert only when force 2FA or template 2FA actually changes."""
    bits: list[str] = []
    old_scope = (prev_scope or "off").strip().lower()[:32]
    new = (new_scope or "off").strip().lower()[:32]
    if old_scope != new:
        bits.append(f"Force 2FA changed from {old_scope} to {new}")
    if bool(prev_template) != bool(new_template):
        bits.append(
            "Template 2FA changed from "
            + ("on" if prev_template else "off")
            + " to "
            + ("on" if new_template else "off")
        )
    if not bits:
        return
    actor_id = getattr(actor, "id", None) or 0
    _open(
        session,
        fingerprint=f"admin_2fa_changed:{actor_id}:{time.time_ns()}",
        title="2FA setting changed",
        detail=". ".join(bits) + ".",
        actor=actor,
        link_url="/herder-backups?tab=general",
    )
