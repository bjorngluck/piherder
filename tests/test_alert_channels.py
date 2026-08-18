"""Wh-lite webhook + H-lite SMTP helpers (no live SMTP)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services import alert_channels as ch
from app.services import app_settings as app_cfg


@pytest.fixture(autouse=True)
def _isolate_settings(tmp_path, monkeypatch):
    app_cfg.clear_cache()
    yield
    app_cfg.clear_cache()


def test_validate_webhook_url():
    assert ch.validate_webhook_url("https://n8n.example/hook") == "https://n8n.example/hook"
    with pytest.raises(ValueError):
        ch.validate_webhook_url("ftp://x")


def test_send_webhook_respects_event_filter(monkeypatch):
    monkeypatch.setattr(
        ch,
        "webhook_config",
        lambda: {
            "url": "https://hook.example/x",
            "number": "",
            "recipients_raw": "[]",
            "secret": "",
            "events_notifications": False,
            "events_jobs": True,
            "events_backup": True,
            "min_severity": "warning",
        },
    )
    r = ch.send_webhook("hi", event="notification", severity="critical")
    assert r.get("skipped") is True


def test_send_webhook_posts(monkeypatch):
    monkeypatch.setattr(
        ch,
        "webhook_config",
        lambda: {
            "url": "https://hook.example/x",
            "number": "+1",
            "recipients_raw": '["a"]',
            "secret": "s3cret",
            "events_notifications": True,
            "events_jobs": True,
            "events_backup": True,
            "min_severity": "info",
        },
    )
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    with patch("app.services.alert_channels.httpx.post", return_value=mock_resp) as post:
        r = ch.send_webhook("hello", event="notification", severity="warning")
    assert r.get("ok") is True
    args, kwargs = post.call_args
    assert args[0] == "https://hook.example/x"
    assert kwargs["json"]["message"] == "hello"
    assert kwargs["headers"]["Authorization"] == "Bearer s3cret"


def test_password_reset_token_roundtrip(session_factory=None):
    """Unit-level hash consume without DB if fixtures missing."""
    from app.services import password_reset as pr

    h1 = pr._hash_token("abc")
    h2 = pr._hash_token("abc")
    assert h1 == h2
    assert h1 != pr._hash_token("abd")


def test_configured_public_origin_from_explicit_url():
    from app.services.password_reset import configured_public_origin

    assert (
        configured_public_origin("https://piherder.example.com:8443/extra")
        == "https://piherder.example.com:8443"
    )
    assert configured_public_origin("") == ""
    assert configured_public_origin("not-a-url") == ""
    assert configured_public_origin("ftp://files.example") == ""
