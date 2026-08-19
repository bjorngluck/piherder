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
from datetime import datetime, timedelta
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


# Home-lab ranges with a DoS ceiling (v1.3 slice 2). Floors match 1.2 helpers.
IDLE_SEC_MIN, IDLE_SEC_MAX, IDLE_SEC_DEFAULT = 60, 28800, 900
MAX_SEC_MIN, MAX_SEC_MAX, MAX_SEC_DEFAULT = 120, 43200, 3600
PER_USER_MIN, PER_USER_MAX, PER_USER_DEFAULT = 1, 16, 4
GLOBAL_MIN, GLOBAL_MAX, GLOBAL_DEFAULT = 1, 64, 20
TICKET_SEC_MIN, TICKET_SEC_MAX, TICKET_SEC_DEFAULT = 15, 300, 60
HOLD_SEC_MIN_POS, HOLD_SEC_MAX, HOLD_SEC_DEFAULT = 30, 3600, 0
REVALIDATE_SEC_MIN, REVALIDATE_SEC_MAX, REVALIDATE_SEC_DEFAULT = 5, 60, 10
SCROLLBACK_MIN, SCROLLBACK_MAX, SCROLLBACK_DEFAULT = 500, 50000, 2000

# AppSetting key → env name (env wins when set and non-empty).
CONSOLE_ENV_KEYS: Tuple[Tuple[str, str], ...] = (
    ("console_idle_sec", "PIHERDER_SSH_CONSOLE_IDLE_SEC"),
    ("console_max_sec", "PIHERDER_SSH_CONSOLE_MAX_SEC"),
    ("console_max_per_user", "PIHERDER_SSH_CONSOLE_MAX_PER_USER"),
    ("console_max_global", "PIHERDER_SSH_CONSOLE_MAX_GLOBAL"),
    ("console_ticket_sec", "PIHERDER_SSH_CONSOLE_TICKET_SEC"),
    ("console_hold_sec", "PIHERDER_SSH_CONSOLE_HOLD_SEC"),
    ("console_revalidate_sec", "PIHERDER_SSH_CONSOLE_REVALIDATE_SEC"),
    ("console_scrollback", "PIHERDER_SSH_CONSOLE_SCROLLBACK"),
    ("console_bind_ip", "PIHERDER_SSH_CONSOLE_BIND_IP"),
    ("console_bind_device", "PIHERDER_SSH_CONSOLE_BIND_DEVICE"),
    ("console_privileged_role", "PIHERDER_SSH_CONSOLE_PRIVILEGED_ROLE"),
    ("console_audit_mode", "PIHERDER_SSH_CONSOLE_AUDIT_MODE"),
    ("console_audit_retention_days", "PIHERDER_SSH_CONSOLE_AUDIT_RETENTION_DAYS"),
    ("console_audit_required", "PIHERDER_SSH_CONSOLE_AUDIT_REQUIRED"),
)


def _settings_cfg() -> dict:
    try:
        from .app_settings import load_settings

        return load_settings()
    except Exception:
        return {}


def _as_int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    return max(lo, min(hi, n))


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in ("1", "true", "on", "yes")


def env_wins(env_name: str) -> bool:
    from .account_stepup import env_wins as _ew

    return _ew(env_name)


def _int_knob(
    env_name: str,
    setting_key: str,
    pydantic_attr: str,
    default: int,
    lo: int,
    hi: int,
) -> int:
    if env_wins(env_name):
        return _as_int(getattr(settings, pydantic_attr, default), default, lo, hi)
    cfg = _settings_cfg()
    if setting_key in cfg and cfg.get(setting_key) not in (None, ""):
        return _as_int(cfg.get(setting_key), default, lo, hi)
    return _as_int(getattr(settings, pydantic_attr, default), default, lo, hi)


def _bool_knob(
    env_name: str,
    setting_key: str,
    pydantic_attr: str,
    default: bool,
) -> bool:
    if env_wins(env_name):
        return bool(getattr(settings, pydantic_attr, default))
    cfg = _settings_cfg()
    if setting_key in cfg and cfg.get(setting_key) not in (None, ""):
        return _as_bool(cfg.get(setting_key), default)
    return bool(getattr(settings, pydantic_attr, default))


