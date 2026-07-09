"""Prometheus /metrics helpers and token gate."""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services import metrics as metrics_svc
from app.models import Server


def test_render_prometheus_format():
    body = metrics_svc.render_prometheus(
        [
            ("piherder_up", "up", ["piherder_up 1"]),
            ("piherder_jobs", "jobs", ['piherder_jobs{status="pending"} 2']),
        ]
    )
    assert "# HELP piherder_up up" in body
    assert "# TYPE piherder_up gauge" in body
    assert "piherder_up 1" in body
    assert 'piherder_jobs{status="pending"} 2' in body


def test_esc_label_quotes():
    line = metrics_svc._line("m", 1, {"type": 'a"b'})
    assert r'a\"b' in line


def test_backup_counts_stale():
    now = datetime.utcnow()
    servers = [
        Server(
            id=1,
            name="a",
            hostname="a",
            backup_enabled=True,
            last_backup_at=now - timedelta(hours=1),
        ),
        Server(
            id=2,
            name="b",
            hostname="b",
            backup_enabled=True,
            last_backup_at=now - timedelta(hours=48),
        ),
        Server(
            id=3,
            name="c",
            hostname="c",
            backup_enabled=True,
            last_backup_at=None,
        ),
        Server(
            id=4,
            name="d",
            hostname="d",
            backup_enabled=False,
            last_backup_at=None,
        ),
    ]
    enabled, stale = metrics_svc._backup_counts(servers, stale_hours=36)
    assert enabled == 3
    assert stale == 2


def test_metrics_token_gate():
    from app.routers import metrics as metrics_router

    assert metrics_router._token_ok("secret", "secret") is True
    assert metrics_router._token_ok("wrong", "secret") is False
    assert metrics_router._token_ok("", "secret") is False
    assert metrics_router._token_ok("anything", "") is True


def test_collect_samples_uses_fleet(monkeypatch):
    session = MagicMock()
    # first call is db ping select(1) path — make exec not raise
    session.exec.return_value.first.return_value = 1
    session.exec.return_value.all.return_value = []
    session.exec.return_value.one.return_value = 0

    monkeypatch.setattr(
        metrics_svc,
        "summarize_fleet",
        lambda servers: {
            "server_count": 2,
            "attention_count": 1,
            "reboot_count": 0,
            "os_host_count": 1,
            "container_host_count": 0,
            "total_os_packages": 3,
            "total_container_projects": 0,
            "never_checked_os": 0,
            "never_checked_containers": 1,
            "rows": [],
            "attention_rows": [],
            "healthy_count": 1,
        },
    )
    monkeypatch.setattr(metrics_svc, "_db_up", lambda s: 1)
    monkeypatch.setattr(
        metrics_svc, "_job_status_counts", lambda s: {"pending": 0, "running": 1, "success": 5, "failed": 0}
    )
    monkeypatch.setattr(metrics_svc, "_jobs_failed_24h", lambda s: 0)
    monkeypatch.setattr(metrics_svc, "_open_notifications_by_type", lambda s: {"backup_failed": 1})
    monkeypatch.setattr(metrics_svc, "_backup_counts", lambda servers, h: (1, 0))

    # select(Server) path
    with patch.object(session, "exec", side_effect=lambda q: SimpleNamespace(all=lambda: [], first=lambda: 1, one=lambda: 0)):
        body = metrics_svc.metrics_body(session)

    assert "piherder_up 1" in body
    assert "piherder_servers 2" in body
    assert "piherder_servers_attention 1" in body
    assert 'piherder_jobs{status="running"} 1' in body
    assert 'piherder_notifications_open_by_type{type="backup_failed"} 1' in body
