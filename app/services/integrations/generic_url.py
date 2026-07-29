"""Generic URL integration — bookmark + reachability for HA / Frigate / n8n / custom.

Not a deep vendor adapter: stores a base URL, optional product preset, optional
health path, and optional encrypted bearer token for the probe. Bindings reuse
role=service so chips appear on server Services / fleet Services.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 12.0
DEFAULT_POLL_SEC = 120

# product key → (label, default display name, default health_path)
PRODUCTS: dict[str, tuple[str, str, str]] = {
    "home_assistant": ("Home Assistant", "Home Assistant", "/"),
    "frigate": ("Frigate", "Frigate", "/api/version"),
    "n8n": ("n8n", "n8n", "/healthz"),
    "custom": ("Custom URL", "Custom link", "/"),
}
PRODUCT_KEYS = tuple(PRODUCTS.keys())


def normalize_product(raw: str | None) -> str:
    p = (raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    if p in ("ha", "hass", "homeassistant"):
        return "home_assistant"
    if p in ("n8n_workflow", "n8n_io"):
        return "n8n"
    if p in PRODUCTS:
        return p
    return "custom"


def product_label(product: str) -> str:
    return PRODUCTS.get(normalize_product(product), PRODUCTS["custom"])[0]


def default_name_for_product(product: str) -> str:
    return PRODUCTS.get(normalize_product(product), PRODUCTS["custom"])[1]


def default_health_path(product: str) -> str:
    return PRODUCTS.get(normalize_product(product), PRODUCTS["custom"])[2]


def normalize_base_url(url: str) -> str:
    u = (url or "").strip().rstrip("/")
    if not u:
        raise ValueError("Base URL is required")
    parsed = urlparse(u)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Base URL must be http(s)://host[:port]")
    return u


def normalize_path(path: str | None) -> str:
    """Return path starting with / or empty (means base only)."""
    p = (path or "").strip()
    if not p or p == "/":
        return "/"
    if p.startswith("http://") or p.startswith("https://"):
        # Absolute override handled by open_url; for health keep as-is only if same host
        return p
    if not p.startswith("/"):
        p = "/" + p
    return p


def join_url(base_url: str, path: str = "") -> str:
    base = (base_url or "").strip().rstrip("/")
    p = (path or "").strip()
    if not p or p == "/":
        return base or ""
    if p.startswith("http://") or p.startswith("https://"):
        return p
    if not p.startswith("/"):
        p = "/" + p
    return urljoin(base + "/", p.lstrip("/"))


def open_url(base_url: str, path: str = "") -> str:
    return join_url(base_url, path)


@dataclass
class GenericProbeResult:
    ok: bool
    error: Optional[str] = None
    status_code: Optional[int] = None
    final_url: str = ""
    product: str = "custom"
    health_path: str = "/"

    def to_status_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "error": self.error,
            "status_code": self.status_code,
            "final_url": self.final_url,
            "product": self.product,
            "health_path": self.health_path,
            # list UI alias
            "monitor_count": 1 if self.ok else 0,
        }


def probe(
    base_url: str,
    *,
    health_path: str = "/",
    tls_verify: bool = True,
    bearer_token: str = "",
    product: str = "custom",
    timeout: float = DEFAULT_TIMEOUT,
) -> GenericProbeResult:
    """HTTP GET health (or base). 2xx/3xx = reachable."""
    prod = normalize_product(product)
    try:
        base = normalize_base_url(base_url)
    except ValueError as e:
        return GenericProbeResult(ok=False, error=str(e), product=prod)

    path = normalize_path(health_path) if health_path is not None else default_health_path(prod)
    # Absolute http path: use as full URL
    if path.startswith("http://") or path.startswith("https://"):
        url = path
        path_label = path
    else:
        url = join_url(base, path if path != "/" else "")
        path_label = path if path != "/" else "/"

    headers: dict[str, str] = {"Accept": "application/json, text/plain, */*"}
    tok = (bearer_token or "").strip()
    if tok:
        headers["Authorization"] = f"Bearer {tok}"

    try:
        with httpx.Client(
            timeout=timeout,
            verify=tls_verify,
            follow_redirects=True,
            headers=headers,
        ) as client:
            r = client.get(url)
        code = r.status_code
        # Accept any 2xx/3xx; also 401/403 as "reachable but auth required"
        if 200 <= code < 400:
            return GenericProbeResult(
                ok=True,
                status_code=code,
                final_url=str(r.url),
                product=prod,
                health_path=path_label,
            )
        if code in (401, 403):
            return GenericProbeResult(
                ok=True,
                status_code=code,
                final_url=str(r.url),
                product=prod,
                health_path=path_label,
                error=f"HTTP {code} (reachable; auth may be required)",
            )
        return GenericProbeResult(
            ok=False,
            error=f"HTTP {code}",
            status_code=code,
            final_url=str(r.url),
            product=prod,
            health_path=path_label,
        )
    except httpx.TimeoutException:
        return GenericProbeResult(
            ok=False,
            error="timeout",
            product=prod,
            health_path=path_label,
        )
    except Exception as e:
        logger.debug("generic_url probe failed: %s", e)
        return GenericProbeResult(
            ok=False,
            error=str(e)[:200],
            product=prod,
            health_path=path_label,
        )
