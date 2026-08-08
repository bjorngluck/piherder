"""Canned device-kind icon ids for maps and discovery (S-icon / M1).

Stable ids match ``nmap.device_classify`` kinds. SVG symbols live in
``templates/partials/device_kind_icons.html`` (``#ph-kind-<id>``).
"""
from __future__ import annotations

from typing import Optional

# Keep in sync with device_classify.VALID_KINDS + map spine extras.
KIND_ICON_IDS: frozenset[str] = frozenset(
    {
        "unknown",
        "raspberry_pi",
        "server",
        "windows",
        "nas",
        "printer",
        "router",
        "access_point",
        "phone",
        "tv",
        "camera",
        "iot",
        "media",
        "network",
        # spine / non-device
        "gateway",
        "lan",
        "app",
    }
)

DEFAULT_KIND_ICON = "unknown"
FLEET_HOST_DEFAULT_ICON = "server"


def icon_id_for_kind(kind: Optional[str], *, is_discovered: bool = False) -> str:
    """Return a symbol id fragment (without ``ph-kind-`` prefix)."""
    k = (kind or "").strip().lower()
    if k in KIND_ICON_IDS and k not in ("gateway", "lan", "app"):
        return k
    if not k or k == "unknown":
        return DEFAULT_KIND_ICON if is_discovered else FLEET_HOST_DEFAULT_ICON
    return DEFAULT_KIND_ICON


def symbol_href(kind: Optional[str], *, is_discovered: bool = False) -> str:
    """``#ph-kind-<id>`` for ``<use href=…>``."""
    return f"#ph-kind-{icon_id_for_kind(kind, is_discovered=is_discovered)}"
