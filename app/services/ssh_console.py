"""Web SSH console tickets, grants, and limits (v1.2 Stream W).

Security model (high bar):
  • Kill switch default OFF (PIHERDER_SSH_CONSOLE=false)
  • operator+ only; viewer never
  • 2FA required to enroll step-up; short-lived **console grant** cookie per host
  • Each PTY opens with a **single-use** ticket bound to:
      user, server, session_version, client IP, console device id
  • Ticket is single-use to open a PTY; **soft resume** after browser WebSocket drop
    (app switch) parks the SSH session until idle/max timeout, then reconnects
  • **Continuous revalidation** while PTY is open (session / IP / device)
  • Concurrent shell caps · idle + max session · private key never leaves herder

Browser flow:
  2FA → grant → ticket → WebSocket PTY · on tab sleep: park → resume token reclaim
"""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Dict, Optional, Tuple

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

# In-process ticket jti consume map (jti → consumed_at) + live session counters
_lock = threading.Lock()
_consumed_jtis: Dict[str, float] = {}
_live_by_user: Dict[int, int] = {}
_live_global: int = 0
# Parked PTYs after browser WebSocket drop (app switch / tab sleep) — resume until idle/max
_held_sessions: Dict[str, "HeldConsole"] = {}


class ConsoleDisabled(Exception):
    """Feature flag off."""


class ConsoleDenied(Exception):
    """Authz / policy / limit failure (safe message)."""


def console_enabled() -> bool:
    """Production: PIHERDER_SSH_CONSOLE flag. Demo (D5): always on (simulated only)."""
    from .demo import demo_mode

    if demo_mode():
        return True
    return bool(getattr(settings, "PIHERDER_SSH_CONSOLE", False))


def is_demo_console() -> bool:
    """True when console sessions must never open live SSH (public demo)."""
    from .demo_console import is_simulated_console

    return is_simulated_console()


def demo_console_skip_2fa() -> bool:
    """Shared demo account must not require 2FA (visitors cannot enroll/lock each other)."""
    return is_demo_console()


def demo_console_allow_viewer() -> bool:
    """Demo console is available to the shared viewer role."""
    return is_demo_console()


def ticket_ttl_sec() -> int:
    return max(15, int(getattr(settings, "PIHERDER_SSH_CONSOLE_TICKET_SEC", 60) or 60))


def idle_sec() -> int:
    return max(60, int(getattr(settings, "PIHERDER_SSH_CONSOLE_IDLE_SEC", 900) or 900))


def max_session_sec() -> int:
    return max(120, int(getattr(settings, "PIHERDER_SSH_CONSOLE_MAX_SEC", 3600) or 3600))


def max_per_user() -> int:
    return max(1, int(getattr(settings, "PIHERDER_SSH_CONSOLE_MAX_PER_USER", 4) or 4))


def max_global() -> int:
    return max(1, int(getattr(settings, "PIHERDER_SSH_CONSOLE_MAX_GLOBAL", 20) or 20))


def default_scrollback() -> int:
    """Default xterm scrollback lines (client can raise within UI caps)."""
    return max(500, min(50000, int(getattr(settings, "PIHERDER_SSH_CONSOLE_SCROLLBACK", 2000) or 2000)))


def grant_minutes() -> int:
    """How long a post-2FA console grant lasts (additional shells without re-TOTP)."""
    return max(2, int(getattr(settings, "PIHERDER_SSH_CONSOLE_GRANT_MIN", 10) or 10))


def require_2fa_every_shell() -> bool:
    """If true, never skip 2FA via grant cookie (each New shell re-prompts)."""
    return bool(getattr(settings, "PIHERDER_SSH_CONSOLE_REQUIRE_2FA_EVERY_SHELL", False))


def allow_backup_codes() -> bool:
    """Backup codes are weak for shell step-up (paper/stolen codes). Default off."""
    return bool(getattr(settings, "PIHERDER_SSH_CONSOLE_ALLOW_BACKUP_CODES", False))


def prefer_passkey() -> bool:
    """UI + messaging prefer WebAuthn when the user has a passkey enrolled."""
    return bool(getattr(settings, "PIHERDER_SSH_CONSOLE_PREFER_PASSKEY", True))


