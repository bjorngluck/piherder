"""HTTP security headers including Content-Security-Policy (v1.2).

CSP is enabled by default. Existing UI uses many inline scripts/styles and
vendored Tailwind Play (needs ``unsafe-eval``). Policy is intentionally
**self-hosted** (no third-party script CDNs) so webshell/xterm stay under
``'self'``. Tighten further (nonces) in a later train when inline scripts shrink.

Env:
  PIHERDER_CSP=true|false          (default true)
  PIHERDER_CSP_REPORT_ONLY=true    send Content-Security-Policy-Report-Only instead
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from ..config import settings


def csp_enabled() -> bool:
    return bool(getattr(settings, "PIHERDER_CSP", True))


def csp_report_only() -> bool:
    return bool(getattr(settings, "PIHERDER_CSP_REPORT_ONLY", False))


def _public_origin() -> Optional[str]:
    raw = (getattr(settings, "PIHERDER_PUBLIC_URL", None) or "").strip()
    if not raw:
        return None
    try:
        p = urlparse(raw)
        if p.scheme and p.netloc:
            return f"{p.scheme}://{p.netloc}"
    except Exception:
        return None
    return None


def build_csp() -> str:
    """Return the Content-Security-Policy value (no header name)."""
    # script-src: 'unsafe-inline' for template scripts; 'unsafe-eval' for Tailwind Play
    # style-src: 'unsafe-inline' for theme/style attributes and xterm
    # connect-src: same origin + WebSocket (console) + optional public origin
    connect = ["'self'", "ws:", "wss:"]
    origin = _public_origin()
    if origin:
        connect.append(origin)
        if origin.startswith("https://"):
            connect.append("wss://" + origin[len("https://") :])
        elif origin.startswith("http://"):
            connect.append("ws://" + origin[len("http://") :])

    # de-dupe preserve order
    seen = set()
    connect_parts = []
    for c in connect:
        if c not in seen:
            seen.add(c)
            connect_parts.append(c)

    # frame-ancestors / frame-src 'self': allow same-origin console modal iframe
    # (third-party embedding still blocked). Previously 'none' which forced
    # window.open popups that browsers often block.
    directives = [
        "default-src 'self'",
        "base-uri 'self'",
        "object-src 'none'",
        "frame-ancestors 'self'",
        "form-action 'self'",
        "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "font-src 'self' data:",
        "connect-src " + " ".join(connect_parts),
        "worker-src 'self'",
        "manifest-src 'self'",
        "media-src 'self'",
        "frame-src 'self'",
    ]
    # Only upgrade on HTTPS public URL (avoid breaking plain http labs)
    if origin and origin.startswith("https://"):
        directives.append("upgrade-insecure-requests")

    return "; ".join(directives) + ";"


def security_headers_dict() -> dict[str, str]:
    """All security headers applied to HTML/app responses."""
    headers = {
        "X-Content-Type-Options": "nosniff",
        # SAMEORIGIN: same-origin console modal iframe; blocks third-party framing
        "X-Frame-Options": "SAMEORIGIN",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        # Camera/mic unused; geolocation off; payment off.
        # publickey-credentials-get=(self) so passkeys work in same-origin console iframe.
        "Permissions-Policy": (
            "camera=(), microphone=(), geolocation=(), payment=(), "
            "publickey-credentials-get=(self)"
        ),
        "Cross-Origin-Opener-Policy": "same-origin",
    }
    if csp_enabled():
        name = (
            "Content-Security-Policy-Report-Only"
            if csp_report_only()
            else "Content-Security-Policy"
        )
        headers[name] = build_csp()
    return headers


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach CSP + baseline security headers to every response."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        path = request.url.path or ""
        # Skip CSP on pure static assets? Still useful; leave on.
        # Health/metrics keep headers (no harm).
        for k, v in security_headers_dict().items():
            # Do not overwrite if an endpoint set a more specific CSP
            if k not in response.headers:
                response.headers[k] = v
        # Avoid caching authenticated HTML with wrong CSP edge cases
        if path.startswith("/static"):
            pass
        return response
