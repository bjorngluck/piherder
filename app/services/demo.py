"""Public demo sandbox helpers (v1.2 Stream D).

When ``PIHERDER_DEMO_MODE=1``:
  • Persistent UI banner
  • Hard blocks: real host onboard, usable API tokens, outbound nmap/SSH/certs/mail
  • Job mutations become canned success (see jobs.service)
  • **Write guard**: most POST/PUT/PATCH/DELETE blocked (connectors, DNS, certs,
    templates, settings, shared-account sabotage) so one visitor cannot trash the
    sandbox for everyone. Safe allowlist: login, canned job runs, notifications,
    favourites.

Never enable on a production herder that holds real keys.
"""
from __future__ import annotations

import re
from typing import Optional

from fastapi import HTTPException
from fastapi.responses import RedirectResponse

from ..config import settings

# Fixed copy — matches PLAN_v1.2.0 §3.2 (shared login is viewer)
DEMO_BANNER = "Demo — shared viewer account · data resets · some actions simulated"

_ACTION_HINTS: dict[str, str] = {
    "register": (
        "Account creation is disabled in the public demo. "
        "Use the shared demo login only."
    ),
    "onboard": "Adding real hosts is disabled in the demo.",
    "wizard": "Adding real hosts is disabled in the demo.",
    "ssh_test": "Live SSH is disabled in the demo.",
    "ssh_deploy": "SSH key deploy is disabled in the demo.",
    "api_token": "API tokens cannot be created in the demo.",
    "api_use": "API token authentication is disabled in the demo.",
    "nmap": "Live network scans are disabled in the demo.",
    "cert_deploy": "Certificate deploy is disabled in the demo.",
    "webhook": "Outbound webhooks are disabled in the demo.",
    "mail": "Outbound email is disabled in the demo.",
    "console": "Web SSH console is disabled in the demo.",
    "secrets_export": "Secret export is disabled in the demo.",
    "job": "Live host jobs are simulated in the demo.",
    # Shared-sandbox identity — one visitor must not lock out everyone else
    "shared_account": (
        "This is a shared demo account. Password, 2FA, SSO link, and similar "
        "account changes are disabled so one visitor cannot lock others out."
    ),
    "user_admin": "User administration is disabled in the public demo.",
    "seed_restore": (
        "Demo seed restore is operator-only (CLI on the VPS). "
        "It is not available in the UI."
    ),
    "settings_security": (
        "Security policy changes (force 2FA, etc.) are disabled in the public demo."
    ),
    "password_reset": (
        "Password reset is disabled in the public demo (shared account)."
    ),
    "sso": "SSO sign-in and linking are disabled in the public demo.",
    "settings_write": (
        "Changing platform settings (SSO, alerts, backups, cleanup) is disabled "
        "in the public demo."
    ),
    "herder_restore": "Instance restore/delete is disabled in the public demo.",
    "connector": (
        "Adding or changing connectors, DNS, certs, templates, and other fleet "
        "config is disabled in the public demo (shared sandbox)."
    ),
    "write": (
        "Changes are disabled in the public demo so one visitor cannot affect "
        "everyone else. No new accounts, connectors, or config — browse the "
        "seeded fleet; jobs are simulated."
    ),
}

# --- Global write guard (middleware) -------------------------------------------------

# Exact paths that may mutate state in demo mode
_DEMO_WRITE_EXACT = frozenset(
    {
        "/auth/login",
        "/auth/logout",
        "/auth/2fa",
    }
)

# Prefixes (path == p or path.startswith(p + "/"))
_DEMO_WRITE_PREFIXES = (
    "/auth/2fa/",  # login 2FA + webauthn (not /auth/account/2fa)
    "/notifications/",
    "/account/favourites",  # favourites toggle under /account/favourites
    "/api/push/",  # personal push subscribe (optional UX)
)

# Canned job runs — demo experience (no live SSH)
_RE_SERVER_RUN = re.compile(r"^/servers/\d+/run(?:/|$)")
_RE_JOB_CANCEL = re.compile(r"^/jobs/\d+/cancel$")


def demo_write_allowed(method: str, path: str) -> bool:
    """Return True if this request may mutate state when DEMO_MODE is on.

    Safe methods always allowed. When not in demo mode, always True.
    """
    m = (method or "GET").upper()
    if m in ("GET", "HEAD", "OPTIONS"):
        return True
    if not demo_mode():
        return True
    p = (path or "/").split("?", 1)[0]
    if len(p) > 1 and p.endswith("/"):
        p = p.rstrip("/")
    if not p.startswith("/"):
        p = "/" + p

    if p in _DEMO_WRITE_EXACT:
        return True
    for pref in _DEMO_WRITE_PREFIXES:
        if p == pref.rstrip("/") or p.startswith(pref):
            return True
    if _RE_SERVER_RUN.match(p):
        return True
    if _RE_JOB_CANCEL.match(p):
        return True
    return False


def demo_write_block_detail() -> str:
    return demo_message("write")


def demo_mode() -> bool:
    """True when this instance is a public demo sandbox."""
    return bool(getattr(settings, "PIHERDER_DEMO_MODE", False))


def demo_banner() -> str:
    return DEMO_BANNER


def demo_message(action: str = "") -> str:
    """User-facing reason for a blocked or simulated action."""
    key = (action or "").strip().lower()
    hint = _ACTION_HINTS.get(key)
    if hint:
        return hint
    return "This action is disabled in the demo."


def reject_if_demo(action: str = "") -> Optional[str]:
    """Return a user-facing block reason when demo mode is on, else None."""
    if demo_mode():
        return demo_message(action)
    return None


class DemoBlocked(Exception):
    """Raised from services when an action must not proceed in demo mode."""

    def __init__(self, action: str = ""):
        self.action = (action or "").strip().lower()
        self.message = demo_message(self.action)
        super().__init__(self.message)


def raise_if_demo(action: str = "") -> None:
    """Raise DemoBlocked when demo mode is on."""
    if demo_mode():
        raise DemoBlocked(action)


def http_403_if_demo(action: str = "") -> None:
    """Raise HTTP 403 when demo mode is on (for routers)."""
    msg = reject_if_demo(action)
    if msg:
        raise HTTPException(status_code=403, detail=msg)


def redirect_if_demo(dest: str, *, error: str = "demo_locked") -> Optional[RedirectResponse]:
    """HTML form posts: 303 away with ``?error=`` when demo mode is on."""
    if not demo_mode():
        return None
    base = (dest or "/").strip() or "/"
    sep = "&" if "?" in base else "?"
    return RedirectResponse(f"{base}{sep}error={error}", status_code=303)
