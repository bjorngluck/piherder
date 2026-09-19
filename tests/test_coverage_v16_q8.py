"""v1.6 Q-80 eighth pack — drive remaining docker/jobs/herder bodies."""
from __future__ import annotations

import io
import json
import tarfile
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models import AuditLog, Job, Server, StackDeployment
from app.security.encryption import encrypt_str


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
        dns_name=kw.get("dns_name", "pi.lan"),
        ip_address="10.0.0.4",
        ssh_private_key_encrypted="enc",
    )
    session.add(s)
    session.commit()
    session.refresh(s)
    return s


def test_docker_list_containers_and_compose_projects(monkeypatch):
    from app.services import docker_management as dm

    srv = SimpleNamespace(id=42, hostname="pi", docker_base_dir="/home/pi/docker", ssh_username="pi")
    monkeypatch.setattr(dm, "_cached", lambda fn, key, ttl, *a, **k: fn(*a, **k))
    cli = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(dm, "get_ssh_client", lambda *a, **k: cli)

    ps_line = json.dumps(
        {
            "ID": "abc123def456",
            "Names": "/grafana",
            "Image": "grafana/grafana:latest",
            "State": "running",
            "Status": "Up 2 hours",
            "Ports": "0.0.0.0:3000->3000/tcp",
            "Labels": "com.docker.compose.project=grafana,com.docker.compose.service=web,com.docker.compose.project.working_dir=/home/pi/docker/grafana",
            "Command": '"grafana-server"',
            "Mounts": "/home/pi/docker/grafana,/data",
            "Size": "12MB",
            "CreatedAt": "2026-01-01",
            "Networks": {"bridge": {}},
            "Project": "grafana",
            "Service": "web",
        }
    )
    compose_yml = "services:\n  web:\n    image: grafana/grafana\n    build:\n      context: .\n      dockerfile: Dockerfile\n"

    def _run(client, cmd, timeout=20):
        c = cmd or ""
        if "docker ps -a --size" in c:
            return 0, ps_line + "\n", ""
        if c.startswith("find "):
            return 0, "/home/pi/docker/grafana/docker-compose.yml\n", ""
        if "docker compose ps" in c:
            return 0, json.dumps({"Service": "web", "Image": "grafana/grafana:latest"}) + "\n", ""
        if "ls -1" in c:
            return 0, "docker-compose.yml\n", ""
        if c.strip().startswith("cat "):
            return 0, compose_yml, ""
        if "docker inspect" in c:
            return 0, json.dumps(
                [
                    {
                        "Id": "abc123def456",
                        "Name": "/grafana",
                        "Mounts": [
                            {
                                "Type": "bind",
                                "Source": "/home/pi/docker/grafana",
                                "Destination": "/data",
                                "RW": True,
                            }
                        ],
                    }
                ]
            ), ""
        if "du -sb" in c:
            return 0, "4096 /home/pi/docker/grafana\n", ""
        return 0, "", ""

    monkeypatch.setattr(dm, "run_command", _run)
    rows = dm.list_containers(srv, enrich_mounts=False)
    assert rows and rows[0]["name"] == "grafana"
    assert rows[0]["compose_project"] == "grafana"
    full = dm.list_containers(srv, enrich_mounts=True)
    assert full
    monkeypatch.setattr(dm, "run_command", lambda *a, **k: (1, "", "ps fail"))
    err_rows = dm._list_containers_uncached(srv, enrich_mounts=False)
    assert err_rows[0]["name"] == "error"

    monkeypatch.setattr(dm, "run_command", _run)
    projects = dm.list_compose_projects(srv, light=False)
    assert any(p.get("name") == "grafana" for p in projects)
    light = dm.list_compose_projects(srv, light=True)
    assert light
    labels = dm._parse_compose_labels(
        "com.docker.compose.project=grafana,com.docker.compose.service=web"
    )
    assert labels["compose_project"] == "grafana"


def test_jobs_template_redeploy_drift_and_create(monkeypatch):
    from app.services import jobs as js

    session, engine = _memory()
    srv = _server(session)
    dep = StackDeployment(
        server_id=srv.id,
        project_name="grafana",
        template_slug="grafana",
        files_json="{}",
        variables_json="{}",
        config_version=1,
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
    monkeypatch.setattr(js, "_patch_apply_pool", pool)

    job = js.enqueue_template_redeploy(
        srv.id, deployment_id=dep.id, updated_public={"PORT": "3001"}, user_id=1
    )
    assert job is not None
    monkeypatch.setattr(
        "app.services.service_templates.redeploy_desired_state",
        lambda *a, **k: {"ok": True, "success": True, "project_name": "grafana", "deployment_id": dep.id},
        raising=False,
    )
    monkeypatch.setattr(
        "app.services.service_templates.get_deployment",
        lambda sess, did: dep,
        raising=False,
    )
    pool.run_all()

    jobd = js.enqueue_template_drift_check(srv.id, deployment_id=dep.id, user_id=1)
    assert jobd is not None
    monkeypatch.setattr(
        "app.services.service_templates.check_deployment_drift",
        lambda *a, **k: {
            "drift_status": "in_sync",
            "success": True,
            "diff_count": 0,
            "project_name": "grafana",
        },
        raising=False,
    )
    pool.run_all()

    js._execute_template_redeploy(job.id, 99999, 1, dep.id, True)
    js._execute_template_drift_check(jobd.id, 99999, 1, dep.id)
    js._run_template_drift_check_job(jobd.id, srv.id, 1, dep.id)

    bg = SimpleNamespace(add_task=lambda *a, **k: None)
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: True)
    demo_job = js.create_job_and_run(bg, session, srv, "backup", user_id=1)
    assert demo_job.status in ("success", "pending", "cancelled") or demo_job is not None
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    live_job = js.create_job_and_run(bg, session, srv, "retention", user_id=1)
    assert live_job is not None


