"""Webhook targets refuse private addresses unless an allowlist opts them in."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services import alert_channels as ch


def test_private_literal_and_resolved_targets_are_refused(monkeypatch):
    monkeypatch.delenv("PIHERDER_WEBHOOK_ALLOW_CIDRS", raising=False)
    for url in (
        "http://127.0.0.1/hook",
        "http://10.1.2.3/hook",
        "http://192.168.1.9/hook",
        "http://169.254.169.254/latest",
        "http://[::1]/hook",
        "http://[fe80::1]/hook",
    ):
        with pytest.raises(ValueError) as caught:
            ch.validate_webhook_url(url)
        assert "not allowed" in str(caught.value)
        assert "127.0.0.1" not in str(caught.value)
        assert "169.254" not in str(caught.value)

    monkeypatch.setattr(ch, "_resolve_webhook_host", lambda host: ["10.1.2.3"])
    with pytest.raises(ValueError):
        ch.validate_webhook_url("https://hooks.example.com/notify")

    monkeypatch.setattr(
        ch, "_resolve_webhook_host", lambda host: ["203.0.113.10", "192.168.0.5"]
    )
    with pytest.raises(ValueError):
        ch.validate_webhook_url("https://hooks.example.com/notify")


def test_public_target_and_allowlist_pass(monkeypatch):
    monkeypatch.delenv("PIHERDER_WEBHOOK_ALLOW_CIDRS", raising=False)
    assert ch.validate_webhook_url("https://203.0.113.10/hook") == "https://203.0.113.10/hook"
    monkeypatch.setattr(ch, "_resolve_webhook_host", lambda host: ["192.168.0.5"])
    with pytest.raises(ValueError):
        ch.validate_webhook_url("https://hooks.example.com/notify")
    monkeypatch.setenv("PIHERDER_WEBHOOK_ALLOW_CIDRS", "192.168.0.0/24")
    assert (
        ch.validate_webhook_url("https://hooks.example.com/notify")
        == "https://hooks.example.com/notify"
    )


def test_send_refuses_a_private_target_without_posting(monkeypatch):
    monkeypatch.setattr(
        ch,
        "webhook_config",
        lambda: {
            "url": "http://10.1.2.3/hook",
            "number": "",
            "recipients_raw": "[]",
            "secret": "",
            "events_notifications": True,
            "events_jobs": True,
            "events_backup": True,
            "min_severity": "info",
            "notify_categories": None,
        },
    )
    with patch("app.services.alert_channels.httpx.post") as post:
        result = ch.send_webhook("hello", event="notification", severity="warning")
    post.assert_not_called()
    assert result["ok"] is False
    assert result["error"] == "webhook target is not allowed"
    assert "10.1.2.3" not in result["error"]


def test_send_does_not_follow_redirects(monkeypatch):
    monkeypatch.setattr(ch, "_resolve_webhook_host", lambda host: ["203.0.113.10"])
    monkeypatch.setattr(
        ch,
        "webhook_config",
        lambda: {
            "url": "https://hooks.example.com/notify",
            "number": "",
            "recipients_raw": "[]",
            "secret": "",
            "events_notifications": True,
            "events_jobs": True,
            "events_backup": True,
            "min_severity": "info",
            "notify_categories": None,
        },
    )
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    with patch("app.services.alert_channels.httpx.post", return_value=mock_resp) as post:
        result = ch.send_webhook("hello", event="test", severity="info")
    assert result["ok"] is True
    assert post.call_args.kwargs["follow_redirects"] is False
