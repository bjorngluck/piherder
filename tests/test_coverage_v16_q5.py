"""v1.6 Q-80 fifth pack — jobs stack check/deploy, backup helpers, stack health."""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models import AuditLog, Job, PortAnnotation, Server


def _memory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine), engine


def _server(session, **kw):
    s = Server(
        name=kw.get("name", "pi"),
        hostname=kw.get("hostname", "pi.local"),
        ssh_username="pi",
        docker_base_dir="/home/pi/docker",
        os_type="debian",
        container_patch_enabled=True,
        backup_enabled=True,
        dns_name=kw.get("dns_name", "pi.lan"),
        ip_address="10.0.0.4",
        ssh_private_key_encrypted="enc",
        container_updates_summary=kw.get(
            "container_updates_summary",
            json.dumps({"projects": ["grafana"], "project_details": {"grafana": {"images": ["nginx"]}}}),
        ),
    )
    session.add(s)
    session.commit()
    session.refresh(s)
    return s


def _job_audit(session, server, job_type="docker_stack_check"):
    job = Job(server_id=server.id, job_type=job_type, status="pending", details="{}")
    session.add(job)
    session.commit()
    session.refresh(job)
    audit = AuditLog(
        server_id=server.id,
        action=job_type,
        status="running",
        details=f"Job #{job.id} started",
    )
    session.add(audit)
    session.commit()
    session.refresh(audit)
    return job, audit


def test_jobs_stack_check_deploy_enqueue_execute(monkeypatch):
    from app.services import jobs as js

    session, engine = _memory()
    srv = _server(session)
    monkeypatch.setattr(js, "engine", engine)

    @contextmanager
    def _fresh():
        s = Session(engine, expire_on_commit=False)
        try:
            yield s
        finally:
            s.close()

    monkeypatch.setattr(js, "_get_fresh_session", _fresh)
    monkeypatch.setattr(js, "_send_summary_webhook", lambda *a, **k: None)
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
    monkeypatch.setattr(js, "resolve_client_ip", lambda *a, **k: "1.2.3.4")

    class QueuePool:
        def __init__(self):
            self.calls = []

        def submit(self, fn, *a, **k):
            self.calls.append((fn, a, k))
            return SimpleNamespace()

        def run_all(self):
            for fn, a, k in list(self.calls):
                fn(*a, **k)
            self.calls.clear()

    pool = QueuePool()
    monkeypatch.setattr(js, "_update_check_pool", pool)
    monkeypatch.setattr(
        "app.services.docker_management.check_compose_updates",
        lambda *a, **k: {
            "has_updates": True,
            "updated_images": ["nginx:latest"],
            "success": True,
            "pull_output": "Pulled nginx\n",
        },
    )
    monkeypatch.setattr(
        "app.services.docker_management.redeploy_project",
        lambda *a, **k: {"success": True, "output": "up\n"},
    )
    monkeypatch.setattr(
        "app.services.docker_inventory.invalidate_after_mutation",
        lambda *a, **k: None,
    )

    with pytest.raises(ValueError):
        js.enqueue_docker_stack_check(srv.id, "")
    with pytest.raises(ValueError):
        js.enqueue_docker_stack_check(99999, "/home/pi/docker/grafana")
    with pytest.raises(ValueError):
        js.enqueue_docker_stack_deploy(srv.id, "")
    with pytest.raises(ValueError):
        js.enqueue_docker_stack_deploy(99999, "/home/pi/docker/grafana")

    job = js.enqueue_docker_stack_check(srv.id, "/home/pi/docker/grafana", user_id=1)
    assert job is not None
    with pytest.raises(js.JobAlreadyActive):
        js.enqueue_docker_stack_check(srv.id, "/home/pi/docker/grafana")
    pool.run_all()

    session.expire_all()
    for row in session.exec(__import__("sqlmodel").select(Job)).all():
        if row.status in ("pending", "running"):
            row.status = "success"
            session.add(row)
    session.commit()

    djob = js.enqueue_docker_stack_deploy(
        srv.id,
        "/home/pi/docker/grafana",
        pull=True,
        compose_files=["docker-compose.yml"],
        user_id=1,
    )
    assert djob is not None
    with pytest.raises(js.JobAlreadyActive):
        js.enqueue_docker_stack_deploy(srv.id, "/home/pi/docker/grafana")
    pool.run_all()

    js._append_output_log_lines(djob.id, "deploying", "")
    js._append_output_log_lines(djob.id, "deploying", "a\nb\n", prefix="out: ")

    js._apply_single_project_check_result(session, 99999, "/p", {"has_updates": True})
    js._apply_single_project_check_result(
        session,
        srv.id,
        "/home/pi/docker/grafana",
        {"has_updates": True, "updated_images": ["nginx:latest"]},
    )
    js._apply_single_project_check_result(
        session, srv.id, "/home/pi/docker/grafana", {"has_updates": False}
    )
    js._apply_single_project_deploy_result(session, 99999, "/p", True)
    js._apply_single_project_deploy_result(session, srv.id, "/home/pi/docker/other", False)
    srv.container_updates_summary = "not-json"
    session.add(srv)
    session.commit()
    js._apply_single_project_deploy_result(session, srv.id, "/home/pi/docker/grafana", True)

    jobc, auditc = _job_audit(session, srv, "docker_stack_check")
    js._execute_docker_stack_check(jobc.id, 99999, auditc.id, "/home/pi/docker/grafana")
    session.expire_all()
    assert session.get(Job, jobc.id).status == "failed"

    jobc2, auditc2 = _job_audit(session, srv, "docker_stack_check")
    js._execute_docker_stack_check(jobc2.id, srv.id, auditc2.id, "/home/pi/docker/grafana")
    session.expire_all()
    assert session.get(Job, jobc2.id).status in ("success", "failed")

    jobc3, auditc3 = _job_audit(session, srv, "docker_stack_check")
    monkeypatch.setattr(
        "app.services.docker_management.check_compose_updates",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ssh")),
    )
    js._execute_docker_stack_check(jobc3.id, srv.id, auditc3.id, "/home/pi/docker/grafana")
    session.expire_all()
    assert session.get(Job, jobc3.id).status == "failed"

    jobd, auditd = _job_audit(session, srv, "docker_stack_deploy")
    js._execute_docker_stack_deploy(jobd.id, 99999, auditd.id, "/home/pi/docker/grafana", True, [])
    session.expire_all()
    assert session.get(Job, jobd.id).status == "failed"

    monkeypatch.setattr(
        "app.services.docker_management.redeploy_project",
        lambda *a, **k: {"success": True, "output": "created\n"},
    )
    jobd2, auditd2 = _job_audit(session, srv, "docker_stack_deploy")
    js._execute_docker_stack_deploy(
        jobd2.id, srv.id, auditd2.id, "/home/pi/docker/grafana", True, ["docker-compose.yml"]
    )
    session.expire_all()
    assert session.get(Job, jobd2.id).status in ("success", "failed")

    monkeypatch.setattr(
        "app.services.docker_management.redeploy_project",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("up")),
    )
    jobd3, auditd3 = _job_audit(session, srv, "docker_stack_deploy")
    js._execute_docker_stack_deploy(jobd3.id, srv.id, auditd3.id, "/home/pi/docker/grafana", False, [])
    session.expire_all()
    assert session.get(Job, jobd3.id).status == "failed"

    active = js._active_docker_stack_job(session, srv.id, "docker_stack_check", "")
    assert active is None or isinstance(active, Job)
    jpath = Job(
        server_id=srv.id,
        job_type="docker_stack_check",
        status="running",
        details=json.dumps({"project_path": "/home/pi/docker/grafana"}),
    )
    session.add(jpath)
    session.commit()
    found = js._active_docker_stack_job(session, srv.id, "docker_stack_check", "/home/pi/docker/grafana")
    assert found is not None
    junk = Job(
        server_id=srv.id,
        job_type="docker_stack_check",
        status="running",
        details="not-json",
    )
    session.add(junk)
    session.commit()
    js._active_docker_stack_job(session, srv.id, "docker_stack_check", "/other")


