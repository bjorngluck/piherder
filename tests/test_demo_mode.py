"""v1.2 Stream D — PIHERDER_DEMO_MODE gates and canned jobs."""
from __future__ import annotations

import json

import pytest
from fastapi import BackgroundTasks, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.main import app
from app.models import Server, User
from app.security.auth import create_access_token, get_password_hash
from app.services import api_tokens as tok_svc
from app.services import demo as demo_svc
from app.services import jobs as job_svc
from app.services import ssh_console as cons


@pytest.fixture()
def demo_on(monkeypatch):
    monkeypatch.setattr(demo_svc.settings, "PIHERDER_DEMO_MODE", True)
    yield
    monkeypatch.setattr(demo_svc.settings, "PIHERDER_DEMO_MODE", False)


@pytest.fixture()
def demo_off(monkeypatch):
    monkeypatch.setattr(demo_svc.settings, "PIHERDER_DEMO_MODE", False)


def test_demo_mode_flag(demo_off, demo_on, monkeypatch):
    monkeypatch.setattr(demo_svc.settings, "PIHERDER_DEMO_MODE", False)
    assert demo_svc.demo_mode() is False
    assert demo_svc.reject_if_demo("wizard") is None
    monkeypatch.setattr(demo_svc.settings, "PIHERDER_DEMO_MODE", True)
    assert demo_svc.demo_mode() is True
    msg = demo_svc.reject_if_demo("wizard")
    assert msg and "demo" in msg.lower()
    assert "Demo —" in demo_svc.demo_banner()


def test_raise_and_http_403(demo_on):
    with pytest.raises(demo_svc.DemoBlocked) as ei:
        demo_svc.raise_if_demo("api_token")
    assert "token" in ei.value.message.lower()
    with pytest.raises(HTTPException) as he:
        demo_svc.http_403_if_demo("nmap")
    assert he.value.status_code == 403


def test_shared_sandbox_locks(demo_on):
    assert "shared" in (demo_svc.reject_if_demo("shared_account") or "").lower()
    assert "operator" in (demo_svc.reject_if_demo("seed_restore") or "").lower()
    assert "user" in (demo_svc.reject_if_demo("user_admin") or "").lower()
    assert "password reset" in (demo_svc.reject_if_demo("password_reset") or "").lower()
    assert "sso" in (demo_svc.reject_if_demo("sso") or "").lower()
    assert "settings" in (demo_svc.reject_if_demo("settings_write") or "").lower()
    redir = demo_svc.redirect_if_demo("/auth/account")
    assert redir is not None
    assert redir.status_code == 303
    assert "demo_locked" in (redir.headers.get("location") or "")
    with pytest.raises(HTTPException) as he:
        demo_svc.http_403_if_demo("seed_restore")
    assert he.value.status_code == 403


def test_demo_write_guard_allowlist(demo_on, monkeypatch):
    # Safe methods always ok
    assert demo_svc.demo_write_allowed("GET", "/integrations/new/pihole") is True
    # Login / canned jobs / notifications
    assert demo_svc.demo_write_allowed("POST", "/auth/login") is True
    assert demo_svc.demo_write_allowed("POST", "/auth/2fa") is True
    assert demo_svc.demo_write_allowed("POST", "/auth/2fa/webauthn/options") is True
    assert demo_svc.demo_write_allowed("POST", "/servers/3/run/backup") is True
    assert demo_svc.demo_write_allowed("POST", "/jobs/9/cancel") is True
    assert demo_svc.demo_write_allowed("POST", "/notifications/1/dismiss") is True
    assert demo_svc.demo_write_allowed("POST", "/account/favourites/toggle") is True
    # Connectors / fleet config blocked
    assert demo_svc.demo_write_allowed("POST", "/integrations/new/pihole") is False
    assert demo_svc.demo_write_allowed("POST", "/integrations/new/generic") is False
    assert demo_svc.demo_write_allowed("POST", "/integrations/5/edit") is False
    assert demo_svc.demo_write_allowed("POST", "/dns/services") is False
    assert demo_svc.demo_write_allowed("POST", "/templates/new") is False
    assert demo_svc.demo_write_allowed("POST", "/certificates/upload") is False
    assert demo_svc.demo_write_allowed("POST", "/auth/account/password") is False
    assert demo_svc.demo_write_allowed("POST", "/herder-backups/oidc") is False
    # No new accounts
    assert demo_svc.demo_write_allowed("POST", "/auth/register") is False
    assert demo_svc.demo_write_allowed("POST", "/auth/users/create") is False
    # Off when not demo
    monkeypatch.setattr(demo_svc.settings, "PIHERDER_DEMO_MODE", False)
    assert demo_svc.demo_write_allowed("POST", "/integrations/new/pihole") is True


def test_console_disabled_in_demo(demo_on, monkeypatch):
    monkeypatch.setattr(cons.settings, "PIHERDER_SSH_CONSOLE", True)
    assert cons.console_enabled() is False
    with pytest.raises(cons.ConsoleDisabled):
        cons.require_enabled()


