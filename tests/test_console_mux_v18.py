"""v1.8 Mux-2 — list and kill leftover ph-u* sessions for one host. No live SSH."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.main import app
from app.models import Server, User
from app.security.auth import create_user_access_token, get_password_hash
from app.services import ssh_console as sc


def test_parse_and_roundtrip_keeps_this_server_only():
    parsed = sc.parse_mux_session_name("ph-u3-s12-n1-p")
    assert parsed["server_id"] == 12
    assert parsed["role"] == "privileged"
    assert sc.parse_mux_session_name("ph-u3-s12-n1-f")["role"] == "fleet"
    assert sc.parse_mux_session_name("piherder") is None
    assert sc.parse_mux_session_name("ph-u3-s12-n100-f") is None
    assert sc.parse_mux_session_name("ph-u3-s12-n1-f;id") is None

    raw = sc.encode_mux_list(
        [
            {"backend": "tmux", "name": "ph-u3-s12-n0-f"},
            {"backend": "screen", "name": "ph-u3-s99-n0-p"},
            {"backend": "bash", "name": "ph-u3-s12-n0-f"},
            {"backend": "tmux", "name": "not-a-session"},
        ]
    )
    rows = sc.decode_mux_list(raw + ",tmux:ph-u1-s12-n0-f;rm", server_id=12)
    names = {(row["backend"], row["name"]) for row in rows}
    assert names == {("tmux", "ph-u3-s12-n0-f")}
    assert sc.decode_mux_list(raw, server_id=99) == [
        {
            "name": "ph-u3-s99-n0-p",
            "user_id": 3,
            "server_id": 99,
            "tab": 0,
            "role": "privileged",
            "backend": "screen",
        }
    ]


def test_list_host_mux_sessions_filters_other_server(monkeypatch):
    screen = (
        "There are screens on:\n"
        "\t23841.ph-u3-s12-n1-f\t(Detached)\n"
        "\t99.ph-u3-s99-n0-p\t(Detached)\n"
        "\t7.editor\t(Detached)\n"
    )

    def _run(client, cmd, timeout=8):
        if "list-sessions" in cmd:
            return 0, "ph-u3-s12-n0-f\nph-u3-s99-n0-f\nnot-ours\n", ""
        if "screen -ls" in cmd:
            return 1, screen, ""
        return 0, "", ""

    monkeypatch.setattr("app.services.ssh.run_command", _run)
    rows = sc.list_host_mux_sessions(object(), 12)
    assert [(row["backend"], row["name"]) for row in rows] == [
        ("tmux", "ph-u3-s12-n0-f"),
        ("screen", "ph-u3-s12-n1-f"),
    ]
    assert sc.list_host_mux_sessions(None, 12) == []


@pytest.fixture()
def mux_client(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'mux2.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def _session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    with Session(engine) as session:
        user = User(
            email="mux2@test.local",
            hashed_password=get_password_hash("SmokeTest1ok"),
            role="admin",
            is_active=True,
            must_change_password=False,
        )
        viewer = User(
            email="mux2-view@test.local",
            hashed_password=get_password_hash("SmokeTest1ok"),
            role="viewer",
            is_active=True,
            must_change_password=False,
        )
        host = Server(name="pi", hostname="pi.local", os_type="debian", console_mux_enabled=True)
        ha = Server(name="ha", hostname="ha.local", os_type="haos")
        session.add(user)
        session.add(viewer)
        session.add(host)
        session.add(ha)
        session.commit()
        session.refresh(user)
        session.refresh(viewer)
        session.refresh(host)
        session.refresh(ha)
        ids = {
            "user": user.id,
            "viewer": viewer.id,
            "host": host.id,
            "ha": ha.id,
        }
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield client, ids
    finally:
        app.dependency_overrides.pop(get_session, None)


def test_mux_routes_list_kill_and_refuse(mux_client, monkeypatch):
    client, ids = mux_client
    admin = {"access_token": create_user_access_token(SimpleNamespace(id=ids["user"], session_version=0))}
    viewer = {"access_token": create_user_access_token(SimpleNamespace(id=ids["viewer"], session_version=0))}
    sid = ids["host"]
    calls = []

    def _list(client_obj, server_id):
        calls.append(("list", server_id))
        return [
            {
                "name": f"ph-u1-s{server_id}-n0-f",
                "user_id": 1,
                "server_id": server_id,
                "tab": 0,
                "role": "fleet",
                "backend": "tmux",
            },
            {
                "name": "ph-u1-s999-n0-f",
                "user_id": 1,
                "server_id": 999,
                "tab": 0,
                "role": "fleet",
                "backend": "tmux",
            },
        ]

    def _kill(client_obj, backend, name):
        calls.append(("kill", backend, name))

    monkeypatch.setattr("app.routers.server_ssh.ssh_service.get_ssh_client", lambda server: object())
    monkeypatch.setattr("app.routers.server_ssh.ssh_console.list_host_mux_sessions", _list)
    monkeypatch.setattr("app.routers.server_ssh.ssh_console.kill_mux_session", _kill)

    denied = client.post(f"/servers/{sid}/ssh/mux-sessions", cookies=viewer)
    assert denied.status_code == 403

    listed = client.post(f"/servers/{sid}/ssh/mux-sessions", cookies=admin, follow_redirects=False)
    assert listed.status_code == 303
    location = listed.headers["location"]
    assert "mux_listed=1" in location
    assert f"ph-u1-s{sid}-n0-f" in location
    assert "s999" not in location

    page = client.get(location, cookies=admin)
    assert page.status_code == 200
    assert f"ph-u1-s{sid}-n0-f" in page.text
    assert "ssh-mux-leftovers" in page.text
    assert "s999" not in page.text

    bad = client.post(
        f"/servers/{sid}/ssh/mux-sessions/kill",
        data={"session_name": "ph-u1-s999-n0-f", "backend": "tmux"},
        cookies=admin,
        follow_redirects=False,
    )
    assert bad.status_code == 303
    assert "error=mux_bad" in bad.headers["location"]
    assert not any(item[0] == "kill" for item in calls)

    other = client.post(
        f"/servers/{sid}/ssh/mux-sessions/kill",
        data={"session_name": "editor", "backend": "tmux"},
        cookies=admin,
        follow_redirects=False,
    )
    assert "error=mux_bad" in other.headers["location"]

    killed = client.post(
        f"/servers/{sid}/ssh/mux-sessions/kill",
        data={"session_name": f"ph-u1-s{sid}-n0-f", "backend": "tmux"},
        cookies=admin,
        follow_redirects=False,
    )
    assert killed.status_code == 303
    assert "msg=mux_killed" in killed.headers["location"]
    assert ("kill", "tmux", f"ph-u1-s{sid}-n0-f") in calls

    ha = client.post(
        f"/servers/{ids['ha']}/ssh/mux-sessions",
        cookies=admin,
        follow_redirects=False,
    )
    assert ha.status_code == 303
    assert "error=mux_fail" in ha.headers["location"]

    monkeypatch.setattr("app.services.demo.demo_mode", lambda: True)
    demo = client.post(f"/servers/{sid}/ssh/mux-sessions", cookies=admin)
    assert demo.status_code == 403