PRIVILEGED_ROLE_DEFAULT = "admin"
PRIVILEGED_ROLES = ("admin", "operator")
CONSOLE_STEPUP_COOKIE = "console_stepup"
STEPUP_SEC = 90
AUDIT_MODE_DEFAULT = "off"
AUDIT_MODES = ("off", "commands", "commands_output")
AUDIT_RETENTION_MIN, AUDIT_RETENTION_MAX, AUDIT_RETENTION_DEFAULT = 1, 90, 14


def _clamp_privileged_role(raw: Any) -> str:
    s = str(raw or "").strip().lower()
    if s in ("operators", "operator+", "op"):
        s = "operator"
    if s in ("admins", "administrator"):
        s = "admin"
    return s if s in PRIVILEGED_ROLES else PRIVILEGED_ROLE_DEFAULT


def _clamp_audit_mode(raw: Any) -> str:
    from .console_audit import clamp_mode

    return clamp_mode(raw)


def _str_knob(
    env_name: str,
    setting_key: str,
    pydantic_attr: str,
    default: str,
    allowed: tuple,
) -> str:
    raw: Any = default
    if env_wins(env_name):
        raw = getattr(settings, pydantic_attr, default)
    else:
        cfg = _settings_cfg()
        if setting_key in cfg and cfg.get(setting_key) not in (None, ""):
            raw = cfg.get(setting_key)
        else:
            raw = getattr(settings, pydantic_attr, default)
    s = str(raw or default).strip().lower()
    return s if s in allowed else default


def _clamp_hold(raw: Any) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = HOLD_SEC_DEFAULT
    if n <= 0:
        return 0
    return max(HOLD_SEC_MIN_POS, min(HOLD_SEC_MAX, n))


