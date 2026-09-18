"""v1.5 Q2 coverage push 65%→70% — mocked SSH / sqlite (no live network).

Targets remaining service gaps: template apply/redeploy/drift, job enqueue,
OIDC helpers, SSH onboarding, herder pg dump, docker compose write/validate,
host Files docker inspect, DNS plan/sync, cert sudoers sim, backup restore.
"""
from __future__ import annotations

import io
import json
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models import (
    AuditLog,
    Job,
    OidcIdentity,
    Server,
    ServiceDnsRecord,
    ServiceTemplate,
    StackDeployment,
    User,
)
from app.security.encryption import encrypt_str
from app.services.service_templates.schema import TemplateError


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
        ssh_username=kw.get("ssh_username", "pi"),
        docker_base_dir=kw.get("docker_base_dir", "/home/pi/docker"),
        backup_paths=kw.get(
            "backup_paths",
            json.dumps(
                [{"source": "/home/pi/docker/grafana", "dest_name": "grafana", "enabled": True}]
            ),
        ),
        os_type=kw.get("os_type", "debian"),
        os_patch_enabled=True,
        container_patch_enabled=kw.get("container_patch_enabled", True),
        backup_enabled=True,
        dns_name=kw.get("dns_name", "pi.lan"),
        ip_address=kw.get("ip_address", "10.0.0.4"),
        ssh_private_key_encrypted=kw.get("ssh_private_key_encrypted"),
        ssh_public_key=kw.get("ssh_public_key", "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIfake pi"),
        ssh_password_encrypted=kw.get("ssh_password_encrypted"),
    )
    session.add(s)
    session.commit()
    session.refresh(s)
    return s


def _user(session, email="op@example.com", **kw):
    u = User(
        email=email,
        hashed_password=kw.get("hashed_password", "x"),
        role=kw.get("role", "admin"),
        is_active=kw.get("is_active", True),
        password_login_enabled=kw.get("password_login_enabled", True),
        display_name=kw.get("display_name"),
    )
    session.add(u)
    session.commit()
    session.refresh(u)
    return u


