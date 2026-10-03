"""MCP OAuth: discovery, consent, pho_ access token, and refresh."""
from __future__ import annotations

import base64
import hashlib
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient


def _client(tmp_path, monkeypatch):
    from sqlalchemy.pool import StaticPool
    from sqlmodel import Session, SQLModel, create_engine

    from app.database import get_session
    from app.main import app
    from app.models import User
    from app.security.auth import create_user_access_token, get_password_hash

    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    monkeypatch.setattr("app.services.demo.reject_if_demo", lambda *_a, **_k: None)
    monkeypatch.setattr("app.services.demo.raise_if_demo", lambda *_a, **_k: None)
    monkeypatch.setattr("app.services.account_stepup.force_2fa_applies", lambda *_a, **_k: False)
    engine = create_engine(
        f"sqlite:///{tmp_path / 'mcp-oauth.db'}",
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
        admin = User(
            email="oauth-admin@test.local",
            hashed_password=get_password_hash("SmokeTest1ok"),
            role="admin",
            is_active=True,
            must_change_password=False,
            totp_enabled=False,
        )
        viewer = User(
            email="oauth-viewer@test.local",
            hashed_password=get_password_hash("SmokeTest1ok"),
            role="viewer",
            is_active=True,
            must_change_password=False,
            totp_enabled=False,
        )
        s.add(admin)
        s.add(viewer)
        s.commit()
        s.refresh(admin)
        s.refresh(viewer)
        admin_cookie = create_user_access_token(admin)
        viewer_cookie = create_user_access_token(viewer)
    return client, app, admin_cookie, viewer_cookie


def _pkce(verifier: str = "a" * 50) -> tuple[str, str]:
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def _auth(cookie: str) -> dict[str, str]:
    return {"Cookie": f"access_token={cookie}"}


def test_missing_token_advertises_oauth_and_a_bad_ph_token_does_not(tmp_path, monkeypatch):
    client, app, _admin, _viewer = _client(tmp_path, monkeypatch)
    try:
        meta = client.get("/.well-known/oauth-protected-resource")
        assert meta.status_code == 200
        body = meta.json()
        assert body["resource"].endswith("/mcp")
        assert body["authorization_servers"]
        server = client.get("/.well-known/oauth-authorization-server")
        doc = server.json()
        assert doc["code_challenge_methods_supported"] == ["S256"]
        assert doc["registration_endpoint"].endswith("/mcp/oauth/register")

        missing = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers={"Accept": "application/json, text/event-stream", "Content-Type": "application/json"},
        )
        assert missing.status_code == 401
        assert "resource_metadata" in (missing.headers.get("www-authenticate") or "")

        bad = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                "Authorization": "Bearer ph_notarealtokenvalue000000000000000",
            },
        )
        assert bad.status_code == 401
        assert "resource_metadata" not in (bad.headers.get("www-authenticate") or "").lower()

        stale = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                "Authorization": "Bearer ph_oa_notarealtokenvalue000000000000000",
            },
        )
        assert stale.status_code == 401
        assert "invalid_token" in (stale.headers.get("www-authenticate") or "")
    finally:
        app.dependency_overrides.clear()