def clamp_console_policy(raw: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Clamp a Settings payload (or defaults) to home-lab floors/ceilings."""
    src = raw or {}
    idle = _as_int(src.get("console_idle_sec"), IDLE_SEC_DEFAULT, IDLE_SEC_MIN, IDLE_SEC_MAX)
    maxs = _as_int(src.get("console_max_sec"), MAX_SEC_DEFAULT, MAX_SEC_MIN, MAX_SEC_MAX)
    if maxs < idle:
        maxs = idle
    per = _as_int(
        src.get("console_max_per_user"), PER_USER_DEFAULT, PER_USER_MIN, PER_USER_MAX
    )
    glob = _as_int(
        src.get("console_max_global"), GLOBAL_DEFAULT, GLOBAL_MIN, GLOBAL_MAX
    )
    if glob < per:
        glob = per
    return {
        "console_idle_sec": idle,
        "console_max_sec": maxs,
        "console_max_per_user": per,
        "console_max_global": glob,
        "console_ticket_sec": _as_int(
            src.get("console_ticket_sec"), TICKET_SEC_DEFAULT, TICKET_SEC_MIN, TICKET_SEC_MAX
        ),
        "console_hold_sec": _clamp_hold(src.get("console_hold_sec")),
        "console_revalidate_sec": _as_int(
            src.get("console_revalidate_sec"),
            REVALIDATE_SEC_DEFAULT,
            REVALIDATE_SEC_MIN,
            REVALIDATE_SEC_MAX,
        ),
        "console_scrollback": _as_int(
            src.get("console_scrollback"), SCROLLBACK_DEFAULT, SCROLLBACK_MIN, SCROLLBACK_MAX
        ),
        "console_bind_ip": _as_bool(src.get("console_bind_ip"), True),
        "console_bind_device": _as_bool(src.get("console_bind_device"), True),
        "console_privileged_role": _clamp_privileged_role(src.get("console_privileged_role")),
        "console_audit_mode": _clamp_audit_mode(src.get("console_audit_mode")),
        "console_audit_retention_days": _as_int(
            src.get("console_audit_retention_days"),
            AUDIT_RETENTION_DEFAULT,
            AUDIT_RETENTION_MIN,
            AUDIT_RETENTION_MAX,
        ),
        "console_audit_required": _as_bool(src.get("console_audit_required"), False),
    }


def console_env_locks() -> Dict[str, bool]:
    return {key: env_wins(env) for key, env in CONSOLE_ENV_KEYS}


def effective_console_policy() -> Dict[str, Any]:
    """Resolved knobs (env / Settings / defaults) plus enable flag."""
    return {
        "console_idle_sec": idle_sec(),
        "console_max_sec": max_session_sec(),
        "console_max_per_user": max_per_user(),
        "console_max_global": max_global(),
        "console_ticket_sec": ticket_ttl_sec(),
        "console_hold_sec": hold_sec(),
        "console_revalidate_sec": revalidate_sec(),
        "console_scrollback": default_scrollback(),
        "console_bind_ip": bind_ip_enabled(),
        "console_bind_device": bind_device_enabled(),
        "console_privileged_role": privileged_role(),
        "console_audit_mode": audit_mode_setting(),
        "console_audit_retention_days": audit_retention_days(),
        "console_audit_required": audit_required(),
        "enabled": console_enabled(),
        "grant_minutes": grant_minutes(),
    }


def console_policy_summary(data: Dict[str, Any] | None = None) -> str:
    p = clamp_console_policy(data) if data is not None else clamp_console_policy(
        effective_console_policy()
    )
    return (
        f"idle={p['console_idle_sec']} max={p['console_max_sec']} "
        f"user={p['console_max_per_user']} global={p['console_max_global']} "
        f"ticket={p['console_ticket_sec']} hold={p['console_hold_sec']} "
        f"reval={p['console_revalidate_sec']} scroll={p['console_scrollback']} "
        f"bind_ip={int(bool(p['console_bind_ip']))} "
        f"bind_dev={int(bool(p['console_bind_device']))} "
        f"priv={p.get('console_privileged_role') or PRIVILEGED_ROLE_DEFAULT} "
        f"audit={p.get('console_audit_mode') or AUDIT_MODE_DEFAULT} "
        f"audit_req={int(bool(p.get('console_audit_required')))} "
        f"audit_keep={p.get('console_audit_retention_days') or AUDIT_RETENTION_DEFAULT}"
    )


def ticket_ttl_sec() -> int:
    return _int_knob(
        "PIHERDER_SSH_CONSOLE_TICKET_SEC",
        "console_ticket_sec",
        "PIHERDER_SSH_CONSOLE_TICKET_SEC",
        TICKET_SEC_DEFAULT,
        TICKET_SEC_MIN,
        TICKET_SEC_MAX,
    )


def idle_sec() -> int:
    return _int_knob(
        "PIHERDER_SSH_CONSOLE_IDLE_SEC",
        "console_idle_sec",
        "PIHERDER_SSH_CONSOLE_IDLE_SEC",
        IDLE_SEC_DEFAULT,
        IDLE_SEC_MIN,
        IDLE_SEC_MAX,
    )


def max_session_sec() -> int:
    raw = _int_knob(
        "PIHERDER_SSH_CONSOLE_MAX_SEC",
        "console_max_sec",
        "PIHERDER_SSH_CONSOLE_MAX_SEC",
        MAX_SEC_DEFAULT,
        MAX_SEC_MIN,
        MAX_SEC_MAX,
    )
    return max(raw, idle_sec())


def max_per_user() -> int:
    return _int_knob(
        "PIHERDER_SSH_CONSOLE_MAX_PER_USER",
        "console_max_per_user",
        "PIHERDER_SSH_CONSOLE_MAX_PER_USER",
        PER_USER_DEFAULT,
        PER_USER_MIN,
        PER_USER_MAX,
    )


def max_global() -> int:
    raw = _int_knob(
        "PIHERDER_SSH_CONSOLE_MAX_GLOBAL",
        "console_max_global",
        "PIHERDER_SSH_CONSOLE_MAX_GLOBAL",
        GLOBAL_DEFAULT,
        GLOBAL_MIN,
        GLOBAL_MAX,
    )
    return max(raw, max_per_user())


def default_scrollback() -> int:
    """Default xterm scrollback lines (client can raise within UI caps)."""
    return _int_knob(
        "PIHERDER_SSH_CONSOLE_SCROLLBACK",
        "console_scrollback",
        "PIHERDER_SSH_CONSOLE_SCROLLBACK",
        SCROLLBACK_DEFAULT,
        SCROLLBACK_MIN,
        SCROLLBACK_MAX,
    )


def grant_minutes() -> int:
    """How long a post-2FA console grant lasts (additional shells without re-TOTP)."""
    if env_wins("PIHERDER_SSH_CONSOLE_GRANT_MIN"):
        return max(2, int(getattr(settings, "PIHERDER_SSH_CONSOLE_GRANT_MIN", 10) or 10))
    try:
        from .account_stepup import stepup_minutes

        return max(2, stepup_minutes("console"))
    except Exception:
        return max(2, int(getattr(settings, "PIHERDER_SSH_CONSOLE_GRANT_MIN", 10) or 10))


def require_2fa_every_shell() -> bool:
    """If true, never skip 2FA via grant cookie (each New shell re-prompts)."""
    try:
        from .account_stepup import console_require_2fa_every_shell

        return console_require_2fa_every_shell()
    except Exception:
        return bool(getattr(settings, "PIHERDER_SSH_CONSOLE_REQUIRE_2FA_EVERY_SHELL", False))


def allow_backup_codes() -> bool:
    """Backup codes are weak for shell step-up (paper/stolen codes). Default off."""
    try:
        from .account_stepup import console_allow_backup_codes

        return console_allow_backup_codes()
    except Exception:
        return bool(getattr(settings, "PIHERDER_SSH_CONSOLE_ALLOW_BACKUP_CODES", False))


def prefer_passkey() -> bool:
    """UI + messaging prefer WebAuthn when the user has a passkey enrolled."""
    try:
        from .account_stepup import console_prefer_passkey

        return console_prefer_passkey()
    except Exception:
        return bool(getattr(settings, "PIHERDER_SSH_CONSOLE_PREFER_PASSKEY", True))


def require_passkey_if_enrolled() -> bool:
    """If true and user has passkeys, reject TOTP for console (passkey only)."""
    try:
        from .account_stepup import console_require_passkey

        return console_require_passkey()
    except Exception:
        return bool(getattr(settings, "PIHERDER_SSH_CONSOLE_REQUIRE_PASSKEY", False))


def bind_ip_enabled() -> bool:
    return _bool_knob(
        "PIHERDER_SSH_CONSOLE_BIND_IP",
        "console_bind_ip",
        "PIHERDER_SSH_CONSOLE_BIND_IP",
        True,
    )


def bind_device_enabled() -> bool:
    return _bool_knob(
        "PIHERDER_SSH_CONSOLE_BIND_DEVICE",
        "console_bind_device",
        "PIHERDER_SSH_CONSOLE_BIND_DEVICE",
        True,
    )


def privileged_role() -> str:
    """admin (default) or operator — who may mint a privileged console ticket."""
    return _str_knob(
        "PIHERDER_SSH_CONSOLE_PRIVILEGED_ROLE",
        "console_privileged_role",
        "PIHERDER_SSH_CONSOLE_PRIVILEGED_ROLE",
        PRIVILEGED_ROLE_DEFAULT,
        PRIVILEGED_ROLES,
    )


def can_open_privileged(user) -> bool:
    """RBAC for break-glass console. Demo never."""
    if is_demo_console():
        return False
    from ..security.auth import ROLE_ADMIN, ROLE_OPERATOR, role_at_least, user_role

    need = privileged_role()
    if need == "operator":
        return role_at_least(user, ROLE_OPERATOR)
    return (user_role(user) or "") == ROLE_ADMIN


def audit_mode_setting() -> str:
    """Stored / env mode before the required-on-all-sessions clamp."""
    return _str_knob(
        "PIHERDER_SSH_CONSOLE_AUDIT_MODE",
        "console_audit_mode",
        "PIHERDER_SSH_CONSOLE_AUDIT_MODE",
        AUDIT_MODE_DEFAULT,
        AUDIT_MODES,
    )


def audit_required() -> bool:
    """When true, every live shell records commands (Off is treated as commands)."""
    return _bool_knob(
        "PIHERDER_SSH_CONSOLE_AUDIT_REQUIRED",
        "console_audit_required",
        "PIHERDER_SSH_CONSOLE_AUDIT_REQUIRED",
        False,
    )


def audit_mode() -> str:
    """Effective capture mode. Required + off → commands. Demo callers still skip persist."""
    mode = audit_mode_setting()
    if mode == AUDIT_MODE_DEFAULT and audit_required():
        return "commands"
    return mode


def audit_retention_days() -> int:
    return _int_knob(
        "PIHERDER_SSH_CONSOLE_AUDIT_RETENTION_DAYS",
        "console_audit_retention_days",
        "PIHERDER_SSH_CONSOLE_AUDIT_RETENTION_DAYS",
        AUDIT_RETENTION_DEFAULT,
        AUDIT_RETENTION_MIN,
        AUDIT_RETENTION_MAX,
    )


def revalidate_sec() -> int:
    """How often to re-check session/IP/device during an open shell (seconds)."""
    return _int_knob(
        "PIHERDER_SSH_CONSOLE_REVALIDATE_SEC",
        "console_revalidate_sec",
        "PIHERDER_SSH_CONSOLE_REVALIDATE_SEC",
        REVALIDATE_SEC_DEFAULT,
        REVALIDATE_SEC_MIN,
        REVALIDATE_SEC_MAX,
    )


def hold_sec() -> int:
    """Max time to keep a detached PTY after WebSocket drop.

    0 (default) = hold until idle_sec from last activity or max_session_sec from start.
    Positive value caps the detached window (still also subject to idle/max).
    """
    if env_wins("PIHERDER_SSH_CONSOLE_HOLD_SEC"):
        return _clamp_hold(getattr(settings, "PIHERDER_SSH_CONSOLE_HOLD_SEC", 0) or 0)
    cfg = _settings_cfg()
    if "console_hold_sec" in cfg and cfg.get("console_hold_sec") not in (None, ""):
        return _clamp_hold(cfg.get("console_hold_sec"))
    return _clamp_hold(getattr(settings, "PIHERDER_SSH_CONSOLE_HOLD_SEC", 0) or 0)


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
    identity_id: Optional[int] = None,
    identity_role: Optional[str] = None,
    reason: Optional[str] = None,
) -> str:
    """
    Short-lived single-use ticket.

    Bound to session_version, and optionally IP + console device id so the
    WebSocket cannot be opened (or continued) from another browser/network.
    Optional ``identity_id`` / ``identity_role`` select fleet vs privileged.
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
    if identity_id:
        payload["iid"] = int(identity_id)
    role = (identity_role or "").strip().lower()
    if role in ("fleet", "privileged"):
        payload["role"] = role
    why = (reason or "").strip()[:200]
    if why:
        payload["why"] = why
    if bind_ip_enabled() and client_ip:
        payload["iph"] = _hash_binding(normalize_ip(client_ip))
    if bind_device_enabled() and device_id:
        payload["did"] = _hash_binding(device_id)
    return create_access_token(
        payload,
        expires_delta=timedelta(seconds=ticket_ttl_sec()),
    )


def mint_stepup_proof(
    *,
    user_id: int,
    session_version: int = 0,
    client_ip: Optional[str] = None,
    device_id: Optional[str] = None,
) -> str:
    """Short-lived proof that 2FA just succeeded (privileged mint, ~90s)."""
    require_enabled()
    payload: Dict[str, Any] = {
        "console_stepup": True,
        "sub": str(int(user_id)),
        "sv": int(session_version),
        "jti": secrets.token_urlsafe(12),
    }
    if bind_ip_enabled() and client_ip:
        payload["iph"] = _hash_binding(normalize_ip(client_ip))
    if bind_device_enabled() and device_id:
        payload["did"] = _hash_binding(device_id)
    return create_access_token(
        payload,
        expires_delta=timedelta(seconds=STEPUP_SEC),
    )


def consume_stepup_proof(
    raw: Optional[str],
    *,
    user_id: int,
    session_version: int,
    client_ip: Optional[str] = None,
    device_id: Optional[str] = None,
) -> bool:
    """Single-use 2FA proof for privileged mint. Fleet grant is not enough."""
    if not raw:
        return False
    payload = decode_token_payload(raw)
    if not payload or not payload.get("console_stepup"):
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
    jti = str(payload.get("jti") or "")
    if not jti:
        return False
    return _consume_jti("stepup:" + jti)


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
    recorder: Any = None

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
    rec = getattr(held, "recorder", None)
    if rec is not None and not getattr(rec, "finalized", True):
        try:
            from ..database import engine
            from sqlmodel import Session as _Sess

            from . import console_audit as ca
            from .audit_write import make_audit_log

            with _Sess(engine) as s:
                ca.flush_recorder(s, rec, finalize=True)
                s.add(
                    make_audit_log(
                        user_id=held.user_id,
                        server_id=held.server_id,
                        action="ssh_console_close",
                        status="success",
                        details=ca.close_details(rec, "park_end"),
                        finished_at=datetime.utcnow(),
                    )
                )
                s.commit()
        except Exception:
            pass
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
            rec = getattr(held, "recorder", None)
            if rec is not None:
                try:
                    rec.feed_stdout(data)
                except Exception:
                    pass
            held.last_activity_mono = time.monotonic()
            progressed = True
        while ch.recv_stderr_ready():
            data = ch.recv_stderr(4096)
            if data:
                held.append_out(data)
                rec = getattr(held, "recorder", None)
                if rec is not None:
                    try:
                        rec.feed_stdout(data)
                    except Exception:
                        pass
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
