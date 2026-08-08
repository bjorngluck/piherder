"""Web SSH console tickets, grants, and limits (v1.2 Stream W).

Security model (high bar):
  • Kill switch default OFF (PIHERDER_SSH_CONSOLE=false)
  • operator+ only; viewer never
  • 2FA required to enroll step-up; short-lived **console grant** cookie per host
  • Each PTY opens with a **single-use** ticket bound to:
      user, server, session_version, client IP, console device id
  • **No resume**: ticket is one-shot; closed WS cannot reconnect with the same ticket
  • **Continuous revalidation** while PTY is open (session / IP / device)
  • Concurrent shell caps · idle + max session · private key never leaves herder

Browser flow:
  2FA → grant cookie (~10 min, per host) → mint ticket → WebSocket PTY (auth msg)
"""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Dict, Optional, Set, Tuple

from ..config import settings
from ..security.auth import (
    create_access_token,
    decode_token_payload,
    user_session_version,
)

# Cookie name for short-lived post-2FA grant (HttpOnly)
CONSOLE_GRANT_COOKIE = "console_grant"
# Stable per-browser device binding for console (HttpOnly, not trusted-device 2FA skip)
CONSOLE_DEVICE_COOKIE = "console_device"

# In-process ticket jti consume set + live session counters (single web worker assumed)
_lock = threading.Lock()
_consumed_jtis: Set[str] = set()
_live_by_user: Dict[int, int] = {}
_live_global: int = 0


class ConsoleDisabled(Exception):
    """Feature flag off."""


class ConsoleDenied(Exception):
    """Authz / policy / limit failure (safe message)."""


def console_enabled() -> bool:
    return bool(getattr(settings, "PIHERDER_SSH_CONSOLE", False))


def ticket_ttl_sec() -> int:
    return max(15, int(getattr(settings, "PIHERDER_SSH_CONSOLE_TICKET_SEC", 60) or 60))


def idle_sec() -> int:
    return max(60, int(getattr(settings, "PIHERDER_SSH_CONSOLE_IDLE_SEC", 900) or 900))


def max_session_sec() -> int:
    return max(120, int(getattr(settings, "PIHERDER_SSH_CONSOLE_MAX_SEC", 3600) or 3600))


def max_per_user() -> int:
    return max(1, int(getattr(settings, "PIHERDER_SSH_CONSOLE_MAX_PER_USER", 2) or 2))


def max_global() -> int:
    return max(1, int(getattr(settings, "PIHERDER_SSH_CONSOLE_MAX_GLOBAL", 10) or 10))


def grant_minutes() -> int:
    """How long a post-2FA console grant lasts (additional shells without re-TOTP)."""
    return max(2, int(getattr(settings, "PIHERDER_SSH_CONSOLE_GRANT_MIN", 10) or 10))


def require_2fa_every_shell() -> bool:
    """If true, never skip 2FA via grant cookie (each New shell re-prompts)."""
    return bool(getattr(settings, "PIHERDER_SSH_CONSOLE_REQUIRE_2FA_EVERY_SHELL", False))


def bind_ip_enabled() -> bool:
    return bool(getattr(settings, "PIHERDER_SSH_CONSOLE_BIND_IP", True))


def bind_device_enabled() -> bool:
    return bool(getattr(settings, "PIHERDER_SSH_CONSOLE_BIND_DEVICE", True))


def revalidate_sec() -> int:
    """How often to re-check session/IP/device during an open shell (seconds)."""
    return max(5, int(getattr(settings, "PIHERDER_SSH_CONSOLE_REVALIDATE_SEC", 15) or 15))


def require_enabled() -> None:
    if not console_enabled():
        raise ConsoleDisabled(
            "Web SSH console is disabled. Set PIHERDER_SSH_CONSOLE=true to enable."
        )


def _hash_binding(value: str) -> str:
    """Short stable hash for IP / device (no raw PII in JWT if not needed)."""
    raw = (value or "").strip().encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


def normalize_ip(ip: Optional[str]) -> str:
    return (ip or "").strip().split("%")[0]  # drop IPv6 zone id


def ensure_device_id(existing: Optional[str]) -> str:
    """Return existing console device id or mint a new one."""
    cur = (existing or "").strip()
    if len(cur) >= 16 and len(cur) <= 128:
        return cur
    return secrets.token_urlsafe(24)


