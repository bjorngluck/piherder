"""GET /metrics/worker proxies a fixed internal URL and clears stale child files."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.services import worker_metrics as wm


def test_worker_start_clears_stale_multiproc_files(tmp_path, monkeypatch):
    stale = tmp_path / "dead-child.db"
    stale.write_text("old-count")
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path))
    from app.celery_app import _clear_worker_metrics_dir

    _clear_worker_metrics_dir()
    assert tmp_path.is_dir()
    assert not stale.exists()


def test_worker_proxy_uses_the_env_url_and_returns_the_body(monkeypatch):
    seen = {}

    class _Response:
        status_code = 200
        content = b'piherder_lock_acquire_failures_total{reason="busy"} 2.0\n'
        headers = {"content-type": "text/plain; version=0.0.4; charset=utf-8"}

    def _get(url, timeout, follow_redirects):
        seen["url"] = url
        seen["timeout"] = timeout
        seen["follow_redirects"] = follow_redirects
        return _Response()

    monkeypatch.setattr(settings, "METRICS_TOKEN", "metrics-secret")
    monkeypatch.setenv("PIHERDER_WORKER_METRICS_URL", "http://celery-worker:9809/metrics")
    monkeypatch.setattr("httpx.get", _get)
    client = TestClient(app)
    denied = client.get(
        "/metrics/worker",
        params={"url": "http://192.0.2.50/metrics"},
    )
    assert denied.status_code == 401
    assert "url" not in seen

    ok = client.get(
        "/metrics/worker",
        params={"url": "http://192.0.2.50/metrics"},
        headers={"Authorization": "Bearer metrics-secret"},
    )
    assert ok.status_code == 200
    assert ok.content == _Response.content
    assert seen["url"] == "http://celery-worker:9809/metrics"
    assert seen["timeout"] == 2.0
    assert seen["follow_redirects"] is False
    assert "192.0.2.50" not in ok.text


def test_worker_proxy_fail_closed_body_has_no_exception_text(monkeypatch):
    def _get(url, timeout, follow_redirects):
        raise TimeoutError("timed out talking to http://celery-worker:9809/metrics")

    monkeypatch.setattr(settings, "METRICS_TOKEN", "")
    monkeypatch.delenv("PIHERDER_WORKER_METRICS_URL", raising=False)
    monkeypatch.setattr("httpx.get", _get)
    client = TestClient(app)
    response = client.get("/metrics/worker", params={"target": "http://192.0.2.51/"})
    assert response.status_code == 503
    assert response.text == wm.WORKER_METRICS_UNAVAILABLE
    assert "timed out" not in response.text
    assert "celery-worker" not in response.text
    assert "192.0.2.51" not in response.text
    assert wm.configured_worker_metrics_url() == wm.DEFAULT_WORKER_METRICS_URL
