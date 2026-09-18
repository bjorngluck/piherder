"""Expired session: HTML/HTMX go to login; APIs stay JSON 401."""
from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine
from starlette.requests import Request

from app.database import get_session
from app.main import app
from app.models import User
from app.security.auth import (
    LOGIN_PATH,
    LOGIN_REQUIRED_DETAIL,
    create_access_token,
    get_password_hash,
    login_required_http_response,
    login_required_kind,
)


def _request(path: str = "/reports", headers: dict[str, str] | None = None) -> Request:
    hdrs = [
        (k.lower().encode("latin-1"), v.encode("latin-1"))
        for k, v in (headers or {}).items()
    ]
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": hdrs,
            "client": ("test", 123),
            "server": ("test", 80),
        }
    )


def test_kind_default_json():
    assert login_required_kind(_request()) == "json"


def test_kind_html_accept_redirects():
    assert (
        login_required_kind(
            _request(headers={"accept": "text/html,application/xhtml+xml"})
        )
        == "redirect"
    )


def test_kind_sec_fetch_document_redirects():
    assert (
        login_required_kind(
            _request(headers={"sec-fetch-dest": "document", "sec-fetch-mode": "navigate"})
        )
        == "redirect"
    )


def test_kind_htmx_uses_hx_redirect():
    assert (
        login_required_kind(
            _request(headers={"hx-request": "true", "accept": "text/html"})
        )
        == "hx"
    )


def test_kind_json_accept_stays_json():
    assert (
        login_required_kind(_request(headers={"accept": "application/json"})) == "json"
    )


def test_kind_api_v1_stays_json_even_with_html():
    assert (
        login_required_kind(
            _request("/api/v1/servers", headers={"accept": "text/html"})
        )
        == "json"
    )


def test_html_response_is_303_login():
    resp = login_required_http_response(
        _request(headers={"accept": "text/html"})
    )
    assert resp.status_code == 303
    assert resp.headers.get("location") == LOGIN_PATH
    assert "json" not in (resp.headers.get("content-type") or "").lower()


def test_hx_response_sets_hx_redirect():
    resp = login_required_http_response(
        _request(headers={"hx-request": "true", "accept": "text/html"})
    )
    assert resp.status_code == 401
    assert resp.headers.get("hx-redirect") == LOGIN_PATH


def test_json_response_keeps_detail():
    resp = login_required_http_response(_request())
    assert resp.status_code == 401
    assert resp.body
    assert LOGIN_REQUIRED_DETAIL.encode() in resp.body


@pytest.fixture()
def login_client(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'login-redir.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def _session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield client, engine
    finally:
        app.dependency_overrides.clear()


def _user(session: Session) -> User:
    user = User(
        email="redir@test.example",
        hashed_password=get_password_hash("Redirect1ok"),
        role="admin",
        is_active=True,
        must_change_password=False,
        totp_enabled=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def test_browser_page_redirects_to_login(login_client):
    client, _ = login_client
    r = client.get(
        "/reports",
        headers={"Accept": "text/html"},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text[:200]
    assert r.headers.get("location") == LOGIN_PATH
    assert "Please log in" not in r.text


def test_expired_cookie_html_redirects_to_login(login_client):
    client, engine = login_client
    with Session(engine) as session:
        uid = _user(session).id
    token = create_access_token(
        {"sub": str(uid)}, expires_delta=timedelta(seconds=-5)
    )
    r = client.get(
        "/servers",
        cookies={"access_token": token},
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text[:200]
    assert r.headers.get("location") == LOGIN_PATH


def test_htmx_gets_hx_redirect(login_client):
    client, _ = login_client
    r = client.get(
        "/jobs",
        headers={"HX-Request": "true", "Accept": "text/html"},
        follow_redirects=False,
    )
    assert r.status_code == 401
    assert r.headers.get("hx-redirect") == LOGIN_PATH


def test_api_probe_still_json_401(login_client):
    client, _ = login_client
    r = client.get("/reports")
    assert r.status_code == 401
    assert r.json().get("detail") == LOGIN_REQUIRED_DETAIL


def test_api_v1_html_accept_stays_json(login_client):
    client, _ = login_client
    r = client.get("/api/v1/servers", headers={"Accept": "text/html"})
    assert r.status_code in (401, 403)
    assert "detail" in r.json()
