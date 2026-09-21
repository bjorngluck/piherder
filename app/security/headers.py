"""HTTP security headers including Content-Security-Policy (v1.2, nonces v1.6).

CSP is enabled by default. Tailwind is a **compiled** stylesheet (no Play, no
``unsafe-eval``). Policy is **self-hosted** (no third-party script CDNs).

v1.6 Slice 1: each request gets a script nonce before render. ``script-src``
is ``'self' 'nonce-…'`` (no ``'unsafe-inline'``). ``script-src-attr`` stays
``'unsafe-inline'`` so ``onclick`` handlers keep working. ``style-src`` stays
``'unsafe-inline'``. ``/docs`` and ``/redoc`` keep today's ``'unsafe-inline'``.
Public demo sends the tightened policy as Report-Only until
``PIHERDER_CSP_ENFORCE=true``.

Env:
  PIHERDER_CSP=true|false          (default true)
  PIHERDER_CSP_REPORT_ONLY=true    send Content-Security-Policy-Report-Only instead
  PIHERDER_CSP_ENFORCE=true        demo enforces (otherwise demo is Report-Only)
"""
from __future__ import annotations

import re
import secrets
from contextvars import ContextVar
from typing import Optional
from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from ..config import settings

# Readable during Jinja render (same task as the middleware). Not process-wide.
csp_nonce_var: ContextVar[str] = ContextVar("piherder_csp_nonce", default="")

_SCRIPT_OPEN = re.compile(r"<script\b([^>]*)>", re.IGNORECASE)
_SCRIPT_SRC = re.compile(r"\bsrc\s*=", re.IGNORECASE)
_SCRIPT_NONCE = re.compile(r"\bnonce\s*=", re.IGNORECASE)
_SCRIPT_TYPE_Q = re.compile(
    r"""\btype\s*=\s*(['"])(.*?)\1""", re.IGNORECASE
)
_SCRIPT_TYPE_BARE = re.compile(r"""\btype\s*=\s*([^\s'"]+)""", re.IGNORECASE)
# Empty type and classic/module scripts execute. JSON and templates do not.
_EXECUTABLE_TYPES = frozenset(
    {
        "",
        "text/javascript",
        "application/javascript",
        "application/ecmascript",
        "text/ecmascript",
        "module",
    }
)


def csp_enabled() -> bool:
    return bool(getattr(settings, "PIHERDER_CSP", True))


def current_csp_nonce() -> str:
    """Nonce for this request, or empty outside a stamped response."""
    return csp_nonce_var.get() or ""


def csp_report_only() -> bool:
    """Report-Only when the flag is set, or on the public demo until enforce.

    Home installs enforce the nonce policy. Demo stays Report-Only so a missed
    inline script does not blank the sandbox before an operator opts in with
    ``PIHERDER_CSP_ENFORCE``.
    """
    if bool(getattr(settings, "PIHERDER_CSP_REPORT_ONLY", False)):
        return True
    if bool(getattr(settings, "PIHERDER_CSP_ENFORCE", False)):
        return False
    try:
        from ..services.demo import demo_mode

        if demo_mode():
            return True
    except Exception:
        return False
    return False


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


def _turnstile_on() -> bool:
    site = (getattr(settings, "PIHERDER_TURNSTILE_SITE_KEY", None) or "").strip()
    secret = (getattr(settings, "PIHERDER_TURNSTILE_SECRET_KEY", None) or "").strip()
    return bool(site and secret)


def is_openapi_ui_path(path: str) -> bool:
    """Swagger UI / ReDoc HTML (not the JSON schema)."""
    p = (path or "/").rstrip("/") or "/"
    return p == "/docs" or p.startswith("/docs/") or p == "/redoc" or p.startswith("/redoc/")


def _script_type(attrs: str) -> str:
    quoted = _SCRIPT_TYPE_Q.search(attrs or "")
    if quoted:
        return (quoted.group(2) or "").strip().lower()
    bare = _SCRIPT_TYPE_BARE.search(attrs or "")
    if bare:
        return (bare.group(1) or "").strip().lower()
    return ""


def stamp_inline_scripts(html: str, nonce: str) -> str:
    """Add ``nonce`` to executable inline ``<script>`` tags.

    External ``src`` scripts stay as they are (``'self'`` covers ``/static``).
    ``type=application/json`` (and other non-executed types) stay un-nonced.
    Event-handler attributes are not rewritten.
    """
    token = (nonce or "").strip()
    if not token or not html or "<script" not in html.lower():
        return html

    def repl(match: re.Match) -> str:
        attrs = match.group(1) or ""
        if _SCRIPT_SRC.search(attrs) or _SCRIPT_NONCE.search(attrs):
            return match.group(0)
        if _script_type(attrs) not in _EXECUTABLE_TYPES:
            return match.group(0)
        return f'<script nonce="{token}"{attrs}>'

    return _SCRIPT_OPEN.sub(repl, html)


