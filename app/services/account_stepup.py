"""Account / login / secrets / console step-up policy (v1.3 slice 1 Deep).

Single helper for mutation step-up (T6) plus Settings-backed scope, windows,
factor matrix, and optional IdP-MFA login skip (T1–T4). Defaults match v1.2.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any, Mapping, Optional, Tuple

from sqlmodel import Session

from ..models import User

FORCE_SCOPES = ("off", "admins", "operators", "all")
SURFACES = ("login", "account", "secrets", "console")
FACTORS = ("totp", "passkey", "backup")

# Home-lab grace (signed 2026-08-18)
GRACE_DAYS_MIN = 0
GRACE_DAYS_MAX = 60

WINDOW_MIN = 1
WINDOW_MAX = 120

_DEFAULT_FACTORS = {
    "login": {"totp": True, "passkey": True, "backup": True},
    "account": {"totp": True, "passkey": True, "backup": True},
    "secrets": {"totp": True, "passkey": True, "backup": False},
    "console": {"totp": True, "passkey": True, "backup": False},
}

# Explicit AMR tokens that count as IdP MFA (fail closed otherwise).
_IDP_MFA_AMR = frozenset(
    {"mfa", "otp", "totp", "hotp", "hwk", "sms", "swk", "face", "fpt"}
)


def _cfg() -> dict:
    from .app_settings import load_settings

    try:
        return load_settings()
    except Exception:
        return {}


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in ("1", "true", "on", "yes")


def _as_int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    return max(lo, min(hi, n))


def force_2fa_scope(cfg: Mapping[str, Any] | None = None) -> str:
    """off | admins | operators | all. Legacy force_2fa=true → all."""
    src = cfg if cfg is not None else _cfg()
    raw = str(src.get("force_2fa_scope") or "").strip().lower()
    if raw in FORCE_SCOPES and raw != "off":
        return raw
    if raw == "off" and not _as_bool(src.get("force_2fa"), False):
        return "off"
    if _as_bool(src.get("force_2fa"), False):
        return "all"
    return "off" if raw not in FORCE_SCOPES else raw


def force_2fa_required() -> bool:
    """True when any force-2FA scope is on (not user-specific)."""
    return force_2fa_scope() != "off"


def _scope_matches(user: User, scope: str) -> bool:
    from ..security.auth import ROLE_ADMIN, ROLE_OPERATOR, user_role

    role = user_role(user)
    if scope == "all":
        return True
    if scope == "admins":
        return role == ROLE_ADMIN
    if scope == "operators":
        return role in (ROLE_ADMIN, ROLE_OPERATOR)
    return False


def _in_grace(cfg: Mapping[str, Any]) -> bool:
    days = _as_int(cfg.get("force_2fa_grace_days"), 0, GRACE_DAYS_MIN, GRACE_DAYS_MAX)
    if days <= 0:
        return False
    raw = str(cfg.get("force_2fa_grace_since") or "").strip()
    if not raw:
        return False
    try:
        since = datetime.fromisoformat(raw.replace("Z", ""))
    except ValueError:
        return False
    return datetime.utcnow() < since + timedelta(days=days)


def force_2fa_applies(
    user: User,
    *,
    request=None,
    session: Optional[Session] = None,
) -> bool:
    """True when this user must enrol 2FA before using the fleet UI."""
    cfg = _cfg()
    scope = force_2fa_scope(cfg)
    if scope == "off":
        return False
    if not _scope_matches(user, scope):
        return False
    if _in_grace(cfg):
        return False
    if (
        request is not None
        and session is not None
        and _as_bool(cfg.get("force_2fa_trusted_skip_enroll"), False)
    ):
        from ..security.auth import find_valid_trusted_device, read_trusted_device_token

        raw = read_trusted_device_token(request.cookies, int(user.id))
        if raw and find_valid_trusted_device(session, int(user.id), raw):
            return False
    return True


def login_trusted_skip_2fa() -> bool:
    return _as_bool(_cfg().get("login_trusted_skip_2fa"), True)


def stepup_minutes(surface: str) -> int:
    cfg = _cfg()
    defaults = {"account": 5, "secrets": 10, "console": 10}
    key = {
        "account": "stepup_account_minutes",
        "secrets": "stepup_secrets_minutes",
        "console": "stepup_console_minutes",
    }.get(surface, "stepup_account_minutes")
    return _as_int(cfg.get(key), defaults.get(surface, 5), WINDOW_MIN, WINDOW_MAX)


def factor_allowed(surface: str, factor: str) -> bool:
    if surface not in SURFACES or factor not in FACTORS:
        return False
    default = _DEFAULT_FACTORS.get(surface, {}).get(factor, False)
    key = f"factor_{surface}_{factor}"
    return _as_bool(_cfg().get(key), default)


def env_wins(env_name: str) -> bool:
    return env_name in os.environ


def console_require_2fa_every_shell() -> bool:
    from ..config import settings

    if env_wins("PIHERDER_SSH_CONSOLE_REQUIRE_2FA_EVERY_SHELL"):
        return bool(getattr(settings, "PIHERDER_SSH_CONSOLE_REQUIRE_2FA_EVERY_SHELL", False))
    return _as_bool(_cfg().get("console_require_2fa_every_shell"), False)


def console_allow_backup_codes() -> bool:
    from ..config import settings

    if env_wins("PIHERDER_SSH_CONSOLE_ALLOW_BACKUP_CODES"):
        return bool(getattr(settings, "PIHERDER_SSH_CONSOLE_ALLOW_BACKUP_CODES", False))
    if not factor_allowed("console", "backup"):
        return False
    return _as_bool(_cfg().get("console_allow_backup_codes"), False)


def console_prefer_passkey() -> bool:
    from ..config import settings

    if env_wins("PIHERDER_SSH_CONSOLE_PREFER_PASSKEY"):
        return bool(getattr(settings, "PIHERDER_SSH_CONSOLE_PREFER_PASSKEY", True))
    return _as_bool(_cfg().get("console_prefer_passkey"), True)


def console_require_passkey() -> bool:
    from ..config import settings

    if env_wins("PIHERDER_SSH_CONSOLE_REQUIRE_PASSKEY"):
        return bool(getattr(settings, "PIHERDER_SSH_CONSOLE_REQUIRE_PASSKEY", False))
    return _as_bool(_cfg().get("console_require_passkey"), False)


def idp_mfa_satisfies_login(claims: Optional[Mapping[str, Any]]) -> bool:
    """T4: opt-in, fail closed. Never skip if claim missing/unknown."""
    cfg = _cfg()
    if not _as_bool(cfg.get("oidc_idp_mfa_satisfies_login_2fa"), False):
        return False
    if not claims:
        return False
    path = str(cfg.get("oidc_idp_mfa_claim") or "amr").strip() or "amr"
    raw = claims.get(path)
    if raw is None and "." in path:
        from .oidc_svc import _claim_path_get

        raw = _claim_path_get(dict(claims), path)
    if raw is None:
        return False
    tokens = raw if isinstance(raw, (list, tuple)) else [raw]
    norm = [str(t).strip().lower() for t in tokens if t is not None and str(t).strip()]
    if not norm:
        return False
    if path.split(".")[-1].lower() == "acr":
        # Only a few well-known MFA ACR values — not any non-empty string.
        return any(
            t in {"mfa", "https://refeds.org/profile/mfa"} or t.endswith("/mfa")
            for t in norm
        )
    return any(t in _IDP_MFA_AMR for t in norm)


def verify_stepup(
    session: Session,
    user: User,
    *,
    password: Optional[str] = None,
    totp_code: Optional[str] = None,
    request=None,
    surface: str = "account",
) -> Tuple[bool, str]:
    """T6 + T3: any *allowed* enrolled factor; password only when no 2FA.

    Returns (ok, error_code).
    """
    from . import webauthn_svc as wa_svc
    from .oidc_svc import password_login_allowed
    from ..security.auth import (
        account_stepup_active,
        consume_backup_code,
        decrypt_totp_secret,
        verify_password,
        verify_totp_code,
    )

    surf = surface if surface in SURFACES else "account"
    has_2fa = wa_svc.user_has_2fa(session, user)
    if has_2fa:
        if (
            factor_allowed(surf, "passkey")
            and request is not None
            and account_stepup_active(request, user)
        ):
            return True, ""
        code = (totp_code or "").strip().replace(" ", "")
        has_pk = wa_svc.has_passkeys(session, int(user.id))
        has_totp = wa_svc.totp_active(user)
        if not code:
            if factor_allowed(surf, "passkey") and has_pk and not (
                factor_allowed(surf, "totp") and has_totp
            ):
                return False, "use_passkey"
            if factor_allowed(surf, "totp") or factor_allowed(surf, "backup"):
                return False, "2fa_required"
            if factor_allowed(surf, "passkey") and has_pk:
                return False, "use_passkey"
            return False, "2fa_required"
        if factor_allowed(surf, "totp") and has_totp and user.totp_secret_encrypted:
            try:
                secret = decrypt_totp_secret(user.totp_secret_encrypted)
                if verify_totp_code(secret, code):
                    return True, ""
            except Exception:
                pass
        if factor_allowed(surf, "backup") and consume_backup_code(
            session, int(user.id), code
        ):
            return True, ""
        return False, "2fa_bad_code"

    if password_login_allowed(user):
        if not password or not verify_password(password, user.hashed_password):
            return False, "password_required"
        return True, ""
    return True, ""


def policy_audit_summary(cfg: Mapping[str, Any] | None = None) -> str:
    src = cfg if cfg is not None else _cfg()
    scope = force_2fa_scope(src)
    grace = _as_int(src.get("force_2fa_grace_days"), 0, GRACE_DAYS_MIN, GRACE_DAYS_MAX)
    return (
        f"scope={scope} grace={grace}d "
        f"acct={_as_int(src.get('stepup_account_minutes'), 5, WINDOW_MIN, WINDOW_MAX)}m "
        f"idp_mfa={_as_bool(src.get('oidc_idp_mfa_satisfies_login_2fa'), False)}"
    )
