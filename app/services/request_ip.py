"""Client IP resolution for reverse-proxied requests (Caddy) and audit logging.

Forwarded headers (CF-Connecting-IP / X-Forwarded-For / X-Real-IP) are used
**only** when the TCP peer sits in ``PIHERDER_TRUSTED_PROXY_CIDRS``. Empty
CIDRs (app default) = never trust client-supplied headers — use the peer.

Bundled Compose sets RFC1918 + loopback so Caddy on the Docker network is
trusted. Direct hits on the app port from an untrusted peer cannot spoof
allowlists, rate limits, or console IP binding.

A ContextVar carries the resolved IP for the duration of a request so every
AuditLog write can pick it up without threading Request through all layers.
"""
from __future__ import annotations

import ipaddress
from contextvars import ContextVar, Token
from typing import Any, Iterable, Mapping, Optional, Sequence

_request_client_ip: ContextVar[Optional[str]] = ContextVar(
    "piherder_request_client_ip", default=None
)

# Compose default — Caddy on the Docker bridge. Not applied unless env/compose sets it.
DEFAULT_COMPOSE_TRUSTED_PROXY_CIDRS = (
    "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.1/32,::1/128"
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


def parse_trusted_proxy_cidrs(
    raw: str | None,
) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """Parse comma/space-separated CIDRs. Invalid tokens are skipped."""
    text = (raw or "").strip()
    if not text:
        return []
    out: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for part in text.replace(";", ",").split(","):
        token = part.strip()
        if not token:
            continue
        try:
            out.append(ipaddress.ip_network(token, strict=False))
        except ValueError:
            continue
    return out


def trusted_proxy_cidrs_from_settings() -> list[
    ipaddress.IPv4Network | ipaddress.IPv6Network
]:
    try:
        from ..config import settings

        return parse_trusted_proxy_cidrs(
            getattr(settings, "PIHERDER_TRUSTED_PROXY_CIDRS", None)
        )
    except Exception:
        return []


def peer_is_trusted_proxy(
    peer_host: str | None,
    cidrs: Sequence[ipaddress.IPv4Network | ipaddress.IPv6Network]
    | Iterable[str]
    | None = None,
) -> bool:
    """True when *peer_host* is an IP inside the trusted-proxy list."""
    nets: list[ipaddress.IPv4Network | ipaddress.IPv6Network]
    if cidrs is None:
        nets = trusted_proxy_cidrs_from_settings()
    else:
        nets = []
        for item in cidrs:
            if isinstance(item, (ipaddress.IPv4Network, ipaddress.IPv6Network)):
                nets.append(item)
            else:
                nets.extend(parse_trusted_proxy_cidrs(str(item)))
    if not nets:
        return False
    raw = _normalize_ip_candidate(peer_host)
    if not raw:
        return False
    try:
        addr = ipaddress.ip_address(raw)
    except ValueError:
        return False
    return any(addr in net for net in nets)


def _forwarded_ip_from_headers(headers: Mapping[str, Any] | None) -> str:
    """First hop from CF / XFF / X-Real-IP. Empty if none present."""
    h = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    for key in ("cf-connecting-ip", "true-client-ip"):
        val = h.get(key)
        if val:
            return _normalize_ip_candidate(val.split(",")[0])
    xff = h.get("x-forwarded-for") or h.get("x-forwarded_for")
    if xff:
        return _normalize_ip_candidate(xff.split(",")[0])
    xri = h.get("x-real-ip") or h.get("x-real_ip")
    if xri:
        return _normalize_ip_candidate(xri)
    return ""


def extract_client_ip(
    headers: Mapping[str, Any] | None,
    peer_host: str | None,
    *,
    trust_forwarded: bool | None = None,
    trusted_cidrs: Sequence[str] | None = None,
) -> str:
    """Resolve client IP for allowlists, audit, and Turnstile remoteip.

    When ``trust_forwarded`` is True (or the TCP peer is in trusted CIDRs),
    preference is:

      1. CF-Connecting-IP / True-Client-IP
      2. X-Forwarded-For — first hop only
      3. X-Real-IP
      4. TCP peer (request.client.host)

    Otherwise forwarded headers are ignored (fail closed).
    """
    peer = _normalize_ip_candidate(peer_host)
    if trust_forwarded is None:
        if trusted_cidrs is not None:
            trust_forwarded = peer_is_trusted_proxy(peer, trusted_cidrs)
        else:
            trust_forwarded = peer_is_trusted_proxy(peer)
    if trust_forwarded:
        forwarded = _forwarded_ip_from_headers(headers)
        if forwarded:
            return forwarded
    return peer


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
