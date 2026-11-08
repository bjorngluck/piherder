"""Worker metrics port, prefork totals, and Redis log lines."""
from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _run_worker_script(code: str, *, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("PROMETHEUS_MULTIPROC_DIR", None)
    env.pop("PIHERDER_WORKER_METRICS", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_worker_metrics_port_defaults(monkeypatch):
    from app.services import worker_metrics as metrics

    monkeypatch.delenv("PIHERDER_WORKER_METRICS", raising=False)
    monkeypatch.delenv("PIHERDER_WORKER_METRICS_PORT", raising=False)
    assert metrics.worker_metrics_enabled() is False
    assert metrics.worker_metrics_port() == 9809
    assert metrics.serve_worker_metrics() is None


def test_prefork_children_add_lock_and_catchup_counts(tmp_path):
    directory = tmp_path / "metrics"
    directory.mkdir()
    env = {
        "PROMETHEUS_MULTIPROC_DIR": str(directory),
    }
    lock_child = _run_worker_script(
        "from app.services.server_job_lock import note_lock_acquire_failure\n"
        "note_lock_acquire_failure('busy')\n"
        "note_lock_acquire_failure('busy')\n",
        extra_env=env,
    )
    catch_child = _run_worker_script(
        "from app.services.schedule_catchup import note_catchup\n"
        "note_catchup('backup', 'queued')\n",
        extra_env=env,
    )
    assert lock_child.returncode == 0, lock_child.stderr
    assert catch_child.returncode == 0, catch_child.stderr
    collected = _run_worker_script(
        "from prometheus_client import CollectorRegistry, generate_latest, multiprocess\n"
        "registry = CollectorRegistry()\n"
        "multiprocess.MultiProcessCollector(registry)\n"
        "print(generate_latest(registry).decode())\n",
        extra_env=env,
    )
    assert collected.returncode == 0, collected.stderr
    body = collected.stdout
    assert 'piherder_lock_acquire_failures_total{reason="busy"} 2.0' in body
    assert 'piherder_schedule_catchup_total{kind="backup",result="queued"} 1.0' in body


def test_worker_http_serves_the_lock_counter():
    port = _free_port()
    code = textwrap.dedent(
        f"""
        import os
        import urllib.request
        os.environ["PIHERDER_WORKER_METRICS"] = "1"
        os.environ["PIHERDER_WORKER_METRICS_PORT"] = "{port}"
        os.environ.pop("PROMETHEUS_MULTIPROC_DIR", None)
        from app.services.server_job_lock import note_lock_acquire_failure
        from app.services.worker_metrics import serve_worker_metrics
        note_lock_acquire_failure("redis_error")
        bound = serve_worker_metrics()
        if bound != {port}:
            raise SystemExit(f"port {{bound}}")
        body = urllib.request.urlopen(
            "http://127.0.0.1:{port}/metrics", timeout=5
        ).read().decode()
        prefix = 'piherder_lock_acquire_failures_total{{reason="redis_error"}} '
        line = next((item for item in body.splitlines() if item.startswith(prefix)), "")
        if not line:
            raise SystemExit(body)
        if float(line.split()[-1]) < 1:
            raise SystemExit(body)
        print("ok")
        """
    )
    proc = _run_worker_script(code)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "ok" in proc.stdout


def test_compose_redis_password_is_optional_and_hides_the_worker_port():
    text = (ROOT / "docker-compose.yml").read_text()
    assert "${REDIS_PASSWORD:?set REDIS_PASSWORD}" not in text
    assert "REDIS_PASSWORD: ${REDIS_PASSWORD:-}" in text
    assert 'exec /usr/local/bin/docker-entrypoint.sh redis-server --requirepass "$$REDIS_PASSWORD"' in text
    assert "postgresql://piherder:piherder@127.0.0.1:5432/piherder" not in text
    assert 'PIHERDER_WORKER_METRICS: "1"' in text
    assert "PIHERDER_WORKER_METRICS_PORT: ${PIHERDER_WORKER_METRICS_PORT:-9809}" in text
    assert "PROMETHEUS_MULTIPROC_DIR: /tmp/piherder-metrics" in text
    assert "9809:9809" not in text


def test_redis_logs_use_the_exception_type_only(monkeypatch, caplog):
    import redis

    from app.services import backup_progress, server_job_lock as lock
    from app.services.nmap import runtime

    secret = "redis://:secret-value@192.0.2.10:6379/0"

    def _boom(*_args, **_kwargs):
        raise ConnectionError(secret)

    monkeypatch.setattr(redis, "from_url", _boom)
    lock.reset_redis_client_for_tests()
    backup_progress._redis_client = None
    with caplog.at_level(logging.DEBUG):
        assert lock._get_redis() is None
        assert backup_progress._get_redis() is None
        monkeypatch.setattr(runtime, "_redis", _boom)
        assert runtime.try_acquire_lock("host", "host-a", holder="job") is False
        runtime.touch_worker_heartbeat()
        assert runtime.worker_online()["error"] == "ConnectionError"
        fake = type("R", (), {"eval": _boom})()
        monkeypatch.setattr(lock, "_get_redis", lambda: fake)
        assert lock.release_server_lock("backup", 4, "token") is False
        assert lock.refresh_server_lock("backup", 4, "token") is False
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "ConnectionError" in text
    assert "secret-value" not in text
    assert "192.0.2.10" not in text
    assert "redis://" not in text