def test_agent_signs_in_and_a_read_token_cannot_trigger_jobs(tmp_path, monkeypatch):
    client, app, admin_cookie, viewer_cookie = _client(tmp_path, monkeypatch)
    verifier, challenge = _pkce()
    redirect = "http://127.0.0.1:9/callback"
    try:
        rejected = client.post(
            "/mcp/oauth/register",
            json={"client_name": "Bad", "redirect_uris": ["http://evil.example/cb"]},
        )
        assert rejected.status_code == 400

        reg = client.post(
            "/mcp/oauth/register",
            json={"client_name": "Cursor", "redirect_uris": [redirect]},
        )
        assert reg.status_code == 201
        client_id = reg.json()["client_id"]

        anon = client.get(
            "/mcp/oauth/authorize",
            params={
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": "st",
                "scope": "read jobs",
            },
            follow_redirects=False,
        )
        assert anon.status_code == 303
        assert anon.headers["location"].endswith("/auth/login")
        assert "ph_mcp_oauth_return" in anon.headers.get("set-cookie", "")

        viewer = client.get(
            "/mcp/oauth/authorize",
            params={
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": "st",
                "scope": "read",
            },
            headers=_auth(viewer_cookie),
        )
        assert viewer.status_code == 403

        page = client.get(
            "/mcp/oauth/authorize",
            params={
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": "st",
                "scope": "read jobs",
                "resource": "http://testserver/mcp",
            },
            headers=_auth(admin_cookie),
        )
        assert page.status_code == 200
        assert "jobs" in page.text
        consent_cookie = page.cookies.get("ph_mcp_oauth_consent")
        assert consent_cookie

        # Ask only for read. A tampered jobs checkbox must not stick,
        # because this second client registered read-only... the first client
        # requested jobs, so unchecking jobs is the scope cut.
        allowed = client.post(
            "/mcp/oauth/consent",
            data={"decision": "allow", "scope": "read"},
            cookies={
                "access_token": admin_cookie,
                "ph_mcp_oauth_consent": consent_cookie,
            },
            follow_redirects=False,
        )
        assert allowed.status_code == 303, allowed.text
        assert allowed.headers["location"].endswith("/mcp/oauth/continue")
        go = client.get("/mcp/oauth/continue", cookies=allowed.cookies, follow_redirects=False)
        assert go.status_code == 200
        start = go.text.find('url=')
        target = go.text[start + 4 : go.text.find('"', start)]
        parsed = urlparse(target.replace("&amp;", "&"))
        qs = parse_qs(parsed.query)
        assert qs["state"] == ["st"]
        code = qs["code"][0]

        wrong = client.post(
            "/mcp/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": client_id,
                "redirect_uri": redirect,
                "code_verifier": "b" * 50,
            },
        )
        assert wrong.status_code == 400

        # Code was burned by the failed PKCE check. Sign in again.
        page = client.get(
            "/mcp/oauth/authorize",
            params={
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": "st2",
                "scope": "read jobs",
            },
            headers=_auth(admin_cookie),
        )
        consent_cookie = page.cookies.get("ph_mcp_oauth_consent")
        allowed = client.post(
            "/mcp/oauth/consent",
            data={"decision": "allow", "scope": "read"},
            cookies={
                "access_token": admin_cookie,
                "ph_mcp_oauth_consent": consent_cookie,
            },
            follow_redirects=False,
        )
        assert allowed.status_code == 303, allowed.text
        go = client.get("/mcp/oauth/continue", cookies=allowed.cookies)
        start = go.text.find("url=")
        target = go.text[start + 4 : go.text.find('"', start)]
        code = parse_qs(urlparse(target.replace("&amp;", "&")).query)["code"][0]
        traded = client.post(
            "/mcp/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": client_id,
                "redirect_uri": redirect,
                "code_verifier": verifier,
            },
        )
        assert traded.status_code == 200
        token = traded.json()
        assert token["access_token"].startswith("ph_oa_")
        assert token["refresh_token"].startswith("mcr_")
        assert token["scope"] == "read"
        again = client.post(
            "/mcp/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": client_id,
                "redirect_uri": redirect,
                "code_verifier": verifier,
            },
        )
        assert again.status_code == 400

        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token['access_token']}",
        }
        listed = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers=headers,
        )
        assert listed.status_code == 200, listed.text
        names = {tool["name"] for tool in listed.json()["result"]["tools"]}
        assert "summary" in names
        assert "trigger_job" not in names
        for refused in (
            "move",
            "undo",
            "nmap",
            "console",
            "docker_stack_down",
            "docker_stack_remove",
        ):
            assert refused not in names

        refreshed = client.post(
            "/mcp/oauth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": token["refresh_token"],
                "client_id": client_id,
            },
        )
        assert refreshed.status_code == 200
        new_access = refreshed.json()["access_token"]
        assert new_access != token["access_token"]
        old = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
            headers={**headers, "Authorization": f"Bearer {token['access_token']}"},
        )
        assert old.status_code == 401
        live = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
            headers={**headers, "Authorization": f"Bearer {new_access}"},
        )
        assert live.status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_login_returns_to_the_agent_consent(tmp_path, monkeypatch):
    from app.security.auth import post_login_path

    _client_bundle = _client(tmp_path, monkeypatch)
    app = _client_bundle[1]
    from starlette.requests import Request

    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": [(b"cookie", b"ph_mcp_oauth_return=/mcp/oauth/authorize?client_id=abc")],
            "client": ("127.0.0.1", 9),
            "server": ("test", 80),
        }
    )

    class _User:
        must_change_password = False
        totp_enabled = True
        id = 1
        role = "admin"
        is_active = True

    try:
        assert post_login_path(_User(), None, request) == "/mcp/oauth/authorize?client_id=abc"
    finally:
        app.dependency_overrides.clear()