def require_passkey_if_enrolled() -> bool:
    """If true and user has passkeys, reject TOTP for console (passkey only)."""
    return bool(getattr(settings, "PIHERDER_SSH_CONSOLE_REQUIRE_PASSKEY", False))


def bind_ip_enabled() -> bool:
    return bool(getattr(settings, "PIHERDER_SSH_CONSOLE_BIND_IP", True))


def bind_device_enabled() -> bool:
    return bool(getattr(settings, "PIHERDER_SSH_CONSOLE_BIND_DEVICE", True))


def revalidate_sec() -> int:
    """How often to re-check session/IP/device during an open shell (seconds)."""
    return max(5, int(getattr(settings, "PIHERDER_SSH_CONSOLE_REVALIDATE_SEC", 10) or 10))


def hold_sec() -> int:
    """Max time to keep a detached PTY after WebSocket drop.

    0 (default) = hold until idle_sec from last activity or max_session_sec from start.
    Positive value caps the detached window (still also subject to idle/max).
    """
    raw = int(getattr(settings, "PIHERDER_SSH_CONSOLE_HOLD_SEC", 0) or 0)
    if raw <= 0:
        return 0
    return max(30, raw)


def require_enabled() -> None:
    if not console_enabled():
        raise ConsoleDisabled(
            "Web SSH console is disabled. Set PIHERDER_SSH_CONSOLE=true to enable."
        )


def open_session_channel(server_snap: Any) -> tuple[Any, Any]:
    """Open PTY channel for a host.

    Demo mode: simulated shell (no network). Production: Paramiko SSH.
    """
    if is_demo_console():
        from .demo_console import open_demo_shell

        label = (
            getattr(server_snap, "hostname", None)
            or getattr(server_snap, "name", None)
            or "demo-host"
        )
        user = getattr(server_snap, "ssh_username", None) or "demo"
        return open_demo_shell(host_label=str(label), username=str(user))

    from . import ssh as ssh_service

    client = ssh_service.get_ssh_client(server_snap)
    channel = client.invoke_shell(term="xterm-256color", width=120, height=40)
    channel.settimeout(0.0)
    return client, channel


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
    server_id: int = 0,
    session_version: int = 0,
    client_ip: Optional[str] = None,
    device_id: Optional[str] = None,
) -> str:
    """Short-lived grant after successful 2FA — valid for **all hosts** (fleet-wide).

    One passkey/TOTP step-up covers multi-host console until the grant expires.
    ``server_id`` is recorded for audit only (not enforced on validation).
    """
    require_enabled()
    payload: Dict[str, Any] = {
        "console_grant": True,
        "fleet": 1,  # all hosts
        "sub": str(int(user_id)),
        "sv": int(session_version),
    }
    # Optional breadcrumb of last host that minted the grant (not checked)
    if server_id:
        payload["last_sid"] = int(server_id)
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
    server_id: int = 0,
    session_version: int,
    client_ip: Optional[str] = None,
    device_id: Optional[str] = None,
) -> bool:
    """Return True if grant cookie allows opening a shell without re-2FA.

    Fleet-wide: ``server_id`` is ignored (one step-up covers every host).
    Legacy per-host grants (``sid`` only, no ``fleet``) still accepted for any host
    so existing cookies keep working until they expire.
    """
    del server_id  # fleet-wide; keep param for call-site compatibility
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
    if not _consume_jti(jti):
        raise ConsoleDenied("Console ticket already used (cannot resume)")
    return payload


