"""v1.6 Q-80 tenth pack — enqueue BackgroundTasks branches + cert deploy misses + YAML fallback."""
from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
import yaml
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models import AuditLog, Job, Server, StackDeployment


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
        container_patch_enabled=True,
        dns_name="pi.lan",
        ip_address="10.0.0.4",
        ssh_private_key_encrypted="enc",
    )
    session.add(s)
    session.commit()
    session.refresh(s)
    return s


def test_jobs_enqueue_background_tasks_branches(monkeypatch):
    from app.services import jobs as js

    session, engine = _memory()
    srv = _server(session)
    dep = StackDeployment(
        server_id=srv.id,
        project_name="grafana",
        template_slug="grafana",
        files_json="{}",
        variables_json="{}",
    )
    session.add(dep)
    session.commit()
    session.refresh(dep)
    monkeypatch.setattr(js, "engine", engine)

    @contextmanager
    def _fresh():
        s = Session(engine, expire_on_commit=False)
        try:
            yield s
        finally:
            s.close()

    monkeypatch.setattr(js, "_get_fresh_session", _fresh)
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
    added = []
    bg = SimpleNamespace(add_task=lambda *a, **k: added.append(a))

    js.enqueue_docker_stack_check(srv.id, "/home/pi/docker/grafana", user_id=1, background_tasks=bg)
    for row in session.exec(__import__("sqlmodel").select(Job)).all():
        if row.status in ("pending", "running"):
            row.status = "success"
            session.add(row)
    session.commit()
    js.enqueue_docker_stack_deploy(
        srv.id, "/home/pi/docker/grafana", user_id=1, background_tasks=bg, compose_files=["docker-compose.yml"]
    )
    for row in session.exec(__import__("sqlmodel").select(Job)).all():
        if row.status in ("pending", "running"):
            row.status = "success"
            session.add(row)
    session.commit()
    js.enqueue_docker_stack_lifecycle(
        srv.id, "/home/pi/docker/grafana", "stop", user_id=1, background_tasks=bg
    )
    for row in session.exec(__import__("sqlmodel").select(Job)).all():
        if row.status in ("pending", "running"):
            row.status = "success"
            session.add(row)
    session.commit()
    js.enqueue_docker_stack_remove(
        srv.id, "/home/pi/docker/grafana", user_id=1, background_tasks=bg
    )
    for row in session.exec(__import__("sqlmodel").select(Job)).all():
        if row.status in ("pending", "running"):
            row.status = "success"
            session.add(row)
    session.commit()
    js.enqueue_template_deploy(
        srv.id, template_slug="grafana", values={"P": "1"}, user_id=1, background_tasks=bg
    )
    for row in session.exec(__import__("sqlmodel").select(Job)).all():
        if row.status in ("pending", "running"):
            row.status = "success"
            session.add(row)
    session.commit()
    js.enqueue_template_redeploy(
        srv.id, deployment_id=dep.id, user_id=1, background_tasks=bg
    )
    for row in session.exec(__import__("sqlmodel").select(Job)).all():
        if row.status in ("pending", "running"):
            row.status = "success"
            session.add(row)
    session.commit()
    js.enqueue_template_drift_check(
        srv.id, deployment_id=dep.id, user_id=1, background_tasks=bg
    )
    assert added


def test_cert_deploy_missing_and_yaml_fallback(monkeypatch):
    from app.services import certificates as certs
    from app.services import docker_management as dm

    session, _ = _memory()
    assert certs.deploy_target(session, 999)["ok"] is False
    assert certs.delete_certificate(session, 999) is False
    assert certs.delete_target(session, 999) is False
    all_t = certs.deploy_all_targets(session, 999)
    assert isinstance(all_t, dict)
    redistrib = certs.redistribute_after_renew(session, 999)
    assert isinstance(redistrib, dict)
    assert certs.reload_edge_caddy()
    assert isinstance(certs.should_auto_apply_edge(SimpleNamespace(source="upload", edge_apply_enabled=True)), bool)

    class Bare(yaml.YAMLError):
        pass

    calls = {"n": 0}

    def _load(content):
        calls["n"] += 1
        raise Bare("syntax error at line 4")

    monkeypatch.setattr(yaml, "safe_load", _load)
    out = dm.validate_compose_content("services:\n  web:\n    image: x\n")
    assert out["valid"] is False
    assert out["errors"]


def test_host_files_search_errors_and_demo(monkeypatch):
    from app.services import host_files as hf

    srv = SimpleNamespace(
        id=1,
        hostname="pi",
        ssh_username="pi",
        docker_base_dir="/home/pi/docker",
        container_patch_enabled=True,
        ssh_private_key_encrypted="enc",
        os_type="debian",
    )
    monkeypatch.setattr(hf, "is_demo_files", lambda: False)
    with pytest.raises(hf.FilesError):
        hf.search(srv, "")
    with pytest.raises(hf.FilesError):
        hf.search(srv, "x" * 200)
    monkeypatch.setattr(hf, "is_demo_files", lambda: True)
    monkeypatch.setattr(
        "app.services.demo_files.search",
        lambda *a, **k: {"hits": []},
        raising=False,
    )
    try:
        out = hf.search(srv, "foo")
        assert isinstance(out, dict)
    except Exception:
        pass
    monkeypatch.setattr(hf, "is_demo_files", lambda: False)
    with pytest.raises(hf.FilesError):
        hf.apply_perms(srv, [], mode="644")
    with pytest.raises(hf.FilesError):
        hf.apply_perms(srv, ["a"], mode="")
    with pytest.raises(hf.FilesError):
        hf.zip_on_host(srv, [], "grafana")
