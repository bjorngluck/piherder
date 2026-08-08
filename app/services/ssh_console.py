"""Web SSH console tickets and limits (v1.2 Stream W).

Browser never receives host PEM. Flow:
  operator+ → (2FA step-up) → short-lived single-use ticket → WebSocket PTY.

Kill switch: PIHERDER_SSH_CONSOLE=false (default).
"""
from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Dict, Optional, Set, Tuple

from ..config import settings
from ..security.auth import create_access_token, decode_token_payload

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


def require_enabled() -> None:
    if not console_enabled():
        raise ConsoleDisabled(
            "Web SSH console is disabled. Set PIHERDER_SSH_CONSOLE=true to enable."
        )


def mint_ticket(*, user_id: int, server_id: int) -> str:
    """Return a short-lived JWT ticket (not yet consumed)."""
    require_enabled()
    jti = secrets.token_urlsafe(16)
    return create_access_token(
        {
            "console": True,
            "sub": str(int(user_id)),
            "sid": int(server_id),
            "jti": jti,
        },
        expires_delta=timedelta(seconds=ticket_ttl_sec()),
    )


def consume_ticket(raw: str, *, user_id: int, server_id: int) -> dict:
    """
    Validate and single-use consume a ticket for this user+server.
    Returns payload. Raises ConsoleDenied on failure.
    """
    require_enabled()
    payload = decode_token_payload(raw or "")
    if not payload or not payload.get("console"):
        raise ConsoleDenied("Invalid or expired console ticket")
    try:
        tid = int(payload.get("sub"))
        sid = int(payload.get("sid"))
    except (TypeError, ValueError):
        raise ConsoleDenied("Invalid console ticket")
    if tid != int(user_id) or sid != int(server_id):
        raise ConsoleDenied("Console ticket does not match this session")
    jti = str(payload.get("jti") or "")
    if not jti:
        raise ConsoleDenied("Invalid console ticket")
    with _lock:
        if jti in _consumed_jtis:
            raise ConsoleDenied("Console ticket already used")
        # Soft bound memory: drop old markers occasionally
        if len(_consumed_jtis) > 5000:
            _consumed_jtis.clear()
        _consumed_jtis.add(jti)
    return payload


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
