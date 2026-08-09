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


def visitor_ip_for_turnstile(request) -> Optional[str]:
    """Visitor IP safe to send as siteverify ``remoteip``.

    Only **CF-Connecting-IP** / **True-Client-IP**. Do **not** use X-Forwarded-For
    from our Caddyfile — it is rewritten to the **Cloudflare edge** hop, which
    causes siteverify ``invalid-input-response`` when orange-clouded.
    """
    if request is None:
        return None
    try:
        h = request.headers
    except Exception:
        return None
    for key in ("cf-connecting-ip", "true-client-ip"):
        try:
            val = (h.get(key) or "").strip()
        except Exception:
            val = ""
        if val:
            return val.split(",")[0].strip() or None
    return None


def verify_turnstile_token(
    token: Optional[str],
    *,
    remoteip: Optional[str] = None,
) -> tuple[bool, str]:
    """Return (ok, error_code). error_code is empty on success.

    ``remoteip`` is optional. Prefer :func:`visitor_ip_for_turnstile` only.
    Omitting remoteip is valid and safest when the visitor IP is unknown.
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
    # Optional — wrong IP (CF edge via Caddy XFF) breaks verification
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
    logger.warning(
        "Turnstile rejected: %s (all=%s remoteip=%s token_len=%s site_prefix=%s)",
        code,
        errs,
        data.get("remoteip") or "(omitted)",
        len(tok),
        (turnstile_site_key() or "")[:12],
    )
    return False, str(code)
