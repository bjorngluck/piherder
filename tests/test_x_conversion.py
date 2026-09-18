"""X conversion pixel: public demo + docs only — never self-hosted installs."""
from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.security import headers as hdr
from app.services import demo as demo_svc


def test_x_conversion_off_without_demo_mode(monkeypatch):
    monkeypatch.setattr(demo_svc.settings, "PIHERDER_DEMO_MODE", False)
    monkeypatch.setattr(
        demo_svc.settings, "PIHERDER_PUBLIC_URL", "https://piherder-demo.hacknow.info"
    )
    assert demo_svc.x_conversion_enabled() is False


def test_x_conversion_off_demo_lab_hostname(monkeypatch):
    monkeypatch.setattr(demo_svc.settings, "PIHERDER_DEMO_MODE", True)
    monkeypatch.setattr(demo_svc.settings, "PIHERDER_PUBLIC_URL", "https://piherder.lan")
    monkeypatch.setattr(demo_svc.settings, "PIHERDER_HOSTNAME", "piherder.lan")
    req = SimpleNamespace(
        url=SimpleNamespace(hostname="piherder.lan"),
        headers={},
    )
    assert demo_svc.x_conversion_enabled(req) is False


def test_x_conversion_on_public_demo_url(monkeypatch):
    monkeypatch.setattr(demo_svc.settings, "PIHERDER_DEMO_MODE", True)
    monkeypatch.setattr(
        demo_svc.settings, "PIHERDER_PUBLIC_URL", "https://piherder-demo.hacknow.info"
    )
    monkeypatch.setattr(demo_svc.settings, "PIHERDER_HOSTNAME", "piherder-demo.hacknow.info")
    assert demo_svc.x_conversion_enabled() is True


def test_x_conversion_on_forwarded_host(monkeypatch):
    monkeypatch.setattr(demo_svc.settings, "PIHERDER_DEMO_MODE", True)
    monkeypatch.setattr(demo_svc.settings, "PIHERDER_PUBLIC_URL", "")
    monkeypatch.setattr(demo_svc.settings, "PIHERDER_HOSTNAME", "")
    req = SimpleNamespace(
        url=SimpleNamespace(hostname="web"),
        headers={"x-forwarded-host": "piherder-demo.hacknow.info"},
    )
    assert demo_svc.x_conversion_enabled(req) is True


def test_csp_twitter_only_when_pixel():
    off = hdr.build_csp(for_x_conversion=False)
    assert "platform.twitter.com" not in off
    assert "analytics.twitter.com" not in off
    assert "t.co" not in off
    on = hdr.build_csp(for_x_conversion=True)
    assert "https://platform.twitter.com" in on
    assert "https://analytics.twitter.com" in on
    assert "https://t.co" in on


def test_login_html_has_no_twitter_on_normal_install(monkeypatch):
    """Home installs never load the pixel. Do not GET /auth/login — that hits Postgres."""
    from pathlib import Path

    monkeypatch.setattr(demo_svc.settings, "PIHERDER_DEMO_MODE", False)
    monkeypatch.setattr(demo_svc.settings, "PIHERDER_PUBLIC_URL", "https://piherder.lan")
    monkeypatch.setattr(demo_svc.settings, "PIHERDER_HOSTNAME", "piherder.lan")
    req = SimpleNamespace(
        url=SimpleNamespace(hostname="piherder.lan"),
        headers={},
    )
    assert demo_svc.x_conversion_enabled(req) is False
    csp = hdr.build_csp(for_x_conversion=False)
    assert "platform.twitter.com" not in csp
    assert "analytics.twitter.com" not in csp
    base = Path("app/templates/base.html").read_text()
    assert "x_conversion_enabled(request)" in base
    assert "partials/x_conversion.html" in base


def test_login_html_has_twitter_on_public_demo(monkeypatch):
    from app.config import settings
    from app.main import app

    monkeypatch.setattr(settings, "PIHERDER_DEMO_MODE", True)
    monkeypatch.setattr(settings, "PIHERDER_PUBLIC_URL", "https://piherder-demo.hacknow.info")
    monkeypatch.setattr(settings, "PIHERDER_HOSTNAME", "piherder-demo.hacknow.info")
    client = TestClient(app)
    r = client.get("/auth/login")
    assert r.status_code == 200
    assert "platform.twitter.com/oct.js" in r.text
    assert "trackPid('rfe8i'" in r.text
    csp = r.headers.get("content-security-policy") or ""
    assert "https://platform.twitter.com" in csp
    assert "https://t.co" in csp
