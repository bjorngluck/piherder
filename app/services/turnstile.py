"""Cloudflare Turnstile verification (login bot shield).

When ``PIHERDER_TURNSTILE_SITE_KEY`` and ``SECRET_KEY`` are both set, login
requires a valid ``cf-turnstile-response`` token. Empty keys = disabled
(local lab / offline).
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def turnstile_site_key() -> str:
    return (getattr(settings, "PIHERDER_TURNSTILE_SITE_KEY", None) or "").strip()


def turnstile_secret_key() -> str:
    return (getattr(settings, "PIHERDER_TURNSTILE_SECRET_KEY", None) or "").strip()


def turnstile_enabled() -> bool:
    return bool(turnstile_site_key() and turnstile_secret_key())


def verify_turnstile_token(
    token: Optional[str],
    *,
    remoteip: Optional[str] = None,
) -> tuple[bool, str]:
    """Return (ok, error_code). error_code is empty on success."""
    if not turnstile_enabled():
        return True, ""
    tok = (token or "").strip()
    if not tok:
        return False, "missing-input-response"
    data = {
        "secret": turnstile_secret_key(),
        "response": tok,
    }
    if remoteip:
        data["remoteip"] = remoteip
    try:
        with httpx.Client(timeout=8.0) as client:
            r = client.post(VERIFY_URL, data=data)
            r.raise_for_status()
            body = r.json()
    except Exception as e:
        logger.warning("Turnstile verify request failed: %s", e)
        return False, "verify-unreachable"
    if body.get("success"):
        return True, ""
    errs = body.get("error-codes") or ["invalid-input-response"]
    code = errs[0] if errs else "invalid-input-response"
    logger.info("Turnstile rejected: %s", code)
    return False, str(code)