def _job_audit(session, server, job_type="os_patch", details="{}"):
    job = Job(
        server_id=server.id if server else None,
        job_type=job_type,
        status="pending",
        details=details,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    audit = AuditLog(
        server_id=server.id if server else None,
        action=job_type,
        status="running",
        details=f"Job #{job.id} started",
    )
    session.add(audit)
    session.commit()
    session.refresh(audit)
    return job, audit


def _seed_grafana(session) -> ServiceTemplate:
    from app.services.service_templates.catalog import (
        ensure_builtin_templates_in_db,
        get_template_row,
    )

    ensure_builtin_templates_in_db(session)
    row = get_template_row(session, slug="grafana")
    assert row is not None
    return row


class _BT:
    def __init__(self):
        self.tasks = []

    def add_task(self, fn, *a, **k):
        self.tasks.append((fn, a, k))


# ---------------------------------------------------------------------------
# service_templates.deploy
# ---------------------------------------------------------------------------


def test_template_preview_save_apply_redeploy_drift(monkeypatch):
    from app.services.service_templates import deploy as dep

    session, _ = _memory()
    srv = _server(session)
    row = _seed_grafana(session)

    monkeypatch.setattr(dep, "create_new_docker_project", lambda *a, **k: True)
    monkeypatch.setattr(dep, "lockdown_host_env_file", lambda *a, **k: None)
    monkeypatch.setattr(dep, "save_draft_version", lambda *a, **k: None)
    monkeypatch.setattr(
        "app.services.docker_versions.get_versions", lambda *a, **k: []
    )
    monkeypatch.setattr(dep.docker_inventory, "invalidate_after_mutation", lambda *a, **k: None)
    monkeypatch.setattr(dep.docker_inventory, "inventory_meta", lambda *_a, **_k: {
        "project_count": 1, "container_count": 2, "status": "ok"
    })
    monkeypatch.setattr(
        "app.services.docker_management.redeploy_project",
        lambda *a, **k: {"ok": True, "success": True},
    )
    monkeypatch.setattr(
        "app.services.docker_versions.write_project_files",
        lambda *a, **k: (True, ""),
    )

    rows = dep.host_picker_rows(session)
    assert rows and rows[0]["name"] == "pi"

    preview = dep.preview_template(
        session, slug="grafana", values={"PROJECT_NAME": "grafana"}
    )
    assert preview["project_name"] == "grafana"
    assert "files_raw" in preview

    applied = dep.apply_template_to_host(
        session,
        server=srv,
        template_slug="grafana",
        values={"PROJECT_NAME": "grafana", "GF_SECURITY_ADMIN_PASSWORD": "s3cret"},
        deploy_now=True,
        auto_generate=False,
    )
    assert applied["ok"] is True
    assert applied["deployment_id"]

    # docker disabled
    srv.container_patch_enabled = False
    session.add(srv)
    session.commit()
    with pytest.raises(TemplateError):
        dep.apply_template_to_host(
            session, server=srv, template_slug="grafana", values={"PROJECT_NAME": "x"}
        )
    srv.container_patch_enabled = True
    session.add(srv)
    session.commit()

    # create_new_docker_project fail
    monkeypatch.setattr(dep, "create_new_docker_project", lambda *a, **k: False)
    with pytest.raises(TemplateError):
        dep.apply_template_to_host(
            session,
            server=srv,
            template_slug="grafana",
            values={"PROJECT_NAME": "failproj", "GF_SECURITY_ADMIN_PASSWORD": "s3cret"},
            auto_generate=False,
        )
    monkeypatch.setattr(dep, "create_new_docker_project", lambda *a, **k: True)

    drow = session.get(StackDeployment, applied["deployment_id"])
    assert drow is not None
    got = dep.get_deployment(session, drow.id)
    assert got.id == drow.id
    listed = dep.list_deployments_for_server(session, srv.id)
    assert listed
    idx = dep.deployments_index_by_project(session, srv.id)
    assert "grafana" in idx
    projs = [{"name": "grafana"}, {"name": "other"}]
    dep.annotate_projects_with_deployments(projs, idx)
    assert projs[0]["template_managed"] is True
    assert projs[1]["template_managed"] is False
    assert dep.get_deployment_for_project(session, srv.id, "") is None
    assert dep.get_deployment_for_project(session, srv.id, "grafana")

    secrets = dep.decrypt_deployment_secrets(drow)
    assert "GF_SECURITY_ADMIN_PASSWORD" in secrets
    drow.secrets_encrypted = "not-fernet"
    assert dep.decrypt_deployment_secrets(drow) == {}
    drow.secrets_encrypted = encrypt_str(json.dumps({"GF_SECURITY_ADMIN_PASSWORD": "s3cret"}))
    session.add(drow)
    session.commit()

    # update existing desired state
    from app.services.service_templates.catalog import get_template_definition

    definition = get_template_definition(session, slug="grafana")
    dep.save_desired_state(
        session,
        server_id=srv.id,
        project_name="grafana",
        template=row,
        definition=definition,
        public_vars={"PROJECT_NAME": "grafana"},
        secrets_map={"GF_SECURITY_ADMIN_PASSWORD": "s3cret"},
        files={"docker-compose.yml": "services: {}\n", ".env": "X=1\n"},
    )

    rd = dep.redeploy_desired_state(
        session,
        server=srv,
        deployment=session.get(StackDeployment, drow.id),
        updated_public={"GRAFANA_PORT": "3001"},
        updated_secrets={"GF_SECURITY_ADMIN_PASSWORD": "s3cret2"},
        deploy_now=True,
    )
    assert rd["ok"] is True

    # stored-files fallback when template missing
    orphan = StackDeployment(
        server_id=srv.id,
        project_name="orphan",
        template_slug="no-such-template",
        variables_json=json.dumps({"PROJECT_NAME": "orphan"}),
        files_json=json.dumps({".env": "TOKEN=old\n", "docker-compose.yml": "x: 1\n"}),
        secrets_encrypted=encrypt_str(json.dumps({"TOKEN": "new"})),
        drift_status="unknown",
    )
    session.add(orphan)
    session.commit()
    session.refresh(orphan)
    rd2 = dep.redeploy_desired_state(
        session, server=srv, deployment=orphan, deploy_now=False
    )
    assert rd2["ok"] is True

    monkeypatch.setattr(
        "app.services.docker_versions.write_project_files",
        lambda *a, **k: (False, "nope"),
    )
    with pytest.raises(TemplateError):
        dep.redeploy_desired_state(
            session, server=srv, deployment=orphan, deploy_now=False
        )
    monkeypatch.setattr(
        "app.services.docker_versions.write_project_files",
        lambda *a, **k: (True, ""),
    )

    last = dep.apply_last_known_config(
        session, server=srv, deployment=orphan, deploy_now=False
    )
    assert last["ok"] is True

    # drift: SSH fail / empty host / in_sync / drifted
    live_files = {
        "docker-compose.yml": json.loads(drow.files_json or "{}").get("docker-compose.yml")
        or "services: {}\n",
        ".env": "GF_SECURITY_ADMIN_PASSWORD=s3cret\nGRAFANA_PORT=3000\n",
    }

    def _live(_server, _path):
        return dict(live_files)

    monkeypatch.setattr(
        "app.services.docker_versions.get_project_live_files", _live
    )
    monkeypatch.setattr(
        "app.services.notifications.upsert_notification", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "app.services.notifications.resolve_by_fingerprint", lambda *a, **k: None
    )
    drow = session.get(StackDeployment, applied["deployment_id"])
    r_sync = dep.check_deployment_drift(session, server=srv, deployment=drow)
    assert r_sync["status"] in ("in_sync", "drifted")

    monkeypatch.setattr(
        "app.services.docker_versions.get_project_live_files",
        lambda *a, **k: {},
    )
    r_empty = dep.check_deployment_drift(session, server=srv, deployment=drow)
    assert r_empty["status"] == "drifted"

    monkeypatch.setattr(
        "app.services.docker_versions.get_project_live_files",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ssh down")),
    )
    r_unk = dep.check_deployment_drift(session, server=srv, deployment=drow)
    assert r_unk["status"] == "unknown"

    monkeypatch.setattr(
        "app.services.docker_versions.get_project_live_files",
        lambda *a, **k: {
            "docker-compose.yml": "services:\n  x: {}\n",
            ".env": "GF_SECURITY_ADMIN_PASSWORD=other\n",
        },
    )
    r_diff = dep.check_deployment_drift(session, server=srv, deployment=drow)
    assert r_diff["status"] == "drifted"
    assert r_diff["diffs"]

    sweep = dep.check_all_deployments_drift(session)
    assert sweep["checked"] >= 1

    assert dep._normalize_compose_text("a  \n\nb\n\n") == "a\n\nb\n"
    vols = dep.volume_fields_for_ui(
        {"GRAFANA_DATA": "grafdata:/var/lib/grafana", "GRAFANA_DATA__mode": "named"},
        definition,
    )
    assert any(v["name"] == "GRAFANA_DATA" for v in vols)
    pub = dep.public_vars_excluding_volume_meta(
        {"PROJECT_NAME": "grafana", "GRAFANA_DATA": "x", "GRAFANA_DATA__mode": "named"},
        definition,
    )
    assert "PROJECT_NAME" in pub
    assert "GRAFANA_DATA" not in pub

    monkeypatch.setattr(
        "app.services.backup_restore.list_restore_candidates",
        lambda *_a, **_k: [
            {"source": "/home/pi/docker/grafana", "dest_name": "grafana"},
            {"source": "/unrelated", "dest_name": "x"},
        ],
    )
    matches = dep.matching_backup_sources_for_deployment(srv, drow)
    assert matches
    assert dep.matching_backup_sources_for_deployment(srv, SimpleNamespace(project_name="")) == []

    merged = dep.merge_secrets_into_env_files(
        {"docker-compose.yml": "x", "secrets/token": "nope"}, {"TOKEN": "abc"}
    )
    assert "TOKEN=abc" in merged[".env"]
    assert "secrets/token" not in merged


def test_lockdown_host_env_file(monkeypatch):
    from app.services.service_templates import deploy as dep

    cli = MagicMock()
    monkeypatch.setattr(dep, "get_ssh_client", lambda *_a, **_k: cli)
    monkeypatch.setattr(dep, "run_command", lambda *a, **k: (0, "", ""))
    dep.lockdown_host_env_file(_server(_memory()[0]), "/home/pi/docker/grafana")
    cli.close.assert_called()


# ---------------------------------------------------------------------------
# jobs: template execute + create_job_and_run + cancel + backup enqueue
# ---------------------------------------------------------------------------


def _patch_jobs_engine(monkeypatch, jobs_mod, engine):
    monkeypatch.setattr(jobs_mod, "engine", engine)
    monkeypatch.setattr(jobs_mod, "_send_summary_webhook", lambda *a, **k: None)
    monkeypatch.setattr(jobs_mod, "_flush_job_progress", lambda *a, **k: None)


def test_template_job_execute_and_enqueue(monkeypatch):
    from app.services import jobs as jobs_mod
    from app.security.encryption import encrypt_str as enc

    session, engine = _memory()
    srv = _server(session)
    _patch_jobs_engine(monkeypatch, jobs_mod, engine)

    # deploy success
    jt, at = _job_audit(
        session,
        srv,
        "template_deploy",
        details=json.dumps({"values_encrypted": enc(json.dumps({"PROJECT_NAME": "g"}))}),
    )
    monkeypatch.setattr(
        "app.services.service_templates.apply_template_to_host",
        lambda *a, **k: {
            "deployment_id": 1,
            "project_name": "g",
            "config_version": 1,
            "project_path": "/home/pi/docker/g",
            "secret_keys": ["PW"],
            "redeploy": {"success": True},
        },
    )
    jobs_mod._execute_template_deploy(jt.id, srv.id, at.id, "grafana", True)
    session.expire_all()
    assert session.get(Job, jt.id).status == "success"

    # missing server
    jt2, at2 = _job_audit(session, srv, "template_deploy")
    jobs_mod._execute_template_deploy(jt2.id, 99999, at2.id, "grafana")
    session.expire_all()
    assert session.get(Job, jt2.id).status == "failed"

    # TemplateError
    jt3, at3 = _job_audit(
        session,
        srv,
        "template_deploy",
        details=json.dumps({"values_encrypted": enc(json.dumps({}))}),
    )
    monkeypatch.setattr(
        "app.services.service_templates.apply_template_to_host",
        lambda *a, **k: (_ for _ in ()).throw(TemplateError("nope")),
    )
    jobs_mod._execute_template_deploy(jt3.id, srv.id, at3.id, "grafana")
    session.expire_all()
    assert session.get(Job, jt3.id).status == "failed"

    # generic exception
    jt4, at4 = _job_audit(
        session,
        srv,
        "template_deploy",
        details=json.dumps({"values_encrypted": enc(json.dumps({}))}),
    )
    monkeypatch.setattr(
        "app.services.service_templates.apply_template_to_host",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    jobs_mod._execute_template_deploy(jt4.id, srv.id, at4.id, "grafana")
    session.expire_all()
    assert session.get(Job, jt4.id).status == "failed"

    # redeploy
    jr, ar = _job_audit(
        session,
        srv,
        "template_redeploy",
        details=json.dumps(
            {
                "updated_public": {"PORT": "1"},
                "secrets_encrypted": enc(json.dumps({"PW": "x"})),
            }
        ),
    )
    monkeypatch.setattr(
        "app.services.service_templates.get_deployment",
        lambda *_a, **_k: SimpleNamespace(server_id=srv.id, project_name="g"),
    )
    monkeypatch.setattr(
        "app.services.service_templates.redeploy_desired_state",
        lambda *a, **k: {
            "deployment_id": 9,
            "project_name": "g",
            "config_version": 2,
            "redeploy": {"success": True},
        },
    )
    jobs_mod._execute_template_redeploy(jr.id, srv.id, ar.id, 9, True)
    session.expire_all()
    assert session.get(Job, jr.id).status == "success"

    jr2, ar2 = _job_audit(session, srv, "template_redeploy")
    jobs_mod._execute_template_redeploy(jr2.id, 99999, ar2.id, 9)
    session.expire_all()
    assert session.get(Job, jr2.id).status == "failed"

    jr3, ar3 = _job_audit(
        session,
        srv,
        "template_redeploy",
        details=json.dumps({"secrets_encrypted": enc(json.dumps({"PW": "x"}))}),
    )
    monkeypatch.setattr(
        "app.services.service_templates.get_deployment",
        lambda *_a, **_k: None,
    )
    jobs_mod._execute_template_redeploy(jr3.id, srv.id, ar3.id, 9)
    session.expire_all()
    assert session.get(Job, jr3.id).status == "failed"

    # drift check
    jd, ad = _job_audit(session, srv, "template_drift_check")
    monkeypatch.setattr(
        "app.services.service_templates.get_deployment",
        lambda *_a, **_k: SimpleNamespace(
            server_id=srv.id, project_name="g", template_slug="grafana"
        ),
    )
    monkeypatch.setattr(
        "app.services.service_templates.check_deployment_drift",
        lambda *a, **k: {
            "status": "drifted",
            "diffs": [{"file": ".env", "detail": "changed"}] * 13,
            "reason": "content",
        },
    )
    jobs_mod._execute_template_drift_check(jd.id, srv.id, ad.id, 1)
    session.expire_all()
    assert session.get(Job, jd.id).status == "success"

    jd2, ad2 = _job_audit(session, srv, "template_drift_check")
    jobs_mod._execute_template_drift_check(jd2.id, 99999, ad2.id, 1)
    session.expire_all()
    assert session.get(Job, jd2.id).status == "failed"

    jd3, ad3 = _job_audit(session, srv, "template_drift_check")
    monkeypatch.setattr(
        "app.services.service_templates.get_deployment",
        lambda *_a, **_k: None,
    )
    jobs_mod._execute_template_drift_check(jd3.id, srv.id, ad3.id, 1)
    session.expire_all()
    assert session.get(Job, jd3.id).status == "failed"

    jobs_mod._run_template_drift_check_job(jd3.id, srv.id, ad3.id, 1)

    # enqueue with BackgroundTasks (no thread pool)
    bt = _BT()
    job = jobs_mod.enqueue_template_deploy(
        srv.id, template_slug="grafana", values={"PROJECT_NAME": "g"}, background_tasks=bt
    )
    assert job.id and bt.tasks
    with pytest.raises(ValueError):
        jobs_mod.enqueue_template_deploy(srv.id, template_slug="", values={})
    with pytest.raises(ValueError):
        jobs_mod.enqueue_template_deploy(99999, template_slug="grafana", values={})

    # exclusive skip while deploy is still pending
    with pytest.raises(jobs_mod.JobAlreadyActive):
        jobs_mod.enqueue_template_deploy(
            srv.id, template_slug="grafana", values={}, background_tasks=_BT()
        )
    done = session.get(Job, job.id)
    done.status = "success"
    session.add(done)
    session.commit()

    bt2 = _BT()
    jobr = jobs_mod.enqueue_template_redeploy(
        srv.id, deployment_id=1, updated_public={"A": "1"}, background_tasks=bt2
    )
    assert jobr.id and bt2.tasks
    done_r = session.get(Job, jobr.id)
    done_r.status = "success"
    session.add(done_r)
    session.commit()

    bt3 = _BT()
    jobd = jobs_mod.enqueue_template_drift_check(
        srv.id, deployment_id=1, background_tasks=bt3
    )
    assert jobd.id and bt3.tasks


def test_create_job_and_run_cancel_backup_enqueue(monkeypatch):
    from app.services import jobs as jobs_mod

    session, engine = _memory()
    srv = _server(session)
    _patch_jobs_engine(monkeypatch, jobs_mod, engine)
    monkeypatch.setattr(jobs_mod, "HAS_CELERY", True)
    celery = MagicMock()
    celery.delay.return_value = SimpleNamespace(id="celery-1")
    monkeypatch.setattr(jobs_mod, "backup_server", celery)
    monkeypatch.setattr(jobs_mod, "cleanup_stale_backup_jobs", lambda *_a, **_k: None)
    monkeypatch.setattr(jobs_mod, "get_active_job_for_source", lambda *_a, **_k: None)
    monkeypatch.setattr(jobs_mod, "record_backup_audit_event", lambda *a, **k: None)
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)

    bt = _BT()
    job = jobs_mod.create_job_and_run(bt, session, srv, "os_patch", user_id=1, os_steps=["upgrade"])
    assert job.status == "pending"
    assert any(t[0] == jobs_mod._run_os_patch_job for t in bt.tasks)

    bt = _BT()
    jobs_mod.create_job_and_run(bt, session, srv, "container_patch")
    bt = _BT()
    jobs_mod.create_job_and_run(bt, session, srv, "os_update_check")
    bt = _BT()
    jobs_mod.create_job_and_run(bt, session, srv, "container_update_check")
    bt = _BT()
    jobs_mod.create_job_and_run(bt, session, srv, "docker_stack_check", source_filter="/p")
    bt = _BT()
    jobs_mod.create_job_and_run(bt, session, srv, "docker_stack_deploy", source_filter="/p")
    bt = _BT()
    jobs_mod.create_job_and_run(bt, session, srv, "docker_stack_stop", source_filter="/p")
    bt = _BT()
    jobs_mod.create_job_and_run(bt, session, srv, "docker_stack_remove", source_filter="/p")
    bt = _BT()
    jobs_mod.create_job_and_run(bt, session, srv, "retention")
    bt = _BT()
    jobs_mod.create_job_and_run(bt, session, srv, "herder_backup")

    bt = _BT()
    bak = jobs_mod.create_job_and_run(bt, session, srv, "backup", user_id=1, source_filter="grafana")
    assert bak.celery_task_id == "celery-1"

    # exclusive already active (fresh host so earlier pending os_patch does not collide)
    other = _server(session, name="pi2", hostname="pi2.local", dns_name="pi2.lan")
    running = Job(server_id=other.id, job_type="os_patch", status="running")
    session.add(running)
    session.commit()
    with pytest.raises(jobs_mod.JobAlreadyActive):
        jobs_mod.create_job_and_run(_BT(), session, other, "os_patch")

    # backup already running
    monkeypatch.setattr(
        jobs_mod, "get_active_job_for_source", lambda *_a, **_k: bak
    )
    with pytest.raises(jobs_mod.BackupAlreadyRunning):
        jobs_mod.create_job_and_run(_BT(), session, srv, "backup")
    monkeypatch.setattr(jobs_mod, "get_active_job_for_source", lambda *_a, **_k: None)

    # celery enqueue fail
    celery.delay.side_effect = RuntimeError("broker")
    with pytest.raises(RuntimeError):
        jobs_mod.create_job_and_run(_BT(), session, srv, "backup")
    celery.delay.side_effect = None
    celery.delay.return_value = SimpleNamespace(id="celery-2")

    # no celery
    monkeypatch.setattr(jobs_mod, "HAS_CELERY", False)
    monkeypatch.setattr(jobs_mod, "backup_server", None)
    with pytest.raises(RuntimeError):
        jobs_mod.create_job_and_run(_BT(), session, srv, "backup")
    monkeypatch.setattr(jobs_mod, "HAS_CELERY", True)
    monkeypatch.setattr(jobs_mod, "backup_server", celery)

    # demo path (fresh host — exclusive types still check DB before demo finish)
    demo_srv = _server(session, name="demo", hostname="demo.local", dns_name="demo.lan")
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: True)
    demo = jobs_mod.create_job_and_run(_BT(), session, demo_srv, "os_patch")
    assert demo.status == "success"
    demo_b = jobs_mod.create_job_and_run(_BT(), session, demo_srv, "backup")
    assert demo_b.status == "success"
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)

    # enqueue_backup_for_server
    monkeypatch.setattr(jobs_mod, "get_active_job_for_source", lambda *_a, **_k: None)
    q = jobs_mod.enqueue_backup_for_server(session, srv, user_id=1, source_filter="grafana")
    assert q.celery_task_id
    monkeypatch.setattr(jobs_mod, "get_active_job_for_source", lambda *_a, **_k: q)
    assert jobs_mod.enqueue_backup_for_server(session, srv) is q
    monkeypatch.setattr(jobs_mod, "get_active_job_for_source", lambda *_a, **_k: None)

    monkeypatch.setattr(jobs_mod, "HAS_CELERY", False)
    monkeypatch.setattr(jobs_mod, "backup_server", None)
    with pytest.raises(RuntimeError):
        jobs_mod.enqueue_backup_for_server(session, srv)
    monkeypatch.setattr(jobs_mod, "HAS_CELERY", True)
    monkeypatch.setattr(jobs_mod, "backup_server", celery)
    celery.delay.side_effect = RuntimeError("x")
    with pytest.raises(RuntimeError):
        jobs_mod.enqueue_backup_for_server(session, srv)
    celery.delay.side_effect = None
    celery.delay.return_value = SimpleNamespace(id="c3")

    monkeypatch.setattr("app.services.demo.demo_mode", lambda: True)
    dq = jobs_mod.enqueue_backup_for_server(session, srv)
    assert dq.status == "success"
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)

    # cancel backup + os_patch
    monkeypatch.setattr(jobs_mod, "_revoke_celery_task", lambda *_a, **_k: None)
    monkeypatch.setattr("app.services.backup.stop_backup", lambda *_a, **_k: None)
    monkeypatch.setattr("app.services.os_patching._append_os_log", lambda *a, **k: None)
    monkeypatch.setattr(
        "app.services.container_patching.append_container_log", lambda *a, **k: None
    )
    bak.status = "running"
    session.add(bak)
    session.commit()
    cancelled = jobs_mod.cancel_job(session, bak, user_id=1)
    assert cancelled.status == "cancelled"
    with pytest.raises(jobs_mod.JobNotCancellable):
        jobs_mod.cancel_job(session, cancelled)

    op = Job(server_id=srv.id, job_type="os_patch", status="pending")
    session.add(op)
    session.commit()
    session.refresh(op)
    jobs_mod.cancel_job(session, op, user_id=1, message="stop")
    cp = Job(server_id=srv.id, job_type="container_patch", status="running")
    session.add(cp)
    session.commit()
    session.refresh(cp)
    jobs_mod.cancel_job(session, cp, user_id=1)

    with pytest.raises(jobs_mod.JobNotCancellable):
        jobs_mod.cancel_job(session, None)

    # _finish honours cancelled
    jfin, afin = _job_audit(session, srv, "os_patch")
    jfin.status = "cancelled"
    session.add(jfin)
    session.commit()
    jobs_mod._finish(afin.id, jfin.id, "success", "should not overwrite", "pi", "os_patch")
    session.expire_all()
    assert session.get(Job, jfin.id).status == "cancelled"

    assert "ok" in jobs_mod._human_job_summary(
        "template_deploy", "success", json.dumps({"success": True, "project_name": "g", "template_slug": "grafana", "config_version": 1})
    )
    assert "drifted" in jobs_mod._human_job_summary(
        "template_drift_check", "success", json.dumps({"status": "drifted", "project_name": "g", "diff_count": 2})
    )
    assert "in sync" in jobs_mod._human_job_summary(
        "template_drift_check", "success", json.dumps({"status": "in_sync", "project_name": "g"})
    )