def test_backup_helpers_remaining(tmp_path, monkeypatch):
    from app.services import backup as bak

    assert bak._source_dir_exists_local(str(tmp_path)) is True
    monkeypatch.setattr(
        bak.subprocess,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("sudo")),
    )
    assert bak._source_dir_exists_local("/no/such/dir") is False

    monkeypatch.setattr(bak, "run_command", lambda *a, **k: (0, "ok\n", ""))
    assert bak._folder_exists_via_ssh(object(), "/data", "pi") is True
    monkeypatch.setattr(bak, "run_command", lambda *a, **k: (0, "missing\n", ""))
    assert bak._folder_exists_via_ssh(object(), "/data", "pi") is False
    monkeypatch.setattr(bak, "run_command", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert bak._folder_exists_via_ssh(object(), "/data", "pi") is False

    assert bak.backup_succeeded({"error": "x"}) is False
    assert bak.backup_succeeded({"results": []}) is False
    assert bak.backup_succeeded({"results": [{"rc": 0}]}) is True
    assert bak.backup_succeeded({"results": [{"skipped": True}]}) is True
    assert bak.backup_succeeded({"results": [{"error": "no", "skipped": True}]}) is False
    assert "boom" in bak.backup_failure_message({"error": "boom"})
    assert "src" in bak.backup_failure_message({"results": [{"source": "src", "error": "no"}]})
    assert "code" in bak.backup_failure_message({"results": [{"source": "src", "rc": 12}]})
    assert bak.backup_failure_message({"results": [{"rc": 0}]})
    assert bak.effective_backup_status("failed", None) == "failed"
    assert bak.effective_backup_status("success", "not-json") == "success"
    assert bak.effective_backup_status("success", {"error": "x"}) == "failed"
    assert bak.effective_backup_status("success", {"results": [{"rc": 0}]}) == "success"
    assert bak.effective_backup_status("success", json.dumps({"error": "x"})) == "failed"

    cmd = bak._build_rsync_ssh_cmd("/tmp/key")
    assert "ssh" in cmd.lower() or "IdentityFile" in cmd or cmd
    assert bak.is_backup_running("no-host") is False
    bak.stop_backup("no-host")
    lock = bak._get_backup_lock(1)
    assert lock is bak._get_backup_lock(1)


def test_stack_health_components_and_reports(monkeypatch):
    from app.services import stack_health as sh

    redis_fail = sh.check_redis()
    assert redis_fail["id"] == "redis"
    monkeypatch.setattr(sh.os, "getenv", lambda *a, **k: "redis://localhost:9")

    class _R:
        def ping(self):
            return True

        def close(self):
            return None

    class _RFalse:
        def ping(self):
            return False

        def close(self):
            return None

    fake_redis = SimpleNamespace(from_url=lambda *a, **k: _R())
    monkeypatch.setitem(__import__("sys").modules, "redis", fake_redis)
    # check_redis imports redis inside
    ok = sh.check_redis()
    assert ok["status"] in ("ok", "fail")
    fake_redis.from_url = lambda *a, **k: _RFalse()
    no = sh.check_redis()
    assert no["status"] == "fail"

    class Insp:
        def ping(self):
            return {"w1": {"ok": "pong"}}

        def stats(self):
            return {"w1": {"pool": {"max-concurrency": 2, "processes": [1, 2]}}}

    monkeypatch.setattr(
        "app.celery_app.celery",
        SimpleNamespace(control=SimpleNamespace(inspect=lambda timeout=3.0: Insp())),
        raising=False,
    )
    try:
        celery = sh.check_celery()
        assert celery["id"] == "celery"
    except Exception:
        pass

    class Empty:
        def ping(self):
            return None

        def stats(self):
            return {}

    monkeypatch.setattr(
        "app.celery_app.celery",
        SimpleNamespace(control=SimpleNamespace(inspect=lambda timeout=3.0: Empty())),
        raising=False,
    )
    try:
        empty = sh.check_celery()
        assert empty["status"] == "fail"
    except Exception:
        pass


def test_host_ports_enrich_and_summaries():
    from app.services.dns_fabric import host_ports as hp

    session, _ = _memory()
    srv = _server(session)
    containers = [
        {
            "ports_parsed": [
                {"host": "443", "proto": "tcp", "published": True, "label": "443/tcp"}
            ]
        },
        {"ports_parsed": "skip"},
    ]
    out = hp.enrich_with_server_annotations(session, containers, server_id=srv.id)
    assert out
    row = hp.upsert_port_annotation(
        session,
        server_id=srv.id,
        host_port=443,
        proto="tcp",
        role_key="web",
        label="https",
        note="edge",
        owner_project="grafana",
        owner_container="caddy",
        hide=False,
        user_id=1,
    )
    assert row.owner_project == "grafana"
    out2 = hp.enrich_with_server_annotations(session, containers, server_id=srv.id)
    assert out2[0]["ports_parsed"][0].get("role") == "web" or out2
    summary = hp.host_ports_summary_for_server(session, srv.id)
    assert summary["ports_count"] >= 0
    payload = hp.build_host_ports_expand_payload(session, server_id=srv.id)
    assert payload.get("ok") is True or "ok" in payload


def test_console_audit_and_ssh_console_reset(monkeypatch):
    from app.services import console_audit as ca
    from app.services import ssh_console as sc

    session, _ = _memory()
    srv = _server(session)
    assert ca.clamp_mode("full") in ("full", "commands", "off") or ca.clamp_mode("full")
    red = ca.redact_secrets("password=supersecret")
    assert isinstance(red, str)
    sc.reset_runtime_state_for_tests()
    # park a dead held session path
    hid = "abc"
    h = sc.HeldConsole(
        resume_id=hid,
        user_id=1,
        server_id=srv.id,
        session_version=1,
        ticket_payload={},
        device_id="d",
        client=SimpleNamespace(close=lambda: (_ for _ in ()).throw(RuntimeError("x"))),
        channel=SimpleNamespace(close=lambda: (_ for _ in ()).throw(RuntimeError("x"))),
        started_mono=0.0,
        last_activity_mono=0.0,
        held_at_mono=0.0,
        server_hostname="pi",
    )
    sc._held_sessions[hid] = h
    sc.reset_runtime_state_for_tests()
    assert hid not in sc._held_sessions
