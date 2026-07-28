"""Risk-based input validation for production hardening (v1.0 AV).

Not a full Form→Pydantic rewrite. Helpers for **dangerous sinks**:
paths, hostnames, cron, action allowlists, string length caps.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

# Conservative defaults for homelab form fields
DEFAULT_MAX_STR = 500
MAX_HOSTNAME = 253
MAX_SSH_USER = 64
MAX_PATH = 512
MAX_CRON = 120
MAX_ACTION = 64
MAX_PEM_CHARS = 256_000  # ~256 KiB text PEM
MAX_COMPOSE_CHARS = 2_000_000  # 2 MiB compose workspace

_NULL = "\x00"
# Path segments that must never appear when product expects a relative/safe path
_PATH_TRAVERSAL = re.compile(r"(^|/|\\)\.\.($|/|\\)")
# SSH username: POSIX-ish portable set
_SSH_USER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")
# Hostname / FQDN / IPv4-ish — not full RFC; reject shell metacharacters
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)"
    r"(\.([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?))*\.?$"
)
_IPV4_RE = re.compile(
    r"^(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)$"
)
# Simple cron: 5 fields (minute hour dom mon dow) — product already has richer checks elsewhere
_CRON_TOKEN_RE = re.compile(r"^[\d\*\/,\-]+$")


class ValidationError(ValueError):
    """Operator-facing validation failure (message safe to show in UI)."""


def clamp_str(
    value: Optional[str],
    *,
    max_len: int = DEFAULT_MAX_STR,
    field: str = "value",
    allow_empty: bool = True,
) -> str:
    """Strip and enforce max length; reject NULs."""
    if value is None:
        s = ""
    else:
        s = str(value)
    if _NULL in s:
        raise ValidationError(f"{field} contains invalid characters")
    s = s.strip() if field != "password" else s  # keep password whitespace policy elsewhere
    if field != "password":
        s = s.strip()
    if not s and not allow_empty:
        raise ValidationError(f"{field} is required")
    if len(s) > max_len:
        raise ValidationError(f"{field} is too long (max {max_len} characters)")
    return s


def safe_path(
    value: Optional[str],
    *,
    field: str = "path",
    max_len: int = MAX_PATH,
    allow_tilde: bool = True,
    allow_absolute: bool = True,
    allow_empty: bool = False,
) -> str:
    """Normalize a host filesystem path for SSH/deploy forms.

    Rejects null bytes and ``..`` segments. Does **not** prove the path exists.
    """
    s = clamp_str(value, max_len=max_len, field=field, allow_empty=allow_empty)
    if not s:
        return s
    if "\\" in s:
        # Homelab product is Linux SSH — backslash paths are suspicious
        raise ValidationError(f"{field} must use forward slashes")
    if _PATH_TRAVERSAL.search(s) or s == ".." or s.startswith("../") or "/../" in s:
        raise ValidationError(f"{field} must not contain parent-directory segments (..)")
    if s.startswith("~"):
        if not allow_tilde:
            raise ValidationError(f"{field} must not start with ~")
        # ~/foo or ~ only
        if len(s) > 1 and not s.startswith("~/"):
            raise ValidationError(f"{field} home path must be ~/… or ~")
    elif s.startswith("/"):
        if not allow_absolute:
            raise ValidationError(f"{field} must be a relative path")
    # No shell metacharacters that break unquoted remote cmds in edge cases
    if any(c in s for c in ("\n", "\r", ";", "|", "&", "`", "$", "(", ")")):
        raise ValidationError(f"{field} contains invalid characters")
    return s


def safe_hostname(
    value: Optional[str],
    *,
    field: str = "hostname",
    max_len: int = MAX_HOSTNAME,
    allow_empty: bool = False,
) -> str:
    """SSH host: FQDN or IPv4 (no brackets/ports — port is separate)."""
    s = clamp_str(value, max_len=max_len, field=field, allow_empty=allow_empty)
    if not s:
        return s
    if s.startswith("[") or ":" in s:
        # IPv6 / host:port — product uses separate ssh_port
        raise ValidationError(f"{field} must be a hostname or IPv4 (port is separate)")
    if _IPV4_RE.match(s):
        return s
    if not _HOSTNAME_RE.match(s):
        raise ValidationError(f"{field} is not a valid hostname")
    return s.lower().rstrip(".")


def safe_ssh_user(
    value: Optional[str],
    *,
    field: str = "ssh_username",
    allow_empty: bool = False,
) -> str:
    s = clamp_str(value, max_len=MAX_SSH_USER, field=field, allow_empty=allow_empty)
    if not s:
        return s
    if not _SSH_USER_RE.match(s):
        raise ValidationError(
            f"{field} must be a simple Linux username "
            "(start with letter or _, then letters, digits, . _ -)"
        )
    return s


def safe_cron(
    value: Optional[str],
    *,
    field: str = "schedule",
    allow_empty: bool = True,
) -> Optional[str]:
    """Basic 5-field cron shape check. Empty → None when allow_empty."""
    s = clamp_str(value, max_len=MAX_CRON, field=field, allow_empty=True)
    if not s:
        if allow_empty:
            return None
        raise ValidationError(f"{field} is required")
    parts = s.split()
    if len(parts) != 5:
        raise ValidationError(f"{field} must be a 5-field cron expression")
    for p in parts:
        if not _CRON_TOKEN_RE.match(p):
            raise ValidationError(f"{field} has an invalid cron field: {p}")
    return s


def allowlist(
    value: Optional[str],
    allowed: Iterable[str],
    *,
    field: str = "action",
    default: Optional[str] = None,
) -> str:
    """Require value ∈ allowed (case-sensitive)."""
    allowed_set = frozenset(allowed)
    s = (value if value is not None else default) or ""
    s = str(s).strip()
    if s not in allowed_set:
        raise ValidationError(
            f"{field} must be one of: {', '.join(sorted(allowed_set))}"
        )
    return s


def clamp_text_blob(
    value: Optional[str],
    *,
    max_chars: int,
    field: str = "content",
    allow_empty: bool = True,
) -> str:
    """Size-cap for PEMs, compose files, large text areas."""
    if value is None:
        s = ""
    else:
        s = str(value)
    if _NULL in s:
        raise ValidationError(f"{field} contains invalid characters")
    if not s and not allow_empty:
        raise ValidationError(f"{field} is required")
    if len(s) > max_chars:
        raise ValidationError(
            f"{field} is too large (max {max_chars} characters)"
        )
    return s


# Common product allowlists (AV2)
# Must match docker_container_action allowlist in server_docker router
DOCKER_CONTAINER_ACTIONS = frozenset({"start", "stop", "restart"})
CERT_LAYOUTS = frozenset(
    {
        "pair",
        "combined",
        "pair_and_combined",
        "pair_and_pfx",
        "pair_combined_pfx",
    }
)
CERT_WRITE_MODES = frozenset({"direct", "stage_sudo"})
# Must match docker_management.prune_unused
PRUNE_TYPES = frozenset({"images", "containers", "both"})