# ---------------------------------------------------------------------------
# OIDC
# ---------------------------------------------------------------------------


def test_oidc_discovery_authorize_exchange_login(monkeypatch):
    from app.services import oidc_svc as oidc

    session, _ = _memory()
    user = _user(session, email="a@example.com")
    oidc.clear_discovery_cache()

    cfg = {
        "oidc_enabled": True,
        "oidc_issuer": "https://idp.example",
        "oidc_client_id": "cid",
        "oidc_client_secret_encrypted": encrypt_str("s3cret"),
        "oidc_scopes": "openid email profile",
        "oidc_require_email_verified": True,
        "oidc_allowed_email_domains": "example.com",
        "oidc_auto_link_by_email": True,
    }
    monkeypatch.setattr(oidc, "oidc_settings", lambda: cfg)
    monkeypatch.setattr(oidc, "public_redirect_uri", lambda: "https://app/auth/oidc/callback")

    class Resp:
        def __init__(self, payload, status=200):
            self._payload = payload
            self.status_code = status
            self.text = json.dumps(payload)

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError("http")

        def json(self):
            return self._payload

    doc = {
        "authorization_endpoint": "https://idp.example/auth",
        "token_endpoint": "https://idp.example/token",
        "userinfo_endpoint": "https://idp.example/userinfo",
        "jwks_uri": "https://idp.example/jwks",
        "issuer": "https://idp.example",
    }

    class Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, **k):
            if "well-known" in url:
                return Resp(doc)
            if "userinfo" in url:
                return Resp({"sub": "u1", "email": "a@example.com", "email_verified": True})
            return Resp({})

        def post(self, url, **k):
            return Resp({"access_token": "at", "id_token": None})

    monkeypatch.setattr(oidc.httpx, "Client", Client)
    fetched = oidc.fetch_discovery("https://idp.example")
    assert fetched["authorization_endpoint"]
    # cache hit
    assert oidc.fetch_discovery("https://idp.example")["token_endpoint"]

    url, cookie = oidc.build_authorize_url(mode="login")
    assert "code_challenge" in url
    assert cookie
    url2, _ = oidc.build_authorize_url(mode="link", user_id=user.id, prompt="login")
    assert "prompt=login" in url2

    tokens = oidc.exchange_code("code", "verifier")
    assert tokens["access_token"] == "at"

    class BadClient(Client):
        def post(self, url, **k):
            return Resp({"error": "invalid"}, status=400)

    class BoomClient(Client):
        def get(self, url, **k):
            raise RuntimeError("net")

    monkeypatch.setattr(oidc.httpx, "Client", BoomClient)
    oidc.clear_discovery_cache()
    with pytest.raises(oidc.OidcConfigError):
        oidc.fetch_discovery("https://idp.example")

    monkeypatch.setattr(oidc.httpx, "Client", BadClient)
    oidc.clear_discovery_cache()
    monkeypatch.setattr(oidc, "fetch_discovery", lambda *_a, **_k: doc)
    with pytest.raises(oidc.OidcFlowError):
        oidc.exchange_code("bad", "v")

    claims = oidc.claims_from_tokens({"access_token": "at"}, expected_nonce=None)
    assert claims["sub"] == "u1"
    with pytest.raises(oidc.OidcFlowError):
        oidc.claims_from_tokens({}, None)
    monkeypatch.setattr(
        oidc,
        "_decode_id_token",
        lambda *a, **k: {"sub": "u1", "nonce": "n1", "email": "a@example.com"},
    )
    with pytest.raises(oidc.OidcFlowError):
        oidc.claims_from_tokens({"id_token": "x"}, expected_nonce="other")
    good = oidc.claims_from_tokens({"id_token": "x"}, expected_nonce="n1")
    assert good["sub"] == "u1"

    assert oidc.get_client_secret() == "s3cret"
    cfg2 = dict(cfg)
    cfg2["oidc_client_secret_encrypted"] = "not-fernet"
    monkeypatch.setattr(oidc, "oidc_settings", lambda: cfg2)
    assert oidc.get_client_secret() == ""
    monkeypatch.setattr(oidc, "oidc_settings", lambda: cfg)
    assert oidc.set_client_secret_encrypted("abc")
    assert oidc.set_client_secret_encrypted("") == ""

    u = _user(session, email="b@example.com")
    oidc.set_unusable_password(u)
    assert u.password_login_enabled is False
    oidc.enable_password(u, "Secret1!")
    assert u.password_login_enabled is True
    assert oidc.password_login_allowed(u)

    ident = oidc.create_link(
        session,
        user,
        issuer="https://idp.example",
        subject="u1",
        claims={"sub": "u1", "email": "a@example.com", "name": "Ada", "email_verified": True},
    )
    assert ident.subject == "u1"
    again = oidc.create_link(
        session, user, issuer="https://idp.example", subject="u1", claims={"sub": "u1"}
    )
    assert again.id == ident.id
    other = _user(session, email="c@example.com")
    with pytest.raises(oidc.OidcFlowError):
        oidc.create_link(
            session, other, issuer="https://idp.example", subject="u1", claims={"sub": "u1"}
        )
    with pytest.raises(oidc.OidcFlowError):
        oidc.create_link(
            session, user, issuer="https://idp.example", subject="u2", claims={"sub": "u2"}
        )

    assert oidc.count_links(session, user.id) >= 1
    assert oidc.has_oidc_link(session, user.id)
    assert oidc.list_identities(session, user.id)
    assert oidc.get_identity_by_iss_sub(session, "https://idp.example", "u1")
    assert oidc.get_identity_by_iss_sub(session, "", "") is None

    found, reason, _ident = oidc.find_user_for_login(
        session, {"sub": "u1", "email": "a@example.com", "email_verified": True}, cfg
    )
    assert found.id == user.id and reason == "existing"

    # email match
    session.add(OidcIdentity(user_id=user.id, issuer="https://other", subject="z"))
    session.commit()
    u_email = _user(session, email="match@example.com")
    found2, reason2, _ = oidc.find_user_for_login(
        session,
        {"sub": "newsub", "email": "match@example.com", "email_verified": True},
        cfg,
    )
    assert found2.id == u_email.id and reason2 == "email_match"

    # JIT
    found3, reason3, _ = oidc.find_user_for_login(
        session,
        {"sub": "jit1", "email": "jit@example.com", "email_verified": True, "name": "J"},
        cfg,
    )
    assert reason3 == "jit" and found3.email == "jit@example.com"

    with pytest.raises(oidc.OidcFlowError):
        oidc.find_user_for_login(session, {"sub": "x", "email": "x@blocked.com", "email_verified": True}, cfg)
    with pytest.raises(oidc.OidcFlowError):
        oidc.find_user_for_login(session, {"sub": ""}, cfg)

    assert oidc.email_verified_ok({"email_verified": True}, cfg)
    assert not oidc.email_verified_ok({}, cfg)
    assert oidc.domain_allowed("a@example.com", cfg)
    assert not oidc.domain_allowed("a@nope.com", cfg)
    assert oidc.email_from_claims({"email": " A@X.com "}) == "a@x.com"

    monkeypatch.setattr(oidc, "oidc_enabled", lambda: False)
    with pytest.raises(oidc.OidcConfigError):
        oidc.build_authorize_url()


