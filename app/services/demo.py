"""Public demo sandbox helpers (v1.2 Stream D).

When ``PIHERDER_DEMO_MODE=1``:
  • Persistent UI banner
  • Hard blocks: real host onboard, usable API tokens, outbound nmap/SSH/certs/mail
  • Job mutations become canned success (see jobs.service)

Never enable on a production herder that holds real keys.
"""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException

from ..config import settings

# Fixed copy — matches PLAN_v1.2.0 §3.2
DEMO_BANNER = "Demo — shared account · data resets · some actions simulated"

_ACTION_HINTS: dict[str, str] = {
    "register": "Registration is disabled in the demo.",
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
}


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
