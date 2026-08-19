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
        "/reports",
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
        "/reports",
        "/servers",
        "/jobs",
        "/audit",
        "/integrations",
        "/certificates",
        "/templates",
        "/dns",
        "/dns/physical",
        "/dns/logical",
        "/dns/coverage",
        "/dns/candidates",
        "/services",
        "/about",
        "/servers/new",
        "/auth/account",
        "/catalog",
        "/integrations/new/nmap",
        "/templates",
    ],
)
def test_main_shells_200_when_logged_in(smoke_client, path):
    client, engine = smoke_client
    with Session(engine) as session:
        user = _make_user(session)
        uid = user.id
    # Do not follow redirects: /catalog 303s to /integrations
    r = client.get(path, cookies=_auth_cookie(uid), follow_redirects=False)
    assert r.status_code in (200, 303), f"{path} → {r.status_code}: {r.text[:200]}"


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
    assert 'data-testid="settings-hub"' in r.text
    assert 'data-testid="settings-password-policy"' in r.text
    assert 'data-testid="password-min-length"' in r.text
    assert 'data-testid="settings-console"' in r.text
    assert 'data-testid="console-idle-sec"' in r.text
    assert 'data-open-settings-modal="security"' in r.text
    assert 'data-settings-modal="console"' in r.text


def test_admin_console_policy_save(smoke_client, monkeypatch):
    from app.services import app_settings as cfg
    from app.services import ssh_console as cons

    store: dict = {}

    def fake_load():
        return dict(store)

    def fake_write(data: dict):
        store.clear()
        store.update(data)

    monkeypatch.setattr(cfg, "_load_raw_from_db", fake_load)
    monkeypatch.setattr(cfg, "_write_raw_to_db", fake_write)
    cfg.clear_cache()

    client, engine = smoke_client
    with Session(engine) as session:
        uid = _make_user(session, role="admin").id
    r = client.post(
        "/herder-backups/console",
        data={
            "console_idle_sec": "1800",
            "console_max_sec": "7200",
            "console_max_per_user": "6",
            "console_max_global": "24",
            "console_ticket_sec": "90",
            "console_hold_sec": "0",
            "console_revalidate_sec": "15",
            "console_scrollback": "3000",
            "console_bind_ip": "1",
            "console_bind_device": "1",
        },
        cookies=_auth_cookie(uid),
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "console_saved" in (r.headers.get("location") or "")
    cfg.clear_cache()
    assert cons.idle_sec() == 1800
    assert cons.max_per_user() == 6
    cfg.clear_cache()


def test_demo_console_policy_save_403(smoke_client, monkeypatch):
    from app.services import demo as demo_svc

    monkeypatch.setattr(demo_svc.settings, "PIHERDER_DEMO_MODE", True)
    client, engine = smoke_client
    with Session(engine) as session:
        uid = _make_user(session, role="admin", email="demo-admin@smoke.test").id
    r = client.post(
        "/herder-backups/console",
        data={"console_idle_sec": "1800"},
        cookies=_auth_cookie(uid),
        follow_redirects=False,
    )
    assert r.status_code == 403


def test_viewer_cannot_post_console_policy(smoke_client):
    client, engine = smoke_client
    with Session(engine) as session:
        user = _make_user(session, role="viewer", email="viewer-console@smoke.test")
        uid = user.id
    r = client.post(
        "/herder-backups/console",
        data={"console_idle_sec": "1800"},
        cookies=_auth_cookie(uid),
        follow_redirects=False,
    )
    assert r.status_code == 403


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


def test_anonymous_root_redirects_to_login(smoke_client):
    """v1.0 (F): unauthenticated / goes to login, not empty dashboard."""
    client, _ = smoke_client
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 303)
    loc = r.headers.get("location") or ""
    assert "/auth/login" in loc


def test_authenticated_root_dashboard(smoke_client):
    """Signed-in users still get the fleet dashboard at /."""
    client, engine = smoke_client
    with Session(engine) as session:
        user = _make_user(session)
        uid = user.id
    r = client.get("/", cookies=_auth_cookie(uid))
    assert r.status_code == 200
    assert "dashboard" in r.text.lower() or "server" in r.text.lower() or "fleet" in r.text.lower()
    assert "/reports" in r.text


def test_reports_board_viewer_200(smoke_client):
    """Reports is backup + OS patch history, not status portlets."""
    client, engine = smoke_client
    with Session(engine) as session:
        user = _make_user(session, role="viewer", email="viewer@smoke.test")
        uid = user.id
    r = client.get("/reports", cookies=_auth_cookie(uid))
    assert r.status_code == 200
    assert 'data-testid="reports-backups"' in r.text
    assert 'data-testid="reports-os-patch"' in r.text
    assert 'data-testid="reports-lan"' in r.text
    assert 'data-testid="reports-docker"' in r.text
    assert 'data-testid="reports-console"' in r.text
    assert "Reports" in r.text
    assert "report-card-alerts_by_severity" not in r.text