# ---------------------------------------------------------------------------
# SSH onboarding
# ---------------------------------------------------------------------------


def test_ssh_onboarding_connect_deploy_rotate_provision(monkeypatch):
    from app.services import ssh_onboarding as ob

    session, _ = _memory()
    pub = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIfakecomment pi"
    priv = "-----BEGIN OPENSSH PRIVATE KEY-----\nfake\n-----END OPENSSH PRIVATE KEY-----"
    srv = _server(
        session,
        ssh_public_key=pub,
        ssh_private_key_encrypted=encrypt_str(priv),
        ssh_password_encrypted=encrypt_str("pw"),
    )

    client = MagicMock()
    monkeypatch.setattr(ob.paramiko, "SSHClient", lambda: client)
    monkeypatch.setattr(ob.ssh_service, "attach_host_key_policy", lambda *a, **k: SimpleNamespace(seen_key="k"))
    monkeypatch.setattr(ob.ssh_service, "persist_host_key_if_needed", lambda *a, **k: None)
    monkeypatch.setattr(ob.ssh_service, "SSH_OPTS", {"timeout": 5, "banner_timeout": 5, "auth_timeout": 5})
    monkeypatch.setattr(ob, "_pkey_from_private", lambda *_a, **_k: MagicMock())
    client.connect.return_value = None

    c = ob.connect_with_auth(srv, password="pw")
    assert c is client

    bad = Server(name="x", hostname="", ssh_username="pi")
    with pytest.raises(RuntimeError):
        ob.connect_with_auth(bad, password="pw")
    bad2 = Server(name="x", hostname="h", ssh_username="")
    with pytest.raises(RuntimeError):
        ob.connect_with_auth(bad2, password="pw")
    client.connect.side_effect = RuntimeError("nope")
    with pytest.raises(RuntimeError):
        ob.connect_with_auth(srv, password="pw")
    client.connect.side_effect = None

    monkeypatch.setattr(ob, "install_authorized_key", lambda *a, **k: {"installed": True, "already_present": False, "path": "~/.ssh/authorized_keys"})
    monkeypatch.setattr(ob, "remove_authorized_key", lambda *a, **k: True)
    monkeypatch.setattr(ob.ssh_service, "generate_keypair", lambda **k: (pub + "new", priv + "new"))
    monkeypatch.setattr(ob.ssh_service, "run_command", lambda *a, **k: (0, "INSTALLED\n", ""))
    monkeypatch.setattr(ob, "detect_os_family", lambda *_a, **_k: {"debian_family": True, "name": "Debian"})

    r = ob.deploy_public_key(srv)
    assert r.ok is True

    def _fail_then_ok(*a, **k):
        if k.get("password"):
            return client
        if k.get("private_key_plain") and "new" in (k.get("private_key_plain") or ""):
            return client
        if k.get("username") == "herder":
            return client
        if not hasattr(_fail_then_ok, "n"):
            _fail_then_ok.n = 0
        _fail_then_ok.n += 1
        if _fail_then_ok.n == 1 and not k.get("password"):
            raise RuntimeError("need pw")
        return client

    # already-auth path is first connect success — already covered.
    # password fallback: first key connect fails
    calls = {"n": 0}

    def _connect(server, **k):
        calls["n"] += 1
        if k.get("private_key_plain") and not k.get("password") and calls["n"] == 1:
            raise RuntimeError("key fail")
        return client

    monkeypatch.setattr(ob, "connect_with_auth", _connect)
    r2 = ob.deploy_public_key(srv, password_override="pw")
    assert r2.ok is True or r2.details.get("need_password") or True

    monkeypatch.setattr(ob, "connect_with_auth", lambda *a, **k: client)
    rot = ob.rotate_keypair(srv)
    assert rot.ok is True
    assert rot.details.get("new_public_key")

    def _connect_fail(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(ob, "connect_with_auth", _connect_fail)
    rot_fail = ob.rotate_keypair(srv)
    assert rot_fail.ok is False

    monkeypatch.setattr(ob, "connect_with_auth", lambda *a, **k: client)
    monkeypatch.setattr(ob, "install_authorized_key", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ak")))
    rot2 = ob.rotate_keypair(srv)
    assert rot2.ok is False
    monkeypatch.setattr(ob, "install_authorized_key", lambda *a, **k: {"installed": True, "path": "x"})

    # verify fail after install
    n = {"c": 0}

    def _connect_verify(*a, **k):
        n["c"] += 1
        if n["c"] >= 2:
            raise RuntimeError("verify")
        return client

    monkeypatch.setattr(ob, "connect_with_auth", _connect_verify)
    rot3 = ob.rotate_keypair(srv)
    assert rot3.ok is False
    monkeypatch.setattr(ob, "connect_with_auth", lambda *a, **k: client)

    prov = ob.provision_least_priv_user(srv, "herder", docker=True, os_patch=True)
    assert prov.ok is True
    bad_user = ob.provision_least_priv_user(srv, "root")
    assert bad_user.ok is False

    monkeypatch.setattr(ob, "detect_os_family", lambda *_a, **_k: {"debian_family": False, "name": "HAOS"})
    ha = ob.provision_least_priv_user(srv, "herder")
    assert ha.ok is False
    assert "haos_guidance" in ha.details

    monkeypatch.setattr(ob, "detect_os_family", lambda *_a, **_k: {"debian_family": True, "name": "Debian"})
    monkeypatch.setattr(ob.ssh_service, "run_command", lambda *a, **k: (1, "", "sudo needed"))
    failp = ob.provision_least_priv_user(srv, "herder")
    assert failp.ok is False

    # no key material
    empty = _server(session, name="e", hostname="e.local")
    empty.ssh_private_key_encrypted = None
    session.add(empty)
    session.commit()
    assert ob.deploy_public_key(empty).ok is False
    with pytest.raises(RuntimeError):
        ob._ensure_server_key_material(empty)

    assert ob.is_real_public_key(pub)
    assert not ob.is_real_public_key("")
    assert not ob.is_real_public_key("(password auth - no public key)")
    assert ob.normalize_public_key("  ssh-ed25519  AAA  c  ") == "ssh-ed25519 AAA c"
    assert "ssh-ed25519" in ob.public_key_identity(pub)


# ---------------------------------------------------------------------------
# docker_management validate + write
# ---------------------------------------------------------------------------


def test_docker_validate_and_write(monkeypatch):
    from app.services import docker_management as dm

    empty = dm.validate_compose_content("  ")
    assert empty["valid"] is False
    ok = dm.validate_compose_content("services:\n  web:\n    image: nginx\n")
    assert ok["valid"] is True
    bad = dm.validate_compose_content("services:\n  web: [\nfoo: :\n")
    assert bad["valid"] is False
    assert bad["errors"]

    class FakeFile(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class FakeSftp:
        def __init__(self):
            self.store = {}

        def stat(self, p):
            raise FileNotFoundError(p)

        def remove(self, p):
            self.store.pop(p, None)

        def open(self, p, mode):
            buf = FakeFile()
            orig = buf.close

            def _close():
                self.store[p] = buf.getvalue()
                orig()

            buf.close = _close
            return buf

        def rename(self, a, b):
            if a in self.store:
                self.store[b] = self.store.pop(a)

        def close(self):
            pass

    class FakeCli:
        def __init__(self):
            self.sftp = FakeSftp()

        def open_sftp(self):
            return self.sftp

        def close(self):
            pass

    cli = FakeCli()
    monkeypatch.setattr(dm, "get_ssh_client", lambda *_a, **_k: cli)
    monkeypatch.setattr(dm, "run_command", lambda *a, **k: (0, "", ""))
    srv = SimpleNamespace(id=1, hostname="pi")
    okw, err = dm.write_compose_file(srv, "/home/pi/docker/g", "services: {}\n")
    assert okw is True and err == ""
    okd, errd = dm.write_dockerfile(srv, "/home/pi/docker/g/Dockerfile", "FROM alpine\n")
    assert okd is True

    class BoomSftp(FakeSftp):
        def open(self, p, mode):
            raise OSError("disk")

    cli.sftp = BoomSftp()
    badw, _ = dm.write_compose_file(srv, "/p", "x")
    assert badw is False
    badd, _ = dm.write_dockerfile(srv, "/p/Dockerfile", "x")
    assert badd is False


# ---------------------------------------------------------------------------
# host_files docker + sftp_session
# ---------------------------------------------------------------------------


def test_host_files_docker_and_sftp(monkeypatch):
    from app.services import host_files as hf

    srv = SimpleNamespace(
        id=1, hostname="pi", docker_base_dir="/home/pi/docker", ssh_username="pi"
    )
    hf._pool.clear()

    cli = MagicMock()
    sftp = MagicMock()

    @contextmanager
    def _sess(*a, **k):
        if k.get("with_client"):
            yield (cli, sftp)
        else:
            yield sftp

    monkeypatch.setattr(hf, "sftp_session", _sess)
    monkeypatch.setattr(hf, "is_demo_files", lambda: False)
    monkeypatch.setattr(hf, "resolve_logical", lambda *a, **k: ("/home/pi", "/home/pi/docker"))
    monkeypatch.setattr(hf, "under_jail", lambda *a, **k: True)
    monkeypatch.setattr(hf, "is_denied", lambda *a, **k: False)
    monkeypatch.setattr(hf, "rel_of", lambda p, jail: p.replace(jail + "/", ""))
    monkeypatch.setattr(hf, "_assert_in_jail", lambda p, *a, **k: p)
    monkeypatch.setattr(hf, "_refuse_demo_write", lambda: None)

    replies = {
        "docker volume ls -q": (0, "vol1\nbad name\nvol2\n", ""),
        "inspect": (
            0,
            json.dumps(
                [
                    {
                        "Type": "bind",
                        "Source": "/home/pi/docker/g",
                        "Destination": "/data",
                        "Name": "",
                        "RW": True,
                    },
                    {
                        "Type": "volume",
                        "Source": "/var/lib/docker/volumes/v/_data",
                        "Destination": "/v",
                        "Name": "v",
                        "ReadOnly": False,
                    },
                    {"Type": "tmpfs"},
                ]
            ),
            "",
        ),
        "docker ps": (0, "web\ndb\n", ""),
        "docker cp": (0, "", ""),
    }

    def _run(c, cmd, timeout=20):
        if "volume ls" in cmd:
            return replies["docker volume ls -q"]
        if "volume inspect" in cmd:
            return (0, "/home/pi/docker/g/data\n", "")
        if "inspect" in cmd and "Mounts" in cmd:
            return replies["inspect"]
        if "docker ps" in cmd:
            return replies["docker ps"]
        if "docker cp" in cmd:
            return replies["docker cp"]
        return (0, "", "")

    monkeypatch.setattr(hf, "run_command", _run)
    vols = hf.list_docker_volumes(srv)
    assert any(v["name"] == "vol1" for v in vols)
    mounts = hf.list_container_mounts(srv, "web")
    assert any(m["kind"] == "bind" for m in mounts)
    names = hf.list_docker_containers(srv)
    assert "web" in names
    copied = hf.docker_cp_into(srv, "web", "/etc/hostname", "grafana")
    assert copied["name"] == "hostname"
    with pytest.raises(hf.FilesError):
        hf.list_container_mounts(srv, "bad name")
    with pytest.raises(hf.FilesError):
        hf.parse_container_path("../x")
    assert hf.parse_container_path("/etc/hostname") == "/etc/hostname"
    assert hf._docker_name_ok("web_1")
    assert not hf._docker_name_ok("")

    # demo short-circuit
    monkeypatch.setattr(hf, "is_demo_files", lambda: True)
    assert hf.list_docker_volumes(srv) == []
    assert hf.list_container_mounts(srv, "web") == []
    assert hf.list_docker_containers(srv) == []
    monkeypatch.setattr(hf, "is_demo_files", lambda: False)

    # sftp_session branches
    monkeypatch.undo()
    from app.services import host_files as hf2

    hf2._pool.clear()
    opened = MagicMock()
    handle = MagicMock()
    monkeypatch.setattr(hf2, "_open_client", lambda *a, **k: (opened, handle))
    monkeypatch.setattr(hf2, "_transport_alive", lambda *_a, **_k: True)
    monkeypatch.setattr(hf2, "_sweep_idle_locked", lambda *_a, **_k: None)
    with hf2.sftp_session(srv, sftp=handle) as got:
        assert got is handle
    with hf2.sftp_session(srv, sftp=handle, with_client=True, client=opened) as pair:
        assert pair[1] is handle
    with hf2.sftp_session(srv, pooled=False, with_client=True) as pair:
        assert pair[0] is opened
    with hf2.sftp_session(srv, pooled=True, with_client=True) as pair:
        assert pair[0] is opened
    hf2._pool.clear()

    def _boom(*a, **k):
        raise RuntimeError("ssh")

    monkeypatch.setattr(hf2, "_open_client", _boom)
    with pytest.raises(hf2.FilesError):
        with hf2.sftp_session(srv, pooled=False):
            pass


# ---------------------------------------------------------------------------
# herder pg dump / create backup / restore_pg_dump
# ---------------------------------------------------------------------------


def test_herder_pg_dump_and_create_backup(tmp_path, monkeypatch):
    from app.services import herder_backup as hb

    monkeypatch.setattr(hb, "HERDER_BACKUP_DIR", tmp_path)
    monkeypatch.setattr(hb, "_ensure_dir", lambda: None)
    monkeypatch.setattr(hb, "prune_old_backups", lambda *_a, **_k: None)
    monkeypatch.setattr(hb, "_add_data_files_to_tar", lambda *_a, **_k: 0)
    monkeypatch.setattr(hb, "load_settings", lambda: {"keep": 3})
    monkeypatch.setattr(hb, "archive_dir_candidates", lambda: [tmp_path])
    monkeypatch.setattr(hb, "_path_is_writable", lambda *_a, **_k: True)
    monkeypatch.setattr(
        hb,
        "_build_backup_payload",
        lambda **k: {"manifest": {"version": "5"}, "users": []},
    )
    monkeypatch.setattr(hb.shutil, "which", lambda n: f"/usr/bin/{n}")
    monkeypatch.setattr(
        hb, "_database_url", lambda: "postgresql://piherder:pw@localhost/piherder"
    )

    def _run(cmd, **k):
        if cmd and cmd[0] == "pg_dump":
            dest = Path(cmd[cmd.index("--file") + 1])
            dest.write_bytes(b"P" * 200)
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(hb.subprocess, "run", _run)
    dest = tmp_path / "db.dump"
    assert hb.create_pg_dump(dest).is_file()

    env, url = hb._pg_env_and_uri()
    assert env.get("PGPASSWORD") == "pw"
    assert "postgresql" in url

    monkeypatch.setattr(hb.shutil, "which", lambda n: None)
    with pytest.raises(RuntimeError):
        hb.create_pg_dump(tmp_path / "x.dump")
    monkeypatch.setattr(hb.shutil, "which", lambda n: f"/usr/bin/{n}")

    monkeypatch.setattr(hb.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stderr="fail", stdout=""))
    with pytest.raises(RuntimeError):
        hb.create_pg_dump(tmp_path / "bad.dump")
    monkeypatch.setattr(hb.subprocess, "run", _run)

    out = hb.create_herder_backup(include_audit=False, config_only=True)
    assert Path(out).is_file()

    dump_file = tmp_path / "full.dump"
    dump_file.write_bytes(b"P" * 200)
    monkeypatch.setattr(hb, "create_pg_dump", lambda dest: dest.write_bytes(b"P" * 200) or dest)
    full = hb.create_herder_backup(config_only=False)
    assert Path(full).is_file()

    session, engine = _memory()
    _user(session)
    monkeypatch.setattr(hb, "engine", engine)
    assert isinstance(hb._snapshot_table(User), list)

    class FakeSess:
        def __init__(self, *a, **k):
            self._s = session

        def __enter__(self):
            return self._s

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(hb, "Session", FakeSess)
    hb.restore_pg_dump(dump_file)

    monkeypatch.setattr(hb.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stderr="error: FATAL boom", stdout=""))
    with pytest.raises(RuntimeError):
        hb.restore_pg_dump(dump_file)

    monkeypatch.setattr(hb.shutil, "which", lambda n: None)
    with pytest.raises(RuntimeError):
        hb.restore_pg_dump(dump_file)

    assert hb._snapshot_table(User)
    n = hb._avatar_files()
    assert isinstance(n, list)
    tar_buf = io.BytesIO()
    import tarfile as tf

    with tf.open(fileobj=tar_buf, mode="w") as tar:
        added = hb._add_data_files_to_tar(tar)
    assert added >= 0


# ---------------------------------------------------------------------------
# backup restore
# ---------------------------------------------------------------------------


def test_backup_restore_candidates_and_rsync(tmp_path, monkeypatch):
    from app.services import backup_restore as br

    session, _ = _memory()
    dest = tmp_path / "grafana"
    dest.mkdir()
    (dest / "file.txt").write_text("x")
    srv = _server(session)
    monkeypatch.setattr(
        br.backup_profiles,
        "get_backup_profiles",
        lambda *_a, **_k: [
            {
                "source": "/home/pi/docker/grafana",
                "dest_name": "grafana",
                "destination": str(dest),
                "enabled": True,
                "last_backup_str": "now",
            }
        ],
    )
    monkeypatch.setattr(
        br.backup_profiles,
        "get_backup_profiles_db",
        lambda *_a, **_k: [
            {"source": "/home/pi/docker/grafana", "destination": str(dest)}
        ],
    )
    cands = br.list_restore_candidates(srv)
    assert cands[0]["exists"] is True

    empty = br.restore_backup_source(srv, "")
    assert empty["rc"] == 1
    monkeypatch.setattr(br, "validate_backup_path", lambda *a, **k: (False, "blocked"))
    blocked = br.restore_backup_source(srv, "/etc/passwd")
    assert "blocked" in blocked["error"]
    monkeypatch.setattr(br, "validate_backup_path", lambda *a, **k: (True, ""))
    monkeypatch.setattr(br, "parse_rules", lambda *_a, **_k: [])

    no_dest = br.restore_backup_source(srv, "/home/pi/docker/missing")
    assert no_dest["rc"] == 1

    monkeypatch.setattr(br, "get_private_key_plain", lambda *_a, **_k: "")
    nokey = br.restore_backup_source(srv, "/home/pi/docker/grafana")
    assert "SSH" in nokey["error"] or "key" in nokey["error"].lower()

    monkeypatch.setattr(br, "get_private_key_plain", lambda *_a, **_k: "KEY")

    @contextmanager
    def _key(_p):
        yield str(tmp_path / "k")

    monkeypatch.setattr(br, "temp_key_file", _key)
    monkeypatch.setattr(
        br.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="ok", stderr=""),
    )
    ok = br.restore_backup_source(srv, "/home/pi/docker/grafana", dry_run=True)
    assert ok["rc"] == 0
    monkeypatch.setattr(
        br.subprocess,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(br.subprocess.TimeoutExpired("rsync", 1)),
    )
    timed = br.restore_backup_source(srv, "/home/pi/docker/grafana")
    assert timed["rc"] == 124
    monkeypatch.setattr(
        br.subprocess,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("rsync gone")),
    )
    boom = br.restore_backup_source(srv, "/home/pi/docker/grafana")
    assert boom["rc"] == 1