def mint_ticket(
    *,
    user_id: int,
    server_id: int,
    session_version: int = 0,
    client_ip: Optional[str] = None,
    device_id: Optional[str] = None,
) -> str:
    """
    Short-lived single-use ticket.

    Bound to session_version, and optionally IP + console device id so the
    WebSocket cannot be opened (or continued) from another browser/network.
    """
    require_enabled()
    jti = secrets.token_urlsafe(16)
    payload: Dict[str, Any] = {
        "console": True,
        "sub": str(int(user_id)),
        "sid": int(server_id),
        "sv": int(session_version),
        "jti": jti,
    }
    if bind_ip_enabled() and client_ip:
        payload["iph"] = _hash_binding(normalize_ip(client_ip))
    if bind_device_enabled() and device_id:
        payload["did"] = _hash_binding(device_id)
    return create_access_token(
        payload,
        expires_delta=timedelta(seconds=ticket_ttl_sec()),
    )


def mint_grant(
    *,
    user_id: int,
    server_id: int,
    session_version: int = 0,
    client_ip: Optional[str] = None,
    device_id: Optional[str] = None,
) -> str:
    """Short-lived grant after successful 2FA for this host (multi-shell without re-TOTP)."""
    require_enabled()
    payload: Dict[str, Any] = {
        "console_grant": True,
        "sub": str(int(user_id)),
        "sid": int(server_id),
        "sv": int(session_version),
    }
    if bind_ip_enabled() and client_ip:
        payload["iph"] = _hash_binding(normalize_ip(client_ip))
    if bind_device_enabled() and device_id:
        payload["did"] = _hash_binding(device_id)
    return create_access_token(
        payload,
        expires_delta=timedelta(minutes=grant_minutes()),
    )


def grant_valid(
    raw: Optional[str],
    *,
    user_id: int,
    server_id: int,
    session_version: int,
    client_ip: Optional[str] = None,
    device_id: Optional[str] = None,
) -> bool:
    if require_2fa_every_shell():
        return False
    if not raw:
        return False
    payload = decode_token_payload(raw)
    if not payload or not payload.get("console_grant"):
        return False
    try:
        if int(payload.get("sub")) != int(user_id):
            return False
        if int(payload.get("sid")) != int(server_id):
            return False
        if int(payload.get("sv", 0) or 0) != int(session_version):
            return False
        if bind_ip_enabled() and payload.get("iph"):
            if _hash_binding(normalize_ip(client_ip)) != payload.get("iph"):
                return False
        if bind_device_enabled() and payload.get("did"):
            if _hash_binding(device_id or "") != payload.get("did"):
                return False
    except (TypeError, ValueError):
        return False
    return True


