"""Brand-1: instance name and one accent. Official mark and primary red stay."""
from __future__ import annotations

from fastapi.testclient import TestClient
from markupsafe import Markup

from app.main import app

from app.services.instance_brand import (
    accent_override_css,
    catalog_nav_visible,
    effective_brand,
    normalize_accent,
)
from app.templates import instance_plain_name, instance_wordmark


def test_empty_name_keeps_official_wordmark(monkeypatch):
    monkeypatch.delenv("PIHERDER_INSTANCE_NAME", raising=False)
    monkeypatch.delenv("PIHERDER_ACCENT", raising=False)
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    monkeypatch.setattr(
        "app.services.app_settings.load_settings",
        lambda: {"instance_name": "", "instance_accent": ""},
    )
    brand = effective_brand()
    assert brand["custom_name"] is False
    assert brand["plain"] == "PiHerder"
    html = str(instance_wordmark())
    assert "ph-brand-pi" in html
    assert "ph-brand-herder" in html
    assert accent_override_css() == ""


def test_name_and_accent_apply_without_touching_primary(monkeypatch):
    monkeypatch.delenv("PIHERDER_INSTANCE_NAME", raising=False)
    monkeypatch.delenv("PIHERDER_ACCENT", raising=False)
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    monkeypatch.setattr(
        "app.services.app_settings.load_settings",
        lambda: {"instance_name": "Homelab <x>", "instance_accent": "#112233"},
    )
    brand = effective_brand()
    assert brand["plain"] == "Homelab <x>"
    html = str(instance_wordmark())
    assert "Homelab &lt;x&gt;" in html
    assert "<script" not in html
    assert isinstance(instance_wordmark(), Markup)
    css = accent_override_css()
    assert "--color-accent: #112233" in css
    assert "--accent-subtle-bg:" in css
    assert "--color-primary" not in css
    assert "#e60012" not in css
    assert instance_plain_name() == "Homelab <x>"


def test_primary_red_is_rejected_as_accent():
    try:
        normalize_accent("#e60012")
        raised = False
    except ValueError:
        raised = True
    assert raised is True
    assert normalize_accent("#00a651") == ""
    assert normalize_accent("") == ""


def test_env_locks_name_and_accent(monkeypatch):
    monkeypatch.setenv("PIHERDER_INSTANCE_NAME", "Locked")
    monkeypatch.setenv("PIHERDER_ACCENT", "#abcdef")
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    monkeypatch.setattr(
        "app.services.app_settings.load_settings",
        lambda: {"instance_name": "Other", "instance_accent": "#111111"},
    )
    brand = effective_brand()
    assert brand["name"] == "Locked"
    assert brand["accent"] == "#abcdef"
    assert brand["name_locked"] is True
    assert brand["accent_locked"] is True


def test_manifest_name_follows_instance_and_keeps_red(monkeypatch):
    monkeypatch.setattr(
        "app.services.instance_brand.effective_brand",
        lambda: {
            "plain": "Homelab",
            "name": "Homelab",
            "accent": "#112233",
            "custom_name": True,
            "custom_accent": True,
            "demo": False,
        },
    )
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/manifest.webmanifest")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Homelab"
    assert body["short_name"] == "Homelab"
    assert body["theme_color"] == "#e60012"
    assert "icon-192.png" in body["icons"][0]["src"]


def test_catalog_nav_defaults_on_and_hides_for_every_role(monkeypatch):
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    monkeypatch.setattr(
        "app.services.app_settings.load_settings",
        lambda: {"catalog_nav_hidden": False},
    )
    assert catalog_nav_visible() is True
    monkeypatch.setattr(
        "app.services.app_settings.load_settings",
        lambda: {"catalog_nav_hidden": True},
    )
    assert catalog_nav_visible() is False
    brand = effective_brand()
    assert brand["catalog_hidden"] is True


def test_demo_keeps_catalog_in_the_nav(monkeypatch):
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: True)
    monkeypatch.setattr(
        "app.services.app_settings.load_settings",
        lambda: {"catalog_nav_hidden": True, "instance_name": "Other"},
    )
    assert catalog_nav_visible() is True
    assert effective_brand()["catalog_hidden"] is False


def test_demo_ignores_saved_brand(monkeypatch):
    monkeypatch.setenv("PIHERDER_INSTANCE_NAME", "Locked")
    monkeypatch.setenv("PIHERDER_ACCENT", "#abcdef")
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: True)
    monkeypatch.setattr(
        "app.services.app_settings.load_settings",
        lambda: {"instance_name": "Other", "instance_accent": "#111111"},
    )
    brand = effective_brand()
    assert brand["demo"] is True
    assert brand["custom_name"] is False
    assert brand["custom_accent"] is False
    assert brand["plain"] == "PiHerder"
    assert "ph-brand-herder" in str(instance_wordmark())
    assert accent_override_css() == ""
