"""Brand-1: instance wordmark and one accent.

The official mark and primary red stay. Empty name keeps the Pi+Herder
wordmark. Demo mode always uses that official chrome.
"""
from __future__ import annotations

import os
import re

OFFICIAL_NAME = "PiHerder"
DEFAULT_ACCENT = "#00a651"
_NAME_MAX = 40
_ACCENT_RE = re.compile(r"^#[0-9a-f]{6}$")
_PRIMARY_RED = "#e60012"


def _demo() -> bool:
    from .demo import demo_mode

    return bool(demo_mode())


def name_env_locked() -> bool:
    return bool((os.environ.get("PIHERDER_INSTANCE_NAME") or "").strip())


def accent_env_locked() -> bool:
    return bool((os.environ.get("PIHERDER_ACCENT") or "").strip())


def clean_instance_name(raw) -> str:
    text = " ".join(str(raw or "").split())
    return text[:_NAME_MAX]


def normalize_accent(raw) -> str:
    """Return '' for empty/official, or #rrggbb. Raise ValueError when invalid."""
    text = str(raw or "").strip()
    if not text:
        return ""
    if not text.startswith("#"):
        text = "#" + text
    text = text.lower()
    if text == DEFAULT_ACCENT:
        return ""
    if not _ACCENT_RE.fullmatch(text):
        raise ValueError("Accent must be a #RRGGBB color")
    if text == _PRIMARY_RED:
        raise ValueError("Accent cannot replace the primary red")
    return text


def _stored() -> tuple[str, str]:
    try:
        from .app_settings import load_settings

        raw = load_settings() or {}
    except Exception:
        raw = {}
    name = clean_instance_name(raw.get("instance_name"))
    try:
        accent = normalize_accent(raw.get("instance_accent"))
    except ValueError:
        accent = ""
    return name, accent


def effective_brand() -> dict:
    """What the chrome should show. Demo ignores saved name and accent."""
    if _demo():
        return {
            "name": "",
            "plain": OFFICIAL_NAME,
            "accent": "",
            "custom_name": False,
            "custom_accent": False,
            "name_locked": False,
            "accent_locked": False,
            "demo": True,
        }
    stored_name, stored_accent = _stored()
    if name_env_locked():
        name = clean_instance_name(os.environ.get("PIHERDER_INSTANCE_NAME"))
    else:
        name = stored_name
    if accent_env_locked():
        try:
            accent = normalize_accent(os.environ.get("PIHERDER_ACCENT"))
        except ValueError:
            accent = ""
    else:
        accent = stored_accent
    return {
        "name": name,
        "plain": name or OFFICIAL_NAME,
        "accent": accent,
        "custom_name": bool(name),
        "custom_accent": bool(accent),
        "name_locked": name_env_locked(),
        "accent_locked": accent_env_locked(),
        "demo": False,
    }


def accent_override_css(accent: str | None = None) -> str:
    """Override accent tokens only. Does not set --color-primary."""
    brand = effective_brand() if accent is None else {"accent": accent, "custom_accent": bool(accent)}
    hex_color = brand.get("accent") or ""
    if not hex_color:
        return ""
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    light = f"rgba({r}, {g}, {b}, 0.10)"
    dark = f"rgba({r}, {g}, {b}, 0.15)"
    return (
        f":root {{ --color-accent: {hex_color}; --accent-subtle-bg: {light}; }}\n"
        f'[data-theme="dark"] {{ --color-accent: {hex_color}; --accent-subtle-bg: {dark}; }}\n'
    )
