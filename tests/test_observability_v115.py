"""Stack-check age gauge, alert delivery counter, starter rules."""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

from app.services import metrics as metrics_svc
from app.services.delivery_metrics import delivery_failure_counts

ROOT = Path(__file__).resolve().parents[1]


class _Frozen(datetime):
    @classmethod
    def utcnow(cls):
        return datetime(2026, 1, 1, 0, 2, 0)


def _body(monkeypatch, checked_at: str | None) -> str:
    monkeypatch.setattr(metrics_svc, "datetime", _Frozen)
    monkeypatch.setattr(metrics_svc, "_db_up", lambda _session: 0)
    report = None if checked_at is None else {"checked_at": checked_at, "components": []}
    monkeypatch.setattr(
        "app.services.stack_health.load_last_report",
        lambda: report,
    )
    return metrics_svc.metrics_body(MagicMock())


def test_stack_check_age_appears_and_moves(monkeypatch):
    older = _body(monkeypatch, "2026-01-01T00:00:00Z")
    newer = _body(monkeypatch, "2026-01-01T00:01:30Z")
    assert "piherder_stack_check_age_seconds 120" in older
    assert "piherder_stack_check_age_seconds 30" in newer
    assert "# TYPE piherder_stack_check_age_seconds gauge" in older


def test_stack_check_age_omitted_without_a_timestamp(monkeypatch):
    body = _body(monkeypatch, None)
    assert "piherder_stack_check_age_seconds" not in body


def test_webhook_failure_moves_the_counter(monkeypatch):
    from app.services import alert_channels as channels

    before = delivery_failure_counts()["webhook"]
    monkeypatch.setattr(
        channels,
        "webhook_config",
        lambda: {
            "url": "http://203.0.113.10/hook",
            "events_notifications": True,
            "events_jobs": True,
            "events_backup": True,
            "min_severity": "info",
            "number": "",
            "recipients_raw": "",
            "secret": "",
            "notify_categories": None,
        },
    )

    def _boom(*_args, **_kwargs):
        raise ConnectionError("broker-down")

    monkeypatch.setattr(channels.httpx, "post", _boom)
    result = channels.send_webhook("hello", event="notification", severity="warning")
    assert result["ok"] is False
    assert delivery_failure_counts()["webhook"] == before + 1
    body = _body(monkeypatch, "2026-01-01T00:01:30Z")
    assert (
        f'piherder_alert_delivery_failures_total{{channel="webhook"}} {before + 1}'
        in body
    )
    assert "203.0.113.10" not in body
    assert "broker-down" not in body
    assert "# TYPE piherder_alert_delivery_failures_total counter" in body


def test_skipped_webhook_does_not_count(monkeypatch):
    from app.services import alert_channels as channels

    before = delivery_failure_counts()["webhook"]
    monkeypatch.setattr(
        channels,
        "webhook_config",
        lambda: {
            "url": "http://203.0.113.10/hook",
            "events_notifications": False,
            "events_jobs": True,
            "events_backup": True,
            "min_severity": "info",
            "number": "",
            "recipients_raw": "",
            "secret": "",
            "notify_categories": None,
        },
    )
    result = channels.send_webhook("hello", event="notification", severity="warning")
    assert result.get("skipped") is True
    assert delivery_failure_counts()["webhook"] == before


def test_email_failure_moves_the_counter(monkeypatch):
    from app.services import alert_channels as channels

    before = delivery_failure_counts()["email"]
    monkeypatch.setattr(
        channels,
        "smtp_config",
        lambda: {
            "alert_enabled": True,
            "alert_min_severity": "warning",
            "notify_categories": None,
            "alert_to": "user@example.com",
            "from_email": "user@example.com",
        },
    )
    monkeypatch.setattr(channels, "smtp_ready", lambda: True)
    monkeypatch.setattr(
        channels,
        "send_email",
        lambda **_kwargs: {"ok": False, "error": "mailbox-down"},
    )
    result = channels.maybe_email_notification(
        severity="warning",
        title="Stack",
        body="check failed",
        category="stack",
    )
    assert result["ok"] is False
    assert delivery_failure_counts()["email"] == before + 1
    body = _body(monkeypatch, "2026-01-01T00:01:30Z")
    assert "mailbox-down" not in body
    assert "user@example.com" not in body


def test_starter_dashboard_names_both_series():
    raw = (ROOT / "contrib" / "grafana" / "piherder-starter.json").read_text()
    dashboard = json.loads(raw)
    assert "piherder_stack_check_age_seconds" in raw
    assert "piherder_alert_delivery_failures_total" in raw
    assert dashboard["title"] == "PiHerder starter"


def test_promtool_accepts_starter_rules():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_prom_rules.py")],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "promtool check rules ok" in proc.stdout
