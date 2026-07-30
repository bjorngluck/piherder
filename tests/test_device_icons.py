"""Canned device-kind icon id mapping (map M1 / S-icon)."""
from __future__ import annotations

from app.services.device_icons import (
    FLEET_HOST_DEFAULT_ICON,
    icon_id_for_kind,
    symbol_href,
)
from app.services.nmap import device_classify as dc


def test_icon_ids_cover_classify_kinds():
    for kind in dc.VALID_KINDS:
        assert icon_id_for_kind(kind, is_discovered=True) == kind


def test_fleet_default_and_unknown():
    assert icon_id_for_kind(None, is_discovered=False) == FLEET_HOST_DEFAULT_ICON
    assert icon_id_for_kind("", is_discovered=False) == FLEET_HOST_DEFAULT_ICON
    assert icon_id_for_kind(None, is_discovered=True) == "unknown"
    assert icon_id_for_kind("nope", is_discovered=True) == "unknown"


def test_symbol_href():
    assert symbol_href("nas") == "#ph-kind-nas"
    assert symbol_href(None, is_discovered=False) == "#ph-kind-server"