def _host_from_url(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    try:
        if "://" in s:
            s = s.split("://", 1)[1]
        s = s.split("/", 1)[0]
        s = s.split("?", 1)[0]
        return s.lower()
    except Exception:
        return ""


def same_site_browser_request(request) -> bool:
    """
    Console mint must come from the PiHerder UI (same host), not a cross-site form.

    - Reject Sec-Fetch-Site: cross-site
    - When Origin or Referer present, host must match request Host
    - When both missing, reject (browsers send at least one for fetch/XHR from pages;
      TestClient can set Origin explicitly)
    """
    site = (request.headers.get("sec-fetch-site") or "").strip().lower()
    if site == "cross-site":
        return False

    host = (request.headers.get("host") or "").split(",")[0].strip().lower()
    if not host:
        return False

    origin = (request.headers.get("origin") or "").strip()
    if origin and origin.lower() not in ("null",):
        oh = _host_from_url(origin)
        return bool(oh) and oh == host

    referer = (request.headers.get("referer") or "").strip()
    if referer:
        rh = _host_from_url(referer)
        return bool(rh) and rh == host

    # No Origin/Referer: block (prevents simple CSRF tools / curl without headers)
    return False


def websocket_origin_allowed(websocket) -> bool:
    """WebSocket must present Origin matching Host (browser same-origin only)."""
    host = (websocket.headers.get("host") or "").split(",")[0].strip().lower()
    origin = (websocket.headers.get("origin") or "").strip()
    if not host or not origin or origin.lower() == "null":
        return False
    return _host_from_url(origin) == host


def consume_ticket(
    raw: str,
    *,
    user_id: int,
    server_id: int,
    session_version: int = 0,
    client_ip: Optional[str] = None,
    device_id: Optional[str] = None,
) -> dict:
    """
    Validate and **single-use consume** a ticket.

    Once consumed, the ticket cannot open another WebSocket (no resume / reconnect).
    Returns payload. Raises ConsoleDenied on failure.
    """
    require_enabled()
    payload = decode_token_payload(raw or "")
    if not payload or not payload.get("console"):
        raise ConsoleDenied("Invalid or expired console ticket")
    try:
        tid = int(payload.get("sub"))
        sid = int(payload.get("sid"))
        tsv = int(payload.get("sv", 0) or 0)
    except (TypeError, ValueError):
        raise ConsoleDenied("Invalid console ticket")
    if tid != int(user_id) or sid != int(server_id):
        raise ConsoleDenied("Console ticket does not match this session")
    if tsv != int(session_version):
        raise ConsoleDenied("Console ticket invalidated (session changed — sign in again)")
    if bind_ip_enabled() and payload.get("iph"):
        if _hash_binding(normalize_ip(client_ip)) != payload.get("iph"):
            raise ConsoleDenied("Console ticket bound to a different network address")
    if bind_device_enabled() and payload.get("did"):
        if _hash_binding(device_id or "") != payload.get("did"):
            raise ConsoleDenied("Console ticket bound to a different browser/device")
    jti = str(payload.get("jti") or "")
    if not jti:
        raise ConsoleDenied("Invalid console ticket")
    with _lock:
        if jti in _consumed_jtis:
            raise ConsoleDenied("Console ticket already used (cannot resume)")
        if len(_consumed_jtis) > 5000:
            _consumed_jtis.clear()
        _consumed_jtis.add(jti)
    return payload


def binding_still_valid(
    ticket_payload: dict,
    *,
    client_ip: Optional[str],
    device_id: Optional[str],
) -> Tuple[bool, str]:
    """Check IP/device still match ticket binding (for continuous revalidation)."""
    if bind_ip_enabled() and ticket_payload.get("iph"):
        if _hash_binding(normalize_ip(client_ip)) != ticket_payload.get("iph"):
            return False, "ip_changed"
    if bind_device_enabled() and ticket_payload.get("did"):
        if _hash_binding(device_id or "") != ticket_payload.get("did"):
            return False, "device_changed"
    return True, ""


def session_still_valid(
    session,
    *,
    user_id: int,
    expected_sv: int,
) -> Tuple[bool, str]:
    """
    Re-load user and confirm session_version / active / operator+.

    Call periodically during an open shell so logout, password change, admin
    session revoke, or demotion kills the PTY immediately.
    """
    from ..models import User
    from ..security.auth import role_at_least, ROLE_OPERATOR

    user = session.get(User, int(user_id))
    if not user or not user.is_active:
        return False, "user_inactive"
    if user_session_version(user) != int(expected_sv):
        return False, "session_revoked"
    if not role_at_least(user, ROLE_OPERATOR):
        return False, "role_lost"
    return True, ""


def try_acquire_slot(user_id: int) -> None:
    """Reserve a live console slot or raise ConsoleDenied."""
    require_enabled()
    uid = int(user_id)
    with _lock:
        global _live_global
        if _live_global >= max_global():
            raise ConsoleDenied("Too many active consoles on this instance")
        n = _live_by_user.get(uid, 0)
        if n >= max_per_user():
            raise ConsoleDenied("Too many active consoles for your account")
        _live_by_user[uid] = n + 1
        _live_global += 1


def release_slot(user_id: int) -> None:
    uid = int(user_id)
    with _lock:
        global _live_global
        n = _live_by_user.get(uid, 0)
        if n <= 1:
            _live_by_user.pop(uid, None)
        else:
            _live_by_user[uid] = n - 1
        if _live_global > 0:
            _live_global -= 1


def live_counts() -> Tuple[int, Dict[int, int]]:
    with _lock:
        return _live_global, dict(_live_by_user)


def slots_remaining(user_id: int) -> int:
    g, by_u = live_counts()
    per = max_per_user() - by_u.get(int(user_id), 0)
    glob = max_global() - g
    return max(0, min(per, glob))


def reset_runtime_state_for_tests() -> None:
    """Test helper — clear tickets and counters."""
    global _live_global
    with _lock:
        _consumed_jtis.clear()
        _live_by_user.clear()
        _live_global = 0


@dataclass
class ConsoleSessionMeta:
    user_id: int
    server_id: int
    opened_at: float = field(default_factory=time.time)
    client_ip: Optional[str] = None