def _consume_jti(jti: str) -> bool:
    """First consume wins. Redis NX when available; never wipe the whole set."""
    ttl = ticket_ttl_sec() + 120
    now = time.time()
    try:
        from .server_job_lock import _get_redis

        r = _get_redis()
    except Exception:
        r = None
    if r is not None:
        try:
            ok = r.set(f"piherder:console:jti:{jti}", "1", nx=True, ex=int(ttl))
            if not ok:
                return False
        except Exception:
            r = None
    with _lock:
        stale = [k for k, ts in _consumed_jtis.items() if now - ts > ttl]
        for k in stale:
            _consumed_jtis.pop(k, None)
        if jti in _consumed_jtis:
            return False
        _consumed_jtis[jti] = now
    return True


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

    Demo (D5): shared viewer is allowed (simulated console only).
    """
    from ..models import User
    from ..security.auth import role_at_least, ROLE_OPERATOR

    user = session.get(User, int(user_id))
    if not user or not user.is_active:
        return False, "user_inactive"
    if user_session_version(user) != int(expected_sv):
        return False, "session_revoked"
    if is_demo_console():
        return True, ""
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
        # Close any parked SSH without requiring live clients in unit tests
        for hid in list(_held_sessions.keys()):
            h = _held_sessions.pop(hid, None)
            if h:
                h.dead = True
                try:
                    if h.channel is not None:
                        h.channel.close()
                except Exception:
                    pass
                try:
                    if h.client is not None:
                        h.client.close()
                except Exception:
                    pass


# Output buffer while browser is backgrounded (replayed on resume)
_HELD_BUF_MAX = 256 * 1024


@dataclass
class HeldConsole:
    """SSH PTY parked after WebSocket drop — claimable via resume token."""

    resume_id: str
    user_id: int
    server_id: int
    session_version: int
    ticket_payload: Dict[str, Any]
    device_id: str
    client: Any
    channel: Any
    started_mono: float
    last_activity_mono: float
    held_at_mono: float
    server_hostname: str
    out_buf: bytearray = field(default_factory=bytearray)
    dead: bool = False

    def append_out(self, data: bytes) -> None:
        if not data:
            return
        self.out_buf.extend(data)
        if len(self.out_buf) > _HELD_BUF_MAX:
            # keep newest
            self.out_buf = bytearray(self.out_buf[-_HELD_BUF_MAX:])

    def take_out(self) -> bytes:
        if not self.out_buf:
            return b""
        data = bytes(self.out_buf)
        self.out_buf.clear()
        return data


def mint_resume_id() -> str:
    return secrets.token_urlsafe(24)


def park_console(held: HeldConsole) -> str:
    """Store a detached PTY for resume. Slot stays acquired."""
    with _lock:
        # Replace any previous entry with same id
        old = _held_sessions.get(held.resume_id)
        if old and old is not held:
            old.dead = True
        _held_sessions[held.resume_id] = held
    return held.resume_id


def get_held(resume_id: str) -> Optional[HeldConsole]:
    with _lock:
        h = _held_sessions.get(resume_id or "")
        if h and h.dead:
            return None
        return h


def claim_resume(
    resume_id: str,
    *,
    user_id: int,
    server_id: int,
    session_version: int,
    device_id: Optional[str] = None,
    client_ip: Optional[str] = None,
) -> HeldConsole:
    """
    Take a parked PTY for a new WebSocket.

    Bound to user / server / session_version / device (same bar as tickets).
    IP re-checked if ticket payload carried an IP hash.
    """
    require_enabled()
    rid = (resume_id or "").strip()
    if not rid:
        raise ConsoleDenied("Missing resume token")
    with _lock:
        held = _held_sessions.pop(rid, None)
    if not held or held.dead:
        raise ConsoleDenied("Console session expired or already resumed")
    try:
        if int(held.user_id) != int(user_id) or int(held.server_id) != int(server_id):
            raise ConsoleDenied("Resume token does not match this host/session")
        if int(held.session_version) != int(session_version):
            raise ConsoleDenied("Login session changed — open a new shell")
        if bind_device_enabled() and held.device_id:
            if (device_id or "") != held.device_id:
                # also allow hash match via ticket_payload
                ok, reason = binding_still_valid(
                    held.ticket_payload, client_ip=client_ip, device_id=device_id
                )
                if not ok and reason == "device_changed":
                    raise ConsoleDenied("Resume bound to a different browser/device")
        if bind_ip_enabled() and held.ticket_payload.get("iph"):
            ok, reason = binding_still_valid(
                held.ticket_payload, client_ip=client_ip, device_id=device_id
            )
            if not ok and reason == "ip_changed":
                # Mobile networks often change IP while app is backgrounded —
                # allow resume if device binding still matches when device bind is on.
                if not (bind_device_enabled() and held.device_id and device_id == held.device_id):
                    raise ConsoleDenied("Resume bound to a different network address")
        # Expired by wall clocks?
        now = time.monotonic()
        if now - held.last_activity_mono > idle_sec():
            raise ConsoleDenied("Console idle timeout while detached")
        if now - held.started_mono > max_session_sec():
            raise ConsoleDenied("Console max session while detached")
        hs = hold_sec()
        if hs and (now - held.held_at_mono > hs):
            raise ConsoleDenied("Console hold window expired")
    except ConsoleDenied:
        # destroy SSH if claim failed
        _destroy_held_resources(held, release_slot_user=held.user_id)
        raise
    return held


def _destroy_held_resources(held: HeldConsole, *, release_slot_user: Optional[int]) -> None:
    held.dead = True
    try:
        if held.channel is not None:
            held.channel.close()
    except Exception:
        pass
    try:
        if held.client is not None:
            held.client.close()
    except Exception:
        pass
    if release_slot_user is not None:
        try:
            release_slot(int(release_slot_user))
        except Exception:
            pass


def destroy_held(resume_id: str, *, reason: str = "") -> bool:
    """Fully tear down a parked console (idle/max/bye). Releases slot."""
    del reason  # for logging callers
    with _lock:
        held = _held_sessions.pop(resume_id or "", None)
    if not held:
        return False
    _destroy_held_resources(held, release_slot_user=held.user_id)
    return True


def discard_parked_for_user(
    resume_id: str,
    *,
    user_id: int,
    server_id: Optional[int] = None,
) -> bool:
    """Client-initiated discard of a parked PTY (close shell / host tab).

    Releases the concurrent slot so new shells can open. Only the owning user
    may discard; optional server_id must match when provided.
    """
    rid = (resume_id or "").strip()
    if not rid:
        return False
    with _lock:
        held = _held_sessions.get(rid)
        if not held or held.dead:
            _held_sessions.pop(rid, None)
            return False
        if int(held.user_id) != int(user_id):
            return False
        if server_id is not None and int(held.server_id) != int(server_id):
            return False
        held = _held_sessions.pop(rid, None)
    if not held:
        return False
    _destroy_held_resources(held, release_slot_user=held.user_id)
    return True


def discard_all_parked_for_user(user_id: int) -> int:
    """Tear down every parked console for a user. Returns count destroyed."""
    uid = int(user_id)
    with _lock:
        ids = [rid for rid, h in _held_sessions.items() if h and int(h.user_id) == uid]
    n = 0
    for rid in ids:
        if destroy_held(rid, reason="user_discard_all"):
            n += 1
    return n


def held_count() -> int:
    with _lock:
        return len(_held_sessions)


def list_held_ids() -> list:
    with _lock:
        return list(_held_sessions.keys())


def drain_held_channel(held: HeldConsole) -> bool:
    """
    Non-blocking drain of SSH → buffer. Returns False if channel died.
    Call from hold-watch loop.
    """
    if held.dead or held.channel is None:
        return False
    try:
        ch = held.channel
        if ch.exit_status_ready() and not ch.recv_ready():
            return False
        progressed = False
        while ch.recv_ready():
            data = ch.recv(8192)
            if not data:
                return False
            held.append_out(data)
            held.last_activity_mono = time.monotonic()
            progressed = True
        while ch.recv_stderr_ready():
            data = ch.recv_stderr(4096)
            if data:
                held.append_out(data)
                held.last_activity_mono = time.monotonic()
                progressed = True
        del progressed
        return True
    except Exception:
        return False


def held_should_expire(held: HeldConsole) -> Optional[str]:
    now = time.monotonic()
    if now - held.last_activity_mono > idle_sec():
        return "idle"
    if now - held.started_mono > max_session_sec():
        return "max"
    hs = hold_sec()
    if hs and (now - held.held_at_mono > hs):
        return "hold"
    return None


@dataclass
class ConsoleSessionMeta:
    user_id: int
    server_id: int
    opened_at: float = field(default_factory=time.time)
    client_ip: Optional[str] = None
