"""Cloudflare Turnstile verification (login bot shield).

When site + secret keys are both set, login requires a valid
``cf-turnstile-response`` token. Empty keys = disabled (local lab / offline).

Env (any pair works; first non-empty wins per field):

* Site: ``PIHERDER_TURNSTILE_SITE_KEY`` or ``TURNSTILE_SITE_KEY``
* Secret: ``PIHERDER_TURNSTILE_SECRET_KEY`` or ``TURNSTILE_SECRET``
  (Cloudflare Spin / dashboard recovery uses ``TURNSTILE_SECRET``)
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def _first_env(*names: str) -> str:
    for n in names:
        v = (os.environ.get(n) or "").strip()
        if v:
            return v
    return ""


def turnstile_site_key() -> str:
    return (
        (getattr(settings, "PIHERDER_TURNSTILE_SITE_KEY", None) or "").strip()
        or _first_env("TURNSTILE_SITE_KEY", "TURNSTILE_SITEKEY")
    )


def turnstile_secret_key() -> str:
    return (
        (getattr(settings, "PIHERDER_TURNSTILE_SECRET_KEY", None) or "").strip()
        or _first_env("TURNSTILE_SECRET", "TURNSTILE_SECRET_KEY")
    )


def turnstile_enabled() -> bool:
    return bool(turnstile_site_key() and turnstile_secret_key())


def verify_turnstile_token(
    token: Optional[str],
    *,
    remoteip: Optional[str] = None,
) -> tuple[bool, str]:
    """Return (ok, error_code). error_code is empty on success.

    ``remoteip`` is optional. Only send a *visitor* IP (e.g. CF-Connecting-IP).
    Do not send the Cloudflare edge address — siteverify may reject the token.
    """
    if not turnstile_enabled():
        return True, ""
    tok = (token or "").strip()
    if not tok:
        return False, "missing-input-response"
    data = {
        "secret": turnstile_secret_key(),
        "response": tok,
    }
    # Optional; wrong IP is worse than omitting (invalid-input-response)
    rip = (remoteip or "").strip()
    if rip and rip.lower() not in ("unknown", "127.0.0.1", "::1"):
        data["remoteip"] = rip
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
    logger.info(
        "Turnstile rejected: %s (all=%s remoteip=%s token_len=%s)",
        code,
        errs,
        data.get("remoteip") or "-",
        len(tok),
    )
    return False, str(code)
