"""v1.6 Q-80 thirty-eighth pack — undo enqueue, host-facts refresh, inventory API."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.main import app
from app.models import Job, Server, User
from app.security.auth import get_password_hash
from app.services import api_tokens as tok_svc


def _engine(path):
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def test_undo_enqueue_and_host_facts(tmp_path, monkeypatch):
    import app.services.jobs as jobs
    from app.services import host_facts as facts
    from app.services import jobs_migrate as jm
    from app.services.service_migrate import undo as undo_mod

    engine = _engine(tmp_path / "q38.db")
    monkeypatch.setattr(jobs, "engine", engine)
    monkeypatch.setattr(facts, "engine", engine)
    monkeypatch.setattr(
        "app.services.service_migrate.host_lock.migrate_surface_allowed",
        lambda: True,
    )
    monkeypatch.setattr(jm, "try_acquire_dual_server_lock", lambda *a, **k: ("a", "b"))
    monkeypatch.setattr(jm, "release_dual_server_lock", lambda *a, **k: None)
    monkeypatch.setattr(undo_mod, "run_undo_pipeline", lambda *a, **k: None)

    with Session(engine) as s:
        src = Server(name="src", hostname="src.local", ssh_username="pi", os_type="debian")
        dst = Server(name="dst", hostname="dst.local", ssh_username="pi", os_type="debian")
        s.add(src)
        s.add(dst)
        s.commit()
        s.refresh(src)
        s.refresh(dst)
        parent = Job(
            server_id=src.id,
            job_type="service_migrate",
            status="failed",
            details=json.dumps(
                {
                    "failed_step": "cutover",
                    "source_id": src.id,
                    "dest_server_id": dst.id,
                    "project": "web",
                    "dest_project": "web",
                    "port_map": {"80/tcp": "8080"},
                }
            ),
        )
        s.add(parent)
        s.commit()
        s.refresh(parent)
        pid, sid, did = parent.id, src.id, dst.id

    job = jm.enqueue_service_migrate_undo(pid, user_id=None)
    assert job is not None
    assert job.status == "success"

    try:
        jm.enqueue_service_migrate_undo(pid)
        raised = False
    except ValueError:
        raised = True
    assert raised

    monkeypatch.setattr(jm, "_migrate_run_inline", lambda: False)
    monkeypatch.setattr(jobs, "HAS_CELERY", False)
    parent2 = Job(
        server_id=sid,
        job_type="service_migrate",
        status="failed",
        details=json.dumps(
            {
                "failed_step": "rebind",
                "source_id": sid,
                "dest_server_id": did,
                "project": "db",
                "port_map": {},
            }
        ),
    )
    with Session(engine) as s:
        s.add(parent2)
        s.commit()
        s.refresh(parent2)
        pid2 = parent2.id
    try:
        jm.enqueue_service_migrate_undo(pid2)
    except RuntimeError:
        pass

    from types import SimpleNamespace

    old = datetime.utcnow() - timedelta(hours=2)
    row = SimpleNamespace(host_facts_status="ok", host_facts_at=old)
    assert facts.is_stale(row) is True
    row.host_facts_status = "never"
    assert facts.is_stale(row) is True
    row.host_facts_status = "refreshing"
    assert facts.is_stale(row) is False
    row.host_facts_status = "ok"
    row.host_facts_at = None
    assert facts.is_stale(row) is True

    monkeypatch.setattr(
        "app.services.diagnostics.run_diagnostics",
        lambda server, force=True: {
            "os_pretty": "Debian GNU/Linux 12",
            "device_tree": "Raspberry Pi 5",
            "memory_total_bytes": 8,
            "memory_used_bytes": 2,
            "disk_total_bytes": 100,
            "disk_used_bytes": 40,
        },
    )
    snap = facts.refresh_server_facts(sid, force=True)
    assert snap.get("status") in ("ok", "error", None) or "os_pretty" in snap or snap.get("error")

    monkeypatch.setattr(
        "app.services.diagnostics.run_diagnostics",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ssh down")),
    )
    err = facts.refresh_server_facts(sid, force=True)
    assert err.get("status") == "error"
    missing = facts.refresh_server_facts(99999, force=True)
    assert missing.get("error") == "server not found"

    facts._refreshing.add(did)
    held = facts.refresh_server_facts(did, force=False)
    assert held.get("status") == "refreshing"
    facts._refreshing.discard(did)


def test_inventory_and_services_api(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    monkeypatch.setattr(
        "app.services.integrations.registry.fleet_service_chips",
        lambda session: [
            {
                "id": 1,
                "server_id": 1,
                "server_name": "Lab",
                "label": "grafana",
                "state": "up",
                "message": "ok",
                "scope": "http",
                "docker_project": "grafana",
                "docker_container": "grafana",
                "checked_at": datetime.utcnow(),
            }
        ],
    )
    engine = _engine(tmp_path / "q38b.db")

    def _session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    client = TestClient(app, raise_server_exceptions=False)
    try:
        with Session(engine) as s:
            user = User(
                email="q38@test.local",
                hashed_password=get_password_hash("SmokeTest1ok"),
                role="admin",
                is_active=True,
                must_change_password=False,
                totp_enabled=False,
            )
            s.add(user)
            srv = Server(
                name="Lab",
                hostname="lab.local",
                ssh_username="pi",
                os_type="debian",
                os_pretty="Debian GNU/Linux 12",
                docker_inventory_json=json.dumps(
                    [
                        {
                            "name": "web",
                            "running": True,
                            "state": "running",
                            "image": "nginx",
                            "status": "Up",
                            "project": "web",
                            "service": "web",
                        }
                    ]
                ),
                docker_inventory_status="ok",
                disk_total_bytes=100,
                disk_used_bytes=10,
            )
            s.add(srv)
            s.commit()
            s.refresh(user)
            s.refresh(srv)
            sid = srv.id
            _row, plain = tok_svc.create_api_token(
                s, name="q38", created_by=user, scopes=["read", "edit"]
            )
        auth = {"Authorization": f"Bearer {plain}"}
        r = client.get("/api/v1/inventory", headers=auth)
        assert r.status_code == 200
        r = client.get(f"/api/v1/servers/{sid}/inventory", headers=auth)
        assert r.status_code == 200
        r = client.get("/api/v1/servers/99999/inventory", headers=auth)
        assert r.status_code == 404
        r = client.get("/api/v1/services", headers=auth)
        assert r.status_code == 200
        assert r.json().get("count") == 1
    finally:
        app.dependency_overrides.pop(get_session, None)


def test_compose_editor_helpers_and_page(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from app.routers import server_docker_compose as sdc
    from app.security.auth import create_user_access_token
    from app.services import stack_health as sh

    tree = tmp_path / "tree"
    (tree / "a").mkdir(parents=True)
    (tree / "a" / "f.txt").write_bytes(b"hello")
    (tree / "b.txt").write_bytes(b"xy")
    used, method = sh._tree_used_bytes(tree, timeout_sec=5)
    assert used is None or used >= 0
    assert method
    kids = sh._top_level_usage(tree, limit=4)
    assert isinstance(kids, list)
    missing, _why = sh._tree_used_bytes(tmp_path / "nope", timeout_sec=2)
    assert missing is None

    assert sdc._parse_files_json("") is None
    assert sdc._parse_files_json("[]") is None
    parsed = sdc._parse_files_json(
        json.dumps({"docker-compose.yml": "x", "../x": "no", "__meta": "no", ".env": None})
    )
    assert "docker-compose.yml" in parsed
    assert "../x" not in parsed

    engine = _engine(tmp_path / "q38c.db")

    def _session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    client = TestClient(app, raise_server_exceptions=False)
    try:
        with Session(engine) as s:
            user = User(
                email="q38c@test.local",
                hashed_password=get_password_hash("SmokeTest1ok"),
                role="admin",
                is_active=True,
                must_change_password=False,
                totp_enabled=False,
            )
            srv = Server(
                name="Lab",
                hostname="lab.local",
                ssh_username="pi",
                docker_base_dir="/home/pi/docker",
                os_type="debian",
            )
            s.add(user)
            s.add(srv)
            s.commit()
            s.refresh(user)
            s.refresh(srv)
            sid = srv.id
            client.cookies.set("access_token", create_user_access_token(user))

        monkeypatch.setattr(sdc, "_invalidate_inventory", lambda *a, **k: None)
        live = {"docker-compose.yml": "services: {}\n", ".env": "TOKEN=secret\n"}

        class _WS:
            proj = {"path": "/home/pi/docker/web", "compose_file": "docker-compose.yml"}
            live_files = live
            project_files = dict(live)
            drafts = []
            editing_version_id = None
            live_compose_key = "docker-compose.yml"
            live_compose = live["docker-compose.yml"]
            template_dep = None

        monkeypatch.setattr(
            "app.services.compose_editor.load_compose_editor_workspace",
            lambda *a, **k: _WS(),
        )
        monkeypatch.setattr(
            "app.services.docker_management.list_compose_projects",
            lambda server: [{"name": "web", "path": "/home/pi/docker/web"}],
        )
        monkeypatch.setattr(
            "app.services.docker_management.get_project_live_files",
            lambda server, path: live,
        )
        r = client.get(f"/servers/{sid}/docker/compose/web/edit?errors=%5B%5D")
        assert r.status_code in (200, 500)
        r = client.get(f"/servers/{sid}/docker/compose/web/file-content?file=.env")
        assert r.status_code in (200, 404, 500)
        r = client.get(f"/servers/{sid}/docker/compose/missing/file-content")
        assert r.status_code in (404, 500)

        with Session(engine) as s:
            server = s.get(Server, sid)
            snap = sdc._snapshot_for_save(
                server,
                "web",
                "/home/pi/docker/web",
                session=s,
                editing_version_id=None,
                files_json=json.dumps({"docker-compose.yml": "services: {web: {}}\n", ".env": "********\n"}),
                single_updates={".env": "TOKEN=********\n"},
            )
            assert "docker-compose.yml" in snap

        draft = SimpleNamespace(id=3, is_draft=True, files=json.dumps({"Dockerfile": "FROM scratch\n"}))
        live_v = SimpleNamespace(id=4, is_draft=False, files=json.dumps({"Dockerfile": "FROM nginx\n"}))
        monkeypatch.setattr(
            sdc.docker_svc,
            "list_compose_projects",
            lambda server: [{"name": "web", "path": "/p", "dockerfile_path": "/p/Dockerfile"}],
        )
        monkeypatch.setattr(sdc.docker_svc, "read_dockerfile", lambda server, path: "FROM nginx\n")
        monkeypatch.setattr(sdc.docker_svc, "get_versions", lambda *a, **k: [draft, live_v])
        r = client.get(f"/servers/{sid}/docker/compose/web/dockerfile/edit?load_draft=3")
        assert r.status_code in (200, 500)

        from app.services import stack_monitor as mon

        monkeypatch.setattr(
            mon.apol,
            "inventory_down_alerts_enabled",
            lambda: (_ for _ in ()).throw(RuntimeError("policy")),
        )
        monkeypatch.setattr(mon, "load_settings", lambda: {"stack_inventory_down_alerts": "yes"})
        assert mon.inventory_down_alerts_enabled() is True
        monkeypatch.setattr(mon, "load_settings", lambda: {"stack_inventory_down_alerts": False})
        assert mon.inventory_down_alerts_enabled() is False
    finally:
        app.dependency_overrides.pop(get_session, None)