def test_audit_client_ip_scrubbed_in_demo(demo_on, monkeypatch):
    """Shared demo must not store or show real visitor IPs in Audit."""
    from datetime import datetime

    from app.services import audit_write as aw
    from app.services import request_ip as rip

    # Storage: any real IP → redacted
    assert (
        demo_svc.scrub_audit_client_ip("203.0.113.50", for_display=False)
        == demo_svc.DEMO_AUDIT_IP_PLACEHOLDER
    )
    # Display: public IPs scrubbed; seed/private lab IPs kept
    assert (
        demo_svc.scrub_audit_client_ip("203.0.113.50", for_display=True)
        == demo_svc.DEMO_AUDIT_IP_PLACEHOLDER
    )
    assert demo_svc.scrub_audit_client_ip("10.42.0.99", for_display=True) == "10.42.0.99"
    assert demo_svc.scrub_audit_client_ip(None, for_display=True) is None

    tok = rip.set_request_client_ip("198.51.100.7")
    try:
        al = aw.make_audit_log(
            action="user_login",
            status="success",
            user_id=1,
            finished_at=datetime.utcnow(),
        )
        assert al.client_ip == demo_svc.DEMO_AUDIT_IP_PLACEHOLDER
    finally:
        rip.reset_request_client_ip(tok)

    # Off demo: real IP kept
    monkeypatch.setattr(demo_svc.settings, "PIHERDER_DEMO_MODE", False)
    assert demo_svc.scrub_audit_client_ip("203.0.113.50") == "203.0.113.50"
    tok = rip.set_request_client_ip("203.0.113.9")
    try:
        al = aw.make_audit_log(action="user_login", status="success")
        assert al.client_ip == "203.0.113.9"
    finally:
        rip.reset_request_client_ip(tok)


def test_audit_ip_passthrough_when_not_demo(demo_off):
    assert demo_svc.scrub_audit_client_ip("1.2.3.4", for_display=True) == "1.2.3.4"
    assert demo_svc.scrub_audit_client_ip("1.2.3.4", for_display=False) == "1.2.3.4"


def test_create_api_token_blocked(demo_on, tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'tok.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        user = User(
            email="admin@demo.test",
            hashed_password=get_password_hash("DemoPass1ok"),
            role="admin",
            is_active=True,
            must_change_password=False,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        with pytest.raises(demo_svc.DemoBlocked):
            tok_svc.create_api_token(session, name="x", created_by=user)


def test_canned_job_success(demo_on, tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'job.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        user = User(
            email="admin@demo.test",
            hashed_password=get_password_hash("DemoPass1ok"),
            role="admin",
            is_active=True,
            must_change_password=False,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        srv = Server(
            name="lab-core.demo",
            hostname="lab-core.demo",
            ip_address="10.0.0.1",
            ssh_username="pi",
            ssh_port=22,
        )
        session.add(srv)
        session.commit()
        session.refresh(srv)

        bg = BackgroundTasks()
        job = job_svc.create_job_and_run(
            bg,
            session,
            srv,
            "os_update_check",
            user_id=user.id,
        )
        assert job.status == "success"
        assert job.finished_at is not None
        details = json.loads(job.details or "{}")
        assert details.get("demo") is True
        assert "Demo simulation" in (details.get("summary") or "")


@pytest.fixture()
def demo_http(tmp_path, monkeypatch):
    monkeypatch.setattr(demo_svc.settings, "PIHERDER_DEMO_MODE", True)
    # Avoid host/app settings DB forcing /auth/force-2fa mid-request
    monkeypatch.setattr("app.security.auth.force_2fa_required", lambda: False)

    engine = create_engine(
        f"sqlite:///{tmp_path / 'http.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def _session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    client = TestClient(app, raise_server_exceptions=False)
    with Session(engine) as s:
        user = User(
            email="demo@demo.test",
            hashed_password=get_password_hash("DemoPass1ok"),
            role="admin",
            is_active=True,
            must_change_password=False,
            totp_enabled=True,
        )
        s.add(user)
        s.commit()
        s.refresh(user)
        uid = user.id

    try:
        yield client, uid
    finally:
        app.dependency_overrides.clear()
        monkeypatch.setattr(demo_svc.settings, "PIHERDER_DEMO_MODE", False)


def test_login_shows_demo_banner(demo_http):
    client, _ = demo_http
    r = client.get("/auth/login")
    assert r.status_code == 200
    assert "demo-banner" in r.text or "Demo —" in r.text


def test_wizard_blocked(demo_http):
    client, uid = demo_http
    token = create_access_token({"sub": str(uid)})
    r = client.get(
        "/servers/new",
        cookies={"access_token": token},
        follow_redirects=False,
    )
    assert r.status_code in (303, 302)
    loc = r.headers.get("location") or ""
    assert "/servers" in loc
    assert "demo" in loc.lower() or "error" in loc.lower()


def test_api_bearer_forbidden(demo_http):
    client, _ = demo_http
    r = client.get("/api/v1/servers", headers={"Authorization": "Bearer ph_fake"})
    assert r.status_code == 403
    body = r.json()
    detail = body.get("detail") or ""
    assert "demo" in detail.lower()