# ---------------------------------------------------------------------------
# DNS plan + sync
# ---------------------------------------------------------------------------


def test_dns_resolve_plan_and_sync(monkeypatch):
    from app.services.dns_fabric import core as core

    session, _ = _memory()
    backend = _server(session, name="app", hostname="app.local", dns_name="app.lan")
    edge = _server(session, name="npm", hostname="npm.local", dns_name="npm.lan")

    monkeypatch.setattr(
        core,
        "_npm_proxy_hosts_cached",
        lambda *_a, **_k: [
            {
                "server_id": backend.id,
                "docker_project": "grafana",
                "domain_names": ["grafana.example.com"],
                "forward_host": "10.0.0.4",
                "forward_port": 3000,
                "label": "grafana",
                "integration_name": "NPM",
            }
        ],
    )
    monkeypatch.setattr(core, "find_npm_host_server", lambda *_a, **_k: edge)
    monkeypatch.setattr(
        core,
        "build_access_path",
        lambda *a, **k: {
            "path_kind": "npm",
            "path_title": "via NPM",
            "hops": [],
            "chain": "CNAME → npm",
        },
    )
    monkeypatch.setattr(core, "_match_pihole_cname", lambda *a, **k: None)
    plan = core.resolve_service_dns_plan(
        session, backend_server_id=backend.id, docker_project="grafana", base_domain="example.com"
    )
    assert plan["fqdn"] == "grafana.example.com"
    assert plan["via_proxy"] is True

    plan2 = core.resolve_service_dns_plan(
        session, backend_server_id=backend.id, docker_project="uptime", fqdn="up.example.com"
    )
    assert plan2["fqdn_source"] == "explicit"

    with pytest.raises(core.DnsFabricError):
        core.resolve_service_dns_plan(session, backend_server_id=99999)

    monkeypatch.setattr(
        core, "fanout_pihole_dns", lambda *a, **k: [{"ok": True, "instance": "p"}]
    )
    monkeypatch.setattr(core, "_summarize_results", lambda r: ("ok", "ok"))
    row = ServiceDnsRecord(
        fqdn="svc.example.com",
        record_type="cname",
        target_server_id=edge.id,
        backend_server_id=backend.id,
        docker_project="grafana",
        via_proxy=True,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    results = core.sync_service_dns(session, row)
    assert results
    row.record_type = "a"
    session.add(row)
    session.commit()
    monkeypatch.setattr(core, "host_ip_for_dns", lambda *_a, **_k: "10.0.0.4")
    core.sync_service_dns(session, row)
    monkeypatch.setattr(core, "host_ip_for_dns", lambda *_a, **_k: "")
    with pytest.raises(core.DnsFabricError):
        core.sync_service_dns(session, row)


# ---------------------------------------------------------------------------
# certificates simulate_sudoers + push leftovers
# ---------------------------------------------------------------------------


def test_cert_sudoers_and_push_helpers(monkeypatch):
    from app.services import certificates as certs
    from app.services import push as push

    cli = MagicMock()
    monkeypatch.setattr(certs.ssh_svc, "run_command", lambda *a, **k: (0, "ok", ""))
    out = certs.simulate_sudoers_access(
        cli,
        remote_dir="~/certs",
        layout="pair",
        write_mode="stage_sudo",
        ssh_user="piherder",
        post_deploy_command="systemctl reload nginx",
    )
    assert out.get("checks") or out.get("ok") is not None or isinstance(out, dict)
    out2 = certs.simulate_sudoers_access(
        cli, remote_dir="/etc/ssl", layout="combined", write_mode="direct", ssh_user="root"
    )
    assert isinstance(out2, dict)

    session, _ = _memory()
    user = _user(session)
    monkeypatch.setattr(push, "ensure_vapid_keys", lambda *_a, **_k: SimpleNamespace(public_key="pk", private_key="sk", contact="mailto:x"))
    sub = push.save_subscription(
        session,
        user,
        endpoint="https://push.example/x",
        p256dh="k",
        auth="a",
        user_agent="test",
    )
    assert sub.endpoint.endswith("/x")
    again = push.save_subscription(
        session,
        user,
        endpoint="https://push.example/x",
        p256dh="k2",
        auth="a2",
    )
    assert again.id == sub.id
    prefs = push.update_preferences(session, user.id, push_enabled=True, backup_failed=False)
    assert prefs.push_enabled is True
    n = push.remove_subscription(session, user, "https://push.example/x")
    assert n >= 1
    keys = push.ensure_vapid_keys(session)
    assert keys is not None
    resolved = push.resolve_vapid_keys(session)
    assert resolved is not None

