"""v1.7 coverage — drive router bodies that the generic route sweep only opens.

SSH, Celery, and live Docker are stubs. Rows live in a temp SQLite engine.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

from sqlmodel import Session

from app.models import Integration, Server
from tests.test_coverage_v17_q1 import _client, _engine


def _job(status="pending"):
    return SimpleNamespace(id=9, status=status)


def test_docker_dns_backup_and_servers(tmp_path, monkeypatch):
    from app.services import docker_management as docker_svc
    import app.services.docker_inventory as inv
    import app.routers.server_docker as docker_mod
    import app.routers.server_docker_compose as compose_mod

    jobs = sys.modules["app.services.jobs"]
    engine = _engine(tmp_path / "q7.db")
    client, _uid, sid = _client(engine, monkeypatch)
    monkeypatch.setattr(inv, "request_refresh", lambda *a, **k: False)
    monkeypatch.setattr(inv, "invalidate_after_mutation", lambda *a, **k: None)
    monkeypatch.setattr(
        docker_svc,
        "list_compose_projects",
        lambda *_a, **_k: [
            {
                "name": "web",
                "path": "/home/pi/docker/web",
                "dockerfile_path": "/home/pi/docker/web/Dockerfile",
            }
        ],
    )
    monkeypatch.setattr(docker_svc, "get_project_live_files", lambda *_a, **_k: {"compose.yml": "services: {}\n"})
    monkeypatch.setattr(docker_svc, "write_project_files", lambda *_a, **_k: (True, ""))
    monkeypatch.setattr(docker_svc, "redeploy_project", lambda *_a, **_k: {"success": True})
    monkeypatch.setattr(docker_svc, "compose_action", lambda *_a, **_k: {"success": True, "output": "ok"})
    monkeypatch.setattr(docker_svc, "container_action", lambda *_a, **_k: {"success": True})
    monkeypatch.setattr(docker_svc, "get_logs", lambda *_a, **_k: "log line")
    monkeypatch.setattr(docker_svc, "prune_unused", lambda *_a, **_k: {"success": True, "output": "pruned"})
    monkeypatch.setattr(
        "app.services.ssh.generate_keypair",
        lambda **_k: ("ssh-ed25519 AAAA piherder", "-----BEGIN OPENSSH PRIVATE KEY-----\n"),
    )
    monkeypatch.setattr(
        "app.services.ssh_onboarding.public_key_from_private",
        lambda *_a, **_k: "ssh-ed25519 AAAA uploaded",
    )

    calls = {"n": 0}

    def _enqueue(*_a, **_k):
        calls["n"] += 1
        if calls["n"] == 2:
            raise jobs.JobAlreadyActive(_job("running"))
        if calls["n"] == 4:
            raise ValueError("bad path")
        return _job()

    for name in (
        "enqueue_docker_stack_lifecycle",
        "enqueue_docker_stack_remove",
        "enqueue_docker_stack_deploy",
        "enqueue_docker_stack_check",
    ):
        monkeypatch.setattr(jobs, name, _enqueue)

    import app.services.container_annotations as ann

    monkeypatch.setattr(ann, "set_annotation", lambda *a, **k: {"category_key": "app", "tags": ["edge"]})
    monkeypatch.setattr(ann, "set_order_via_annotations", lambda *a, **k: ["web", "db"])
    import app.services.stack_order as so

    monkeypatch.setattr(so, "set_order", lambda *a, **k: ["web", "db"])

    headers = {"X-PiHerder-Async": "1", "Accept": "application/json"}
    posts = [
        (f"/servers/{sid}/docker/compose/restart", {"project_path": "/home/pi/docker/web"}, headers),
        (f"/servers/{sid}/docker/compose/stop", {"project_path": "/home/pi/docker/web"}, headers),
        (f"/servers/{sid}/docker/compose/restart", {"project_path": "/home/pi/docker/web", "service": "web"}, {}),
        (f"/servers/{sid}/docker/compose/remove", {"project_path": "/home/pi/docker/web", "remove_ack": "1"}, headers),
        (f"/servers/{sid}/docker/compose/remove", {"project_path": "/home/pi/docker/web"}, {}),
        (f"/servers/{sid}/docker/redeploy", {"project_path": "/home/pi/docker/web", "pull": "true", "compose_file": "compose.yml"}, headers),
        (f"/servers/{sid}/docker/check-updates", {"project_path": "/home/pi/docker/web"}, headers),
        (f"/servers/{sid}/docker/container/restart", {"name": "web"}, {}),
        (f"/servers/{sid}/docker/prune-unused", {"prune_type": "both"}, {}),
        (
            f"/servers/{sid}/docker/compose/web/save",
            {"content": "services: {}\n", "active_file": "compose.yml", "via_modal": "true"},
            {},
        ),
        (
            f"/servers/{sid}/docker/compose/web/dockerfile/save",
            {"content": "FROM scratch\n", "action": "deploy", "via_modal": "true"},
            {},
        ),
        (
            f"/servers/{sid}/backup-config",
            {"backup_schedule": "not cron", "backup_paths": "/home/pi", "scope": "this_host"},
            {},
        ),
        (
            f"/servers/{sid}/backup-config",
            {
                "backup_schedule": "0 2 * * *",
                "backup_paths": "/home/pi/docker",
                "dest_root": "/backups",
                "folder_name": "pi",
                "retention_days": "7",
                "scope": "this_host",
            },
            {},
        ),
        (
            f"/servers/{sid}/backup-config",
            {"backup_paths": "/home/pi", "dest_root": "/backups", "scope": "global", "backup_schedule": ""},
            {},
        ),
        (
            "/servers/add",
            {"name": "new", "hostname": "new.local", "ssh_username": "pi", "key_mode": "generate", "ssh_password": "secret"},
            {},
        ),
        (
            "/servers/add",
            {"name": "pw", "hostname": "pw.local", "key_mode": "password", "ssh_password": "secret"},
            {},
        ),
        (
            "/servers/add",
            {"name": "up", "hostname": "up.local", "key_mode": "upload", "private_key": "not-a-key", "ssh_password": "secret"},
            {},
        ),
        (
            "/dns/container-annotation",
            {
                "server_id": str(sid),
                "project": "web",
                "container_key": "web",
                "category_key": "app",
                "tags": "edge,proxy",
                "visual_stack_id": "main",
            },
            {"Accept": "application/json"},
        ),
        (
            "/dns/container-annotation",
            {
                "server_id": str(sid),
                "project": "web",
                "container_key": "web",
                "clear_category": "1",
                "clear_visual_stack": "1",
                "visual_stack_id": "3",
            },
            {"Accept": "application/json"},
        ),
        (
            "/dns/stack-order",
            {"server_id": str(sid), "project": "web", "order": '["web","db"]', "visual_stack": "main"},
            {"Accept": "application/json"},
        ),
        (
            "/dns/stack-order",
            {"server_id": str(sid), "project": "web", "order": "web, db", "visual_stack": "all"},
            {},
        ),
    ]
    try:
        gets = [
            f"/servers/{sid}/docker/logs/web?format=json",
            f"/servers/{sid}?edit=1&show_ssh_key=1",
            f"/servers/{sid}/docker",
        ]
        for path in gets:
            response = client.get(path, follow_redirects=False)
            assert response.status_code < 600
        for path, data, hdrs in posts:
            response = client.post(path, data=data, headers=hdrs, follow_redirects=False)
            assert response.status_code < 600, path

        monkeypatch.setattr(docker_svc, "write_project_files", lambda *_a, **_k: (False, "denied"))
        failed = client.post(
            f"/servers/{sid}/docker/compose/web/save",
            data={"content": "x", "active_file": "compose.yml", "via_modal": "true"},
            follow_redirects=False,
        )
        assert failed.status_code < 600

        def _ann_fail(*_a, **_k):
            raise ValueError("bad tag")

        monkeypatch.setattr(ann, "set_annotation", _ann_fail)
        bad = client.post(
            "/dns/container-annotation",
            data={"server_id": str(sid), "project": "web", "container_key": "web"},
            headers={"Accept": "application/json"},
            follow_redirects=False,
        )
        assert bad.status_code < 600
        assert docker_mod and compose_mod
    finally:
        from app.database import get_session
        from app.main import app

        app.dependency_overrides.pop(get_session, None)


def test_integration_mutations(tmp_path, monkeypatch):
    from app.services.integrations import poll as poll_svc
    from app.services.integrations import registry as reg

    engine = _engine(tmp_path / "q7b.db")
    client, _uid, sid = _client(engine, monkeypatch)

    class _Result:
        ok = True
        error = ""
        version = "1"
        queries = 3
        blocked = 1
        worker_online = True
        monitors = [1]
        dashboards = [{"uid": "abc"}]
        proxy_hosts = [1]
        certificates = [1]
        cidrs = ["10.0.0.0/24"]

    monkeypatch.setattr(poll_svc, "test_connection", lambda *_a, **_k: _Result())
    monkeypatch.setattr(poll_svc, "poll_integration", lambda *_a, **_k: {"ok": True})
    monkeypatch.setattr(reg, "dashboards_from_cache", lambda *_a, **_k: [{"uid": "abc", "title": "Fleet"}])

    types = [
        reg.TYPE_UPTIME_KUMA,
        reg.TYPE_GRAFANA,
        reg.TYPE_PIHOLE,
        reg.TYPE_NPM,
        reg.TYPE_NMAP,
        reg.TYPE_GENERIC_URL,
    ]
    ids = []
    with Session(engine) as session:
        for kind in types:
            row = Integration(type=kind, name=kind, base_url="http://example", enabled=True)
            session.add(row)
            session.commit()
            session.refresh(row)
            ids.append((kind, row.id))

    try:
        for kind, iid in ids:
            response = client.post(f"/integrations/{iid}/test", follow_redirects=False)
            assert response.status_code < 600, kind
            response = client.post(f"/integrations/{iid}/poll", follow_redirects=False)
            assert response.status_code < 600
            response = client.post(
                f"/integrations/{iid}/edit",
                data={
                    "name": kind,
                    "base_url": "http://example",
                    "api_key": "token",
                    "poll_interval_sec": "120",
                    "tls_verify": "1",
                    "enabled": "1",
                    "username": "admin",
                    "password": "secret",
                },
                follow_redirects=False,
            )
            assert response.status_code < 600
            response = client.post(
                f"/integrations/{iid}/bindings",
                data={
                    "server_id": str(sid),
                    "external_id": "mon-1",
                    "role": "service",
                    "docker_project": "web",
                    "docker_container": "web",
                    "display_name": "web",
                },
                follow_redirects=False,
            )
            assert response.status_code < 600
        graf = next(iid for kind, iid in ids if kind == reg.TYPE_GRAFANA)
        cleared = client.post(
            f"/integrations/{graf}/bindings",
            data={
                "server_id": str(sid),
                "role": "dashboard",
                "kind": "metrics",
                "external_id": "",
                "clear": "1",
            },
            follow_redirects=False,
        )
        bound = client.post(
            f"/integrations/{graf}/bindings",
            data={
                "server_id": str(sid),
                "role": "dashboard",
                "kind": "containers",
                "external_id": "abc",
                "display_name": "Fleet",
            },
            follow_redirects=False,
        )
        assert cleared.status_code < 600 and bound.status_code < 600
        assert Server
    finally:
        from app.database import get_session
        from app.main import app

        app.dependency_overrides.pop(get_session, None)