def build_csp(
    *,
    for_openapi_ui: bool = False,
    for_x_conversion: bool = False,
    nonce: str | None = None,
) -> str:
    """Return the Content-Security-Policy value (no header name)."""
    # App pages: nonce drops script-src 'unsafe-inline' (CSP3). onclick stays
    # via script-src-attr. style-src stays 'unsafe-inline'.
    # OpenAPI /docs and /redoc keep 'unsafe-inline' (stock Swagger/ReDoc).
    # No 'unsafe-eval' — Tailwind is compiled CSS, not Play.
    # connect-src: same origin only. Modern browsers treat 'self' as covering
    # same-origin fetch + WebSocket. Do **not** allow bare ws:/wss: (any host).
    # When PIHERDER_PUBLIC_URL is set, also allow that origin + its ws/wss
    # (Caddy :8443 vs app :8000, or CF orange-cloud).
    connect = ["'self'"]
    origin = _public_origin()
    if origin:
        connect.append(origin)
        if origin.startswith("https://"):
            connect.append("wss://" + origin[len("https://") :])
        elif origin.startswith("http://"):
            connect.append("ws://" + origin[len("http://") :])

    frame_src = ["'self'"]
    style_src = ["'self'", "'unsafe-inline'"]
    script_src_attr = ""
    if for_openapi_ui:
        script_src = ["'self'", "'unsafe-inline'"]
    else:
        script_src = ["'self'"]
        token = (nonce or "").strip()
        if token:
            script_src.append(f"'nonce-{token}'")
        script_src_attr = "script-src-attr 'unsafe-inline'"
    # Cloudflare Turnstile (managed challenge loads scripts/frames/workers/images)
    worker_src = ["'self'"]
    img_src = ["'self'", "data:", "blob:"]
    font_src = ["'self'", "data:"]
    # FastAPI's stock /docs and /redoc load Swagger UI + ReDoc from jsDelivr
    # (and ReDoc pulls Google Fonts). Global CSP stays self-hosted; only these
    # two UI paths get the extra origins so the pages actually render.
    if for_openapi_ui:
        jd = "https://cdn.jsdelivr.net"
        script_src.append(jd)
        style_src.extend([jd, "https://fonts.googleapis.com"])
        font_src.extend(["https://fonts.gstatic.com", "https://fonts.googleapis.com", jd])
        img_src.extend([jd, "https://fastapi.tiangolo.com"])
        if "blob:" not in worker_src:
            worker_src.append("blob:")
    if _turnstile_on():
        cf = "https://challenges.cloudflare.com"
        script_src.append(cf)
        frame_src.append(cf)
        connect.append(cf)
        worker_src.extend([cf, "blob:"])
        img_src.append(cf)
        style_src.append(cf)
    if for_x_conversion:
        # Public demo X conversion pixel only (never home installs).
        tw_js = "https://platform.twitter.com"
        script_src.append(tw_js)
        connect.append(tw_js)
        for tw_img in ("https://analytics.twitter.com", "https://t.co"):
            img_src.append(tw_img)
            connect.append(tw_img)

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
        # Account → Link SSO POSTs here, then GET /auth/oidc/link 303s to the IdP.
        # Do not 303 a *form* straight to Authentik — browsers honour form-action.
        "form-action 'self'",
        "script-src " + " ".join(script_src),
        *([script_src_attr] if script_src_attr else []),
        "style-src " + " ".join(style_src),
        "img-src " + " ".join(img_src),
        "font-src " + " ".join(font_src),
        "connect-src " + " ".join(connect_parts),
        "worker-src " + " ".join(worker_src),
        "child-src " + " ".join(worker_src),
        "manifest-src 'self'",
        "media-src 'self'",
        "frame-src " + " ".join(frame_src),
    ]
    # Only upgrade on HTTPS public URL (avoid breaking plain http labs)
    if origin and origin.startswith("https://"):
        directives.append("upgrade-insecure-requests")

    return "; ".join(directives) + ";"


def security_headers_dict(
    *,
    for_openapi_ui: bool = False,
    for_x_conversion: bool = False,
    nonce: str | None = None,
) -> dict[str, str]:
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
        headers[name] = build_csp(
            for_openapi_ui=for_openapi_ui,
            for_x_conversion=for_x_conversion,
            nonce=None if for_openapi_ui else nonce,
        )
    return headers


async def _stamp_html_response(response: Response, nonce: str) -> Response:
    """Rewrite an HTML body so inline scripts carry this request's nonce.

    Reads ``body_iterator`` (BaseHTTPMiddleware streams the downstream app)
    and puts the stamped bytes back on the same response so ``Set-Cookie``
    headers are not collapsed.
    """
    ctype = (response.headers.get("content-type") or "").lower()
    if "text/html" not in ctype:
        return response
    if (response.headers.get("content-encoding") or "").strip():
        return response
    iterator = getattr(response, "body_iterator", None)
    if iterator is None:
        return response
    chunks: list[bytes] = []
    async for chunk in iterator:
        if isinstance(chunk, str):
            chunks.append(chunk.encode("utf-8"))
        else:
            chunks.append(chunk)
    raw = b"".join(chunks)
    try:
        text = raw.decode("utf-8")
        stamped = stamp_inline_scripts(text, nonce)
        new_body = raw if stamped == text else stamped.encode("utf-8")
    except Exception:
        new_body = raw

    async def _one():
        yield new_body

    response.body_iterator = _one()
    response.headers["content-length"] = str(len(new_body))
    return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach CSP + baseline security headers to every response.

    The nonce is set on ``request.state`` and a context var **before** the
    app renders, so Jinja can read it. Headers are applied after the response.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        nonce = secrets.token_urlsafe(16)
        request.state.csp_nonce = nonce
        token = csp_nonce_var.set(nonce)
        try:
            response = await call_next(request)
            path = request.url.path or ""
            openapi = is_openapi_ui_path(path)
            if csp_enabled() and not openapi:
                response = await _stamp_html_response(response, nonce)
            from ..services.demo import x_conversion_enabled

            for k, v in security_headers_dict(
                for_openapi_ui=openapi,
                for_x_conversion=x_conversion_enabled(request),
                nonce=nonce,
            ).items():
                if k not in response.headers:
                    response.headers[k] = v
            return response
        finally:
            csp_nonce_var.reset(token)
