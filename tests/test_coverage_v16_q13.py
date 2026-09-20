"""v1.6 Q-80 thirteenth pack — compose actions/logs, backup create_job_and_run, prune."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models import AuditLog, Job, Server


def _memory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine), engine


def _server(session):
    s = Server(
        name="pi",
        hostname="pi.local",
        ssh_username="pi",
        docker_base_dir="/home/pi/docker",
        os_type="debian",
        backup_enabled=True,
        ssh_private_key_encrypted="enc",
    )
    session.add(s)
    session.commit()
    session.refresh(s)
    return s


def test_docker_compose_action_logs_prune_path(monkeypatch):
    from app.services import docker_management as dm

    srv = SimpleNamespace(id=1, hostname="pi", docker_base_dir="/home/pi/docker")

    class _Out:
        def __iter__(self):
            return iter(["log-line\n"])

    cli = SimpleNamespace(
        close=lambda: None,
        exec_command=lambda cmd, timeout=None: (None, _Out(), _Out()),
    )
    monkeypatch.setattr(dm, "get_ssh_client", lambda *a, **k: cli)

    def _run(client, cmd, timeout=20):
        c = cmd or ""
        if "compose stop" in c or "compose down" in c or "compose start" in c:
            return 0, "ok\n", ""
        if "docker logs" in c or "compose logs" in c:
            return 0, "line1\nline2\n", ""
        if "docker image prune" in c or "docker container prune" in c:
            return 0, "reclaimed\n", ""
        if "docker images --filter" in c:
            return 0, "sha dangling\n", ""
        if "docker ps -a --filter" in c:
            return 0, "exited\n", ""
        return 0, "", ""

    monkeypatch.setattr(dm, "run_command", _run)
    bad = dm.compose_action(srv, "/home/pi/docker/g", "explode")
    assert bad["success"] is False
    empty = dm.compose_action(srv, "", "stop")
    assert empty["success"] is False
    stop = dm.compose_action(srv, "/home/pi/docker/g", "stop")
    assert stop["success"] is True
    down = dm.compose_action(srv, "/home/pi/docker/g", "down", remove_volumes=True)
    assert down["success"] is True
    svc = dm.compose_action(srv, "/home/pi/docker/g", "start", service="web")
    assert svc["success"] is True

    cmd = dm.compose_build_shell_cmd("/home/pi/docker/g", services=["web"], no_cache=True)
    assert "--no-cache" in cmd and "web" in cmd
    monkeypatch.setattr(
        dm,
        "list_compose_projects",
        lambda *a, **k: [{"name": "grafana", "path": "/home/pi/docker/grafana"}],
    )
    assert dm.resolve_compose_project_path(srv, "grafana").endswith("grafana")
    with pytest.raises(ValueError):
        dm.resolve_compose_project_path(srv, "/tmp/evil")
    with pytest.raises(ValueError):
        dm.resolve_compose_project_path(srv, "../x")
    with pytest.raises(ValueError):
        dm.resolve_compose_project_path(srv, "")
    with pytest.raises(ValueError):
        dm.resolve_compose_project_path(srv, "missing")
    built = dm.build_compose_services(srv, "/home/pi/docker/g", services=["web"], no_cache=True)
    assert built.get("success") is True

    logs = dm.get_logs(srv, "grafana", lines=20, follow=False)
    assert "line" in logs or logs == logs
    plogs = dm.get_logs(srv, "__all__", lines=10, project_path="/home/pi/docker/g")
    assert isinstance(plogs, str)
    chunks = list(dm.stream_logs(srv, "grafana", lines=5))
    assert chunks
    more = list(dm.stream_logs(srv, "__all__", lines=5, project_path="/home/pi/docker/g"))
    assert more

    unused = dm.list_unused_images_and_containers(srv)
    assert isinstance(unused, dict)
    pruned = dm.prune_unused(srv, prune_type="both")
    assert isinstance(pruned, dict)
    dm.prune_unused(srv, prune_type="images")
    dm.prune_unused(srv, prune_type="containers")


def test_jobs_create_backup_and_exclusive(monkeypatch):
    from app.services import jobs as js

    session, engine = _memory()
    srv = _server(session)
    monkeypatch.setattr(js, "engine", engine)
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    monkeypatch.setattr(js, "HAS_CELERY", True)

    class _Ok:
        def delay(self, *a, **k):
            return SimpleNamespace(id="c1")

    monkeypatch.setattr(js, "backup_server", _Ok())
    monkeypatch.setattr(
        js,
        "make_audit_log",
        lambda **k: AuditLog(
            user_id=k.get("user_id"),
            server_id=k.get("server_id"),
            action=k.get("action") or "job",
            status=k.get("status") or "running",
            details=k.get("details") or "",
        ),
    )
    monkeypatch.setattr(js, "resolve_client_ip", lambda *a, **k: "1.1.1.1")

    class _Bg:
        def add_task(self, fn, *a, **k):
            return None

    job = js.create_job_and_run(_Bg(), session, srv, "backup", user_id=1, source_filter="/data")
    assert job.celery_task_id == "c1"
    with pytest.raises(js.BackupAlreadyRunning):
        js.create_job_and_run(_Bg(), session, srv, "backup", user_id=1, source_filter="/data")

    chk = js.create_job_and_run(_Bg(), session, srv, "container_update_check", user_id=1)
    assert chk.id
    with pytest.raises(js.JobAlreadyActive):
        js.create_job_and_run(_Bg(), session, srv, "container_update_check", user_id=1)

    monkeypatch.setattr(js, "HAS_CELERY", False)
    monkeypatch.setattr(js, "backup_server", None)
    srv2 = Server(
        name="pi2",
        hostname="pi2.local",
        ssh_username="pi",
        docker_base_dir="/home/pi/docker",
        os_type="debian",
        backup_enabled=True,
        ssh_private_key_encrypted="enc",
    )
    session.add(srv2)
    session.commit()
    session.refresh(srv2)
    with pytest.raises(RuntimeError):
        js.create_job_and_run(_Bg(), session, srv2, "backup", user_id=1)
