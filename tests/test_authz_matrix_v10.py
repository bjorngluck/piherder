"""v1.0 AC — authorization matrix smoke (streams + mutate roles)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlalchemy.pool import StaticPool

from app.database import get_session
from app.main import app
from app.models import User
from app.security.auth import create_access_token, get_password_hash


@pytest.fixture()
def sqlite_engine(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'authz.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture()
def client(sqlite_engine):
    def _session():
        with Session(sqlite_engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    c = TestClient(app, raise_server_exceptions=False)
    try:
        yield c, sqlite_engine
    finally:
        app.dependency_overrides.clear()


def _user(session: Session, *, role: str, email: str) -> User:
    u = User(
        email=email,
        hashed_password=get_password_hash("AuthzTest1ok"),
        role=role,
        is_active=True,
        must_change_password=False,
        totp_enabled=True,
    )
    session.add(u)
    session.commit()
    session.refresh(u)
    return u


def _cookie(uid: int) -> dict[str, str]:
    return {"access_token": create_access_token({"sub": str(uid)})}


# --- AC2: streams must not be anonymous ------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/servers/1/docker/logs/web/stream",
        "/servers/1/docker/build-stream?project=demo",
        "/servers/1/backup/logs/stream",
        "/servers/1/os-patch/logs/stream",
    ],
)
def test_streams_require_login(client, path):
    c, _ = client
    r = c.get(path)
    assert r.status_code == 401, f"{path} → {r.status_code}"


def test_build_stream_viewer_forbidden(client):
    c, engine = client
    with Session(engine) as session:
        uid = _user(session, role="viewer", email="viewer@authz.test").id
    r = c.get(
        "/servers/1/docker/build-stream?project=demo",
        cookies=_cookie(uid),
    )
    # Operator required — 403 (or 404 if server missing after auth)
    assert r.status_code in (403, 404), r.status_code


def test_log_stream_viewer_allowed_auth_gate(client):
    """Viewers may open log SSE once authenticated (read-only); 404 without server."""
    c, engine = client
    with Session(engine) as session:
        uid = _user(session, role="viewer", email="viewer2@authz.test").id
    r = c.get(
        "/servers/1/docker/logs/web/stream",
        cookies=_cookie(uid),
    )
    assert r.status_code in (200, 404), r.status_code
    assert r.status_code != 401


# --- AC1: mutate requires login; viewer blocked on fleet --------------------


@pytest.mark.parametrize(
    "path,data",
    [
        ("/servers/1/docker/container/restart", {"name": "web"}),
        ("/servers/1/docker/prune-unused", {"prune_type": "both"}),
        ("/dns/services", {"fqdn": "x.example", "target_server_id": "1", "backend_server_id": "1"}),
        ("/servers/1/update", {
            "name": "s",
            "hostname": "host.example",
            "ssh_username": "pi",
        }),
    ],
)
def test_fleet_mutate_requires_login(client, path, data):
    c, _ = client
    r = c.post(path, data=data)
    assert r.status_code == 401, f"{path} → {r.status_code}"


def test_viewer_fleet_mutate_forbidden(client):
    c, engine = client
    with Session(engine) as session:
        uid = _user(session, role="viewer", email="vmut@authz.test").id
    r = c.post(
        "/servers/1/docker/container/restart",
        data={"name": "web"},
        cookies=_cookie(uid),
    )
    assert r.status_code == 403, r.status_code


def test_viewer_dns_mutate_forbidden(client):
    c, engine = client
    with Session(engine) as session:
        uid = _user(session, role="viewer", email="vdns@authz.test").id
    r = c.post(
        "/dns/services",
        data={
            "fqdn": "app.example.com",
            "target_server_id": "1",
            "backend_server_id": "1",
        },
        cookies=_cookie(uid),
    )
    assert r.status_code == 403


def test_admin_only_users_forbidden_for_operator(client):
    c, engine = client
    with Session(engine) as session:
        uid = _user(session, role="operator", email="op@authz.test").id
    r = c.get("/auth/users", cookies=_cookie(uid))
    # Admin-only GET on users
    assert r.status_code == 403, r.status_code


def test_admin_can_open_users(client):
    c, engine = client
    with Session(engine) as session:
        uid = _user(session, role="admin", email="adm@authz.test").id
    r = c.get("/auth/users", cookies=_cookie(uid), follow_redirects=False)
    assert r.status_code == 200
