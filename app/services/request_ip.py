"""Client IP resolution for reverse-proxied requests (Caddy) and audit logging.

Caddy overwrites X-Forwarded-For / X-Real-IP with ``{remote_host}`` so values
are trustworthy when traffic enters via the bundled proxy. Direct hits on the
app port use the TCP peer.

A ContextVar carries the resolved IP for the duration of a request so every
AuditLog write can pick it up without threading Request through all layers.
"""
from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any, Mapping, Optional

_request_client_ip: ContextVar[Optional[str]] = ContextVar(
    "piherder_request_client_ip", default=None
)


def _normalize_ip_candidate(raw: str | None) -> str:
    """Strip brackets / trailing :port from proxy header values."""
    s = (raw or "").strip()
    if not s:
        return ""
    if s.startswith("["):
        end = s.find("]")
        if end > 0:
            return s[1:end]
    # IPv4 host:port (single colon)
    if s.count(":") == 1:
        host, port = s.rsplit(":", 1)
        if port.isdigit():
            return host
    return s


def extract_client_ip(
    headers: Mapping[str, Any] | None,
    peer_host: str | None,
) -> str:
    """Resolve client IP for allowlists and audit.

    Preference (edge proxy should *set* these, not pass client-spoofed values):
      1. X-Forwarded-For — first hop only
      2. X-Real-IP
      3. TCP peer (request.client.host)
    """
    h = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    xff = h.get("x-forwarded-for") or h.get("x-forwarded_for")
    if xff:
        return _normalize_ip_candidate(xff.split(",")[0])
    xri = h.get("x-real-ip") or h.get("x-real_ip")
    if xri:
        return _normalize_ip_candidate(xri)
    return _normalize_ip_candidate(peer_host)


def client_ip_from_request(request: Any) -> Optional[str]:
    """Extract client IP from a Starlette/FastAPI Request."""
    if request is None:
        return None
    peer = None
    try:
        if getattr(request, "client", None) is not None:
            peer = request.client.host
    except Exception:
        peer = None
    try:
        headers = dict(request.headers) if request.headers is not None else {}
    except Exception:
        headers = {}
    ip = extract_client_ip(headers, peer)
    return ip or None


def get_request_client_ip() -> Optional[str]:
    """IP for the current request (set by middleware), or None offline/scheduler."""
    return _request_client_ip.get()


def set_request_client_ip(ip: Optional[str]) -> Token:
    """Bind client IP for this context (middleware / job worker). Returns reset token."""
    return _request_client_ip.set((ip or "").strip() or None)


def reset_request_client_ip(token: Token) -> None:
    _request_client_ip.reset(token)


def bind_client_ip(ip: Optional[str]) -> Token:
    """Alias for set_request_client_ip (readable at call sites)."""
    return set_request_client_ip(ip)
