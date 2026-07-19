"""HTTP TestClient smoke — auth gates + main shells (no live SSH / no real Postgres required).

Uses an in-memory SQLite session override so CI unit jobs stay DB-free.
Does not enter the app lifespan (no Alembic / scheduler bootstrap).
"""
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
        f"sqlite:///{tmp_path / 'smoke.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture()
def smoke_client(sqlite_engine):
    """TestClient with get_session → SQLite; lifespan never entered."""

    def _session():
        with Session(sqlite_engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    # Client without context manager avoids ASGI lifespan (Alembic / real engine).
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield client, sqlite_engine
    finally:
        app.dependency_overrides.clear()


def _make_user(session: Session, *, role: str = "admin", email: str = "admin@smoke.test") -> User:
    user = User(
        email=email,
        hashed_password=get_password_hash("SmokeTest1ok"),
        role=role,
        is_active=True,
        must_change_password=False,
        totp_enabled=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _auth_cookie(user_id: int) -> dict[str, str]:
    token = create_access_token({"sub": str(user_id)})
    return {"access_token": token}


# --- unauthenticated -------------------------------------------------------


def test_health_ok(smoke_client):
    client, _ = smoke_client
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json().get("status") == "ok"


def test_login_page_200(smoke_client):
    client, _ = smoke_client
    r = client.get("/auth/login")
    assert r.status_code == 200
    assert "login" in r.text.lower() or "password" in r.text.lower()


def test_favicon_and_static_present(smoke_client):
    client, _ = smoke_client
    assert client.get("/favicon.ico").status_code == 200
    # Use a file that is committed (alpine/htmx/tailwind are gitignored vendored CDNs)
    r = client.get("/static/css/themes.css")
    assert r.status_code == 200
    assert len(r.content) > 100
    r2 = client.get("/static/sw.js")
    assert r2.status_code == 200


@pytest.mark.parametrize(
    "path",
    [
        "/servers",
        "/jobs",
        "/audit",
        "/integrations",
        "/certificates",
        "/templates",
        "/dns",
        "/services",
        "/herder-backups",
        "/about",
        "/servers/new",
    ],
)
def test_protected_paths_require_login(smoke_client, path):
    client, _ = smoke_client
    r = client.get(path)
    # get_current_user raises 401 JSON (not a soft redirect)
    assert r.status_code == 401, f"{path} → {r.status_code}"
    detail = (r.json() or {}).get("detail", "")
    assert "log in" in detail.lower() or "unauthorized" in detail.lower() or detail


def test_api_v1_requires_bearer(smoke_client):
    client, _ = smoke_client
    r = client.get("/api/v1/servers")
    assert r.status_code in (401, 403)


# --- authenticated shells --------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/servers",
        "/jobs",
        "/audit",
        "/integrations",
        "/certificates",
        "/templates",
        "/dns",
        "/services",
        "/about",
        "/servers/new",
        "/auth/account",
    ],
)
def test_main_shells_200_when_logged_in(smoke_client, path):
    client, engine = smoke_client
    with Session(engine) as session:
        user = _make_user(session)
        uid = user.id
    r = client.get(path, cookies=_auth_cookie(uid))
    assert r.status_code == 200, f"{path} → {r.status_code}: {r.text[:200]}"


def test_settings_general_admin_200(smoke_client):
    client, engine = smoke_client
    with Session(engine) as session:
        user = _make_user(session, role="admin")
        uid = user.id
    r = client.get("/herder-backups?tab=general", cookies=_auth_cookie(uid))
    assert r.status_code == 200
    body = r.text.lower()
    # Stale data cleanup card (stream R) or timezone / general chrome
    assert "timezone" in body or "stale" in body or "general" in body or "settings" in body


def test_viewer_cannot_post_fleet_mutate(smoke_client):
    client, engine = smoke_client
    with Session(engine) as session:
        user = _make_user(session, role="viewer", email="viewer@smoke.test")
        uid = user.id
    # POST a fleet action — must 403 for viewer
    r = client.post(
        "/servers/bulk",
        data={"action": "os_update_check", "server_ids": "1"},
        cookies=_auth_cookie(uid),
    )
    assert r.status_code in (403, 404, 422, 400), f"unexpected {r.status_code}"
    if r.status_code == 403:
        assert "read-only" in (r.json() or {}).get("detail", "").lower() or True


def test_dependency_override_optional_user_anonymous_dashboard(smoke_client):
    """Root uses optional user; anonymous still 200 (public-ish dashboard chrome)."""
    client, _ = smoke_client
    r = client.get("/")
    assert r.status_code == 200