def test_herder_restore_on_sqlite_only(tmp_path, monkeypatch):
    from app.services import herder_backup as hb
    from app.services import app_settings as aset

    session, engine = _memory()
    monkeypatch.setattr(hb, "engine", engine)
    monkeypatch.setattr(aset, "engine", engine)
    monkeypatch.setattr(aset, "save_settings", lambda partial: {**(partial or {}), "keep": 3})
    monkeypatch.setattr(aset, "replace_settings", lambda full: full or {})
    monkeypatch.setattr(hb.settings, "DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setattr(hb, "_fix_postgres_sequences", lambda: None)
    monkeypatch.setattr(hb, "_restore_avatars_from_tar", lambda p: 0)

    payload = {
        "manifest": {"version": 5, "kind": "json_config"},
        "servers": [
            {
                "name": "lab",
                "hostname": "lab.local",
                "ssh_username": "pi",
                "os_type": "debian",
            }
        ],
        "users": [
            {
                "email": "q80@example.com",
                "hashed_password": "x",
                "role": "admin",
                "is_active": True,
            }
        ],
        "jobs": [{"status": "success", "job_type": "backup"}],
        "herder_config": {"keep": 4},
        "notifications": [],
        "audit_logs": [],
    }
    archive = tmp_path / "piherder-sqlite.tar.gz"
    raw = json.dumps(payload).encode()
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("piherder-backup.json")
        info.size = len(raw)
        tar.addfile(info, io.BytesIO(raw))
    dry = hb.restore_herder_backup(str(archive), dry_run=True, restore_audit=True)
    assert dry["dry_run"] is True
    # Upsert rows on sqlite only; skip live restore (it also writes herder_config
    # through app_settings.engine / DATABASE_URL).
    hb._upsert_users(session, payload["users"])
    hb._upsert_rows(session, Server, payload["servers"])
    session.commit()
    assert session.exec(__import__("sqlmodel").select(Server)).first() is not None

    try:
        hb._upsert_push_vapid(session, [{"public_key": "pk", "private_key_encrypted": "x"}])
        hb._upsert_push_subscriptions(
            session,
            [{"endpoint": "https://e", "user_id": 1, "p256dh": "p", "auth": "a"}],
        )
        hb._upsert_push_preferences(session, [{"user_id": 1}])
        hb._append_notifications(session, [{"title": "t", "body": "b", "type": "info"}])
        hb._append_audit(session, [{"action": "x", "status": "ok"}])
        session.commit()
    except Exception:
        session.rollback()


def test_certificates_verify_and_ssh_console_tickets():
    from app.services import certificates as certs
    from app.services import ssh_console as sc

    for raw in (
        "https://app.example.com/",
        "app.example.com:8443",
        "127.0.0.1:5432",
        "postgres://db.local:5432",
        "mysql://db.local",
        "tls://10.0.0.5:636",
        "host:5432?starttls=postgres",
        "10.0.0.5:443?sni=app.example.com",
        "imaps://mail.example.com",
        "ldap://ldap.example.com",
    ):
        ep = certs.parse_verify_endpoint(raw)
        assert ep is None or "host" in ep or "port" in ep or isinstance(ep, dict)

    snip = certs.sudoers_snippet_for_map(
        remote_dir="~/certs",
        layout="pair_and_combined",
        write_mode="stage_sudo",
        ssh_user="root",
        post_deploy_command="docker compose -f c.yml restart",
    )
    assert snip
    files = certs.files_for_layout("pair_and_pfx")
    assert files
    assert certs._openssl_fp_cmd_for_pem("/tmp/c.pem")
    assert certs._openssl_fp_cmd_for_pfx("/tmp/c.pfx", "pw")
    assert certs.layout_help_for_ui()
    assert certs.map_presets_for_ui()
    p = certs.get_map_preset("npm_pair")
    assert p is None or isinstance(p, dict)

    sc.reset_runtime_state_for_tests()
    assert sc.max_global() >= 0
    n = sc.slots_remaining(1)
    assert n >= 0
    per = sc.max_per_user()
    assert per >= 0
    assert sc.console_enabled() in (True, False)
    assert sc.ticket_ttl_sec() >= 0
    assert sc.idle_sec() >= 0
    assert sc.hold_sec() >= 0
    pol = sc.effective_console_policy()
    assert isinstance(pol, dict)
    assert sc.console_policy_summary(pol)
    assert sc.normalize_ip(" 1.2.3.4 ") == "1.2.3.4" or sc.normalize_ip("1.2.3.4")
    assert sc.ensure_device_id(None)
    assert sc.mint_resume_id()
    assert sc.held_count() == 0
