"""Cloudflare Turnstile verification (login bot shield).

When site + secret keys are both set, login requires a valid
``cf-turnstile-response`` token. Empty keys = disabled (local lab / offline).

Env (any pair works; first non-empty wins per field):

* Site: ``PIHERDER_TURNSTILE_SITE_KEY`` or ``TURNSTILE_SITE_KEY``
* Secret: ``PIHERDER_TURNSTILE_SECRET_KEY`` or ``TURNSTILE_SECRET``
  (Cloudflare Spin / dashboard recovery uses ``TURNSTILE_SECRET``)

**remoteip is always sent** to siteverify. Caddy must put the real visitor in
``CF-Connecting-IP`` / ``X-Forwarded-For`` (see root ``Caddyfile`` when orange-clouded).
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

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


def _normalize_ip(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    if s.startswith("["):
        end = s.find("]")
        if end > 0:
            return s[1:end]
    if s.count(":") == 1:
        host, port = s.rsplit(":", 1)
        if port.isdigit():
            return host
    return s


def visitor_ip_for_turnstile(request: Any) -> Optional[str]:
    """Resolve visitor IP for siteverify ``remoteip`` (always required when verifying).

    Order (must match Caddy when behind Cloudflare orange-cloud):

    1. ``CF-Connecting-IP`` / ``True-Client-IP`` (Cloudflare / some CDNs)
    2. ``X-Forwarded-For`` first hop (Caddy sets this to CF-Connecting-IP when present)
    3. ``X-Real-IP``
    4. TCP peer (``request.client.host``)
    """
    if request is None:
        return None
    try:
        h = {str(k).lower(): str(v) for k, v in dict(request.headers).items()}
    except Exception:
        h = {}
    for key in ("cf-connecting-ip", "true-client-ip"):
        val = (h.get(key) or "").strip()
        if val:
            return _normalize_ip(val.split(",")[0]) or None
    xff = (h.get("x-forwarded-for") or "").strip()
    if xff:
        return _normalize_ip(xff.split(",")[0]) or None
    xri = (h.get("x-real-ip") or "").strip()
    if xri:
        return _normalize_ip(xri) or None
    try:
        if getattr(request, "client", None) is not None and request.client.host:
            return _normalize_ip(request.client.host) or None
    except Exception:
        pass
    return None


def verify_turnstile_token(
    token: Optional[str],
    *,
    remoteip: Optional[str] = None,
) -> tuple[bool, str]:
    """Return (ok, error_code). error_code is empty on success.

    **Always** includes ``remoteip`` when verifying. Callers must pass the
    visitor IP from :func:`visitor_ip_for_turnstile` (not omit).
    """
    if not turnstile_enabled():
        return True, ""
    tok = (token or "").strip()
    if not tok:
        return False, "missing-input-response"
    rip = _normalize_ip(remoteip or "")
    if not rip or rip.lower() == "unknown":
        logger.warning("Turnstile: missing remoteip (token_len=%s)", len(tok))
        return False, "missing-remoteip"
    data = {
        "secret": turnstile_secret_key(),
        "response": tok,
        "remoteip": rip,
    }
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
        rip,
        len(tok),
        (turnstile_site_key() or "")[:12],
    )
    return False, str(code)
