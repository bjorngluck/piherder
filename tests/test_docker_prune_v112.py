"""Docker prune is a job. Volumes stay on the page. API and MCP need edit."""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.database import get_session
from app.main import app
from app.models import AuditLog, Job, Server, User
from app.security.auth import create_user_access_token, get_password_hash
from app.services import api_tokens as tok
from app.services import docker_management as dm


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def test_prune_commands_keep_volumes_out_of_both(monkeypatch):
    cmds = []

    def _run(_client, cmd, timeout=60):
        del timeout
        cmds.append(cmd)
        return 0, "Total reclaimed space: 0B", ""

    monkeypatch.setattr(dm, "get_ssh_client", lambda _server: object())
    monkeypatch.setattr(dm, "run_command", _run)
    server = Server(name="host-a", hostname="host-a.local")
    both = dm.prune_unused(server, "both")
    assert both["success"] is True
    assert any("image prune" in cmd for cmd in cmds)
    assert any("container prune" in cmd for cmd in cmds)
    assert not any("volume prune" in cmd or "network prune" in cmd for cmd in cmds)
    cmds.clear()
    dm.prune_unused(server, "networks")
    assert cmds == ["docker network prune -f"]
    cmds.clear()
    dm.prune_unused(server, "volumes")
    assert cmds == ["docker volume prune -f"]
    assert dm.prune_unused(server, "nope")["success"] is False


def _client(engine, monkeypatch):
    import app.database as dbmod
    import app.services.jobs as jobs

    monkeypatch.setattr("app.database.engine", engine)
    monkeypatch.setattr("app.database.engine", engine)
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    monkeypatch.setattr(
        dm,
        "prune_unused",
        lambda _server, prune_type="both": {
            "success": True,
            "output": "Total reclaimed space: 1MB",
            "type": prune_type,
        },
    )

    def _session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    client = TestClient(app, raise_server_exceptions=False)
    with Session(engine) as session:
        user = User(
            email="prune@test.local",
            hashed_password=get_password_hash("SmokeTest1ok"),
            role="operator",
            is_active=True,
            must_change_password=False,
            totp_enabled=False,
        )
        host = Server(
            name="host-a",
            hostname="host-a.local",
            ssh_username="pi",
            container_patch_enabled=True,
        )
        session.add(user)
        session.add(host)
        session.commit()
        session.refresh(user)
        session.refresh(host)
        _read, read_plain = tok.create_api_token(
            session, name="read", created_by=user, scopes=["read"]
        )
        _jobs, jobs_plain = tok.create_api_token(
            session, name="jobs", created_by=user, scopes=["read", "jobs"]
        )
        _edit, edit_plain = tok.create_api_token(
            session, name="edit", created_by=user, scopes=["read", "edit"]
        )
        _narrow, narrow_plain = tok.create_api_token(
            session,
            name="os-only",
            created_by=user,
            scopes=["read", "edit", "feature:os"],
        )
        session.refresh(user)
        sid = host.id
        client.cookies.set("access_token", create_user_access_token(user))
    return client, sid, {
        "read": read_plain,
        "jobs": jobs_plain,
        "edit": edit_plain,
        "narrow": narrow_plain,
    }


def _prune_audits(engine):
    with Session(engine) as session:
        return list(session.exec(select(AuditLog).where(AuditLog.action == "docker_prune")).all())


def test_page_starts_a_job_and_volumes_wait_for_confirm(tmp_path, monkeypatch):
    del tmp_path
    engine = _engine()
    client, sid, _secrets = _client(engine, monkeypatch)
    try:
        held = client.post(
            f"/servers/{sid}/docker/prune-unused",
            data={"prune_type": "volumes"},
            follow_redirects=False,
        )
        assert held.status_code == 303
        assert "prune=need_confirm" in held.headers["location"]
        assert _prune_audits(engine) == []

        started = client.post(
            f"/servers/{sid}/docker/prune-unused",
            data={"prune_type": "volumes", "confirm": "true"},
            follow_redirects=False,
        )
        assert started.status_code == 303
        assert "prune=queued" in started.headers["location"]
        assert "prune_type=volumes" in started.headers["location"]
        audits = _prune_audits(engine)
        assert len(audits) == 1
        assert audits[0].status == "success"
        assert "volumes" in (audits[0].details or "")
        assert "1MB" in (audits[0].output_snippet or "")
    finally:
        app.dependency_overrides.clear()


def test_api_and_hosted_mcp_require_edit_and_refuse_volumes(monkeypatch):
    engine = _engine()
    client, sid, secrets = _client(engine, monkeypatch)
    try:
        for token in (secrets["read"], secrets["jobs"], secrets["narrow"]):
            denied = client.post(
                f"/api/v1/servers/{sid}/docker/prune",
                headers={"Authorization": f"Bearer {token}"},
                json={"prune_type": "both"},
            )
            assert denied.status_code == 403, token

        volumes = client.post(
            f"/api/v1/servers/{sid}/docker/prune",
            headers={"Authorization": f"Bearer {secrets['edit']}"},
            json={"prune_type": "volumes"},
        )
        assert volumes.status_code == 400
        assert "docker/volumes" in volumes.json()["detail"]

        accepted = client.post(
            f"/api/v1/servers/{sid}/docker/prune",
            headers={"Authorization": f"Bearer {secrets['edit']}"},
            json={"prune_type": "networks"},
        )
        assert accepted.status_code == 202, accepted.text
        body = accepted.json()
        assert body["job_type"] == "docker_prune"
        assert body["prune_type"] == "networks"
        audits = _prune_audits(engine)
        assert any(row.status == "success" and "networks" in (row.details or "") for row in audits)

        with Session(engine) as session:
            session.add(Job(server_id=sid, job_type="docker_prune", status="pending"))
            session.commit()
        busy = client.post(
            f"/api/v1/servers/{sid}/docker/prune",
            headers={"Authorization": f"Bearer {secrets['edit']}"},
            json={"prune_type": "images"},
        )
        assert busy.status_code == 409

        listed = client.post(
            "/mcp",
            headers={
                "Authorization": f"Bearer {secrets['read']}",
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        names = [tool["name"] for tool in listed.json()["result"]["tools"]]
        assert "prune_docker" not in names

        hidden = client.post(
            "/mcp",
            headers={
                "Authorization": f"Bearer {secrets['read']}",
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "prune_docker", "arguments": {"server_id": sid}},
            },
        )
        assert hidden.json()["result"]["isError"] is True

        called = client.post(
            "/mcp",
            headers={
                "Authorization": f"Bearer {secrets['edit']}",
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "prune_docker",
                    "arguments": {"server_id": sid, "prune_type": "volumes"},
                },
            },
        )
        assert called.json()["result"]["isError"] is True
        assert "docker/volumes" in called.json()["result"]["content"][0]["text"]
    finally:
        app.dependency_overrides.clear()
