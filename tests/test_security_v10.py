"""v1.0 AA security helpers — cookies, rate defaults, same-origin, weak secret."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.security.auth import (
    LOGIN_RATE_MAX,
    REGISTER_RATE_MAX,
    TWOFA_RATE_MAX,
    cookie_auth_kwargs,
    is_weak_secret_key,
    same_origin_request,
)


def test_rate_limits_tighter_than_legacy_defaults():
    # Production train: login/2FA tighter than the old 20/30 defaults
    assert LOGIN_RATE_MAX <= 12
    assert TWOFA_RATE_MAX <= 15
    assert REGISTER_RATE_MAX <= 10


def test_cookie_auth_kwargs_flags():
    kw = cookie_auth_kwargs(max_age=3600)
    assert kw["httponly"] is True
    assert kw["samesite"] == "lax"
    assert kw["path"] == "/"
    assert kw["max_age"] == 3600
    assert "secure" in kw


@pytest.mark.parametrize(
    "key,weak",
    [
        ("", True),
        ("short", True),
        ("dev-secret-change-in-prod", True),
        ("please-change-me-now", True),
        ("x" * 48, False),
        ("a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6", False),
    ],
)
def test_is_weak_secret_key(key, weak):
    assert is_weak_secret_key(key) is weak


def _make_request(headers: dict[str, str], host: str = "piherder.example.com") -> Request:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": "/auth/login",
        "raw_path": b"/auth/login",
        "query_string": b"",
        "headers": [
            (b"host", host.encode()),
            *[(k.lower().encode(), v.encode()) for k, v in headers.items()],
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("test", 443),
    }
    return Request(scope)


def test_same_origin_ok_matching_origin():
    req = _make_request({"origin": "https://piherder.example.com"})
    assert same_origin_request(req) is True


def test_same_origin_ok_matching_referer():
    req = _make_request({"referer": "https://piherder.example.com/auth/login"})
    assert same_origin_request(req) is True


def test_same_origin_rejects_cross_origin():
    req = _make_request({"origin": "https://evil.example"})
    assert same_origin_request(req) is False


def test_same_origin_rejects_cross_referer():
    req = _make_request({"referer": "https://evil.example/phish"})
    assert same_origin_request(req) is False


def test_same_origin_allows_missing_headers():
    req = _make_request({})
    assert same_origin_request(req) is True


def test_same_origin_middleware_blocks_cross_origin_post():
    from app.main import SameOriginPostMiddleware

    app = FastAPI()
    app.add_middleware(SameOriginPostMiddleware)

    @app.post("/probe")
    def probe():
        return {"ok": True}

    client = TestClient(app)
    # Matching origin
    r = client.post(
        "/probe",
        headers={"Host": "testserver", "Origin": "http://testserver"},
    )
    assert r.status_code == 200
    # Cross origin
    r2 = client.post(
        "/probe",
        headers={"Host": "testserver", "Origin": "https://evil.example"},
    )
    assert r2.status_code == 403


def test_same_origin_middleware_skips_api_v1_bearer():
    from app.main import SameOriginPostMiddleware

    app = FastAPI()
    app.add_middleware(SameOriginPostMiddleware)

    @app.post("/api/v1/servers")
    def api():
        return {"ok": True}

    client = TestClient(app)
    r = client.post(
        "/api/v1/servers",
        headers={
            "Host": "testserver",
            "Origin": "https://evil.example",
            "Authorization": "Bearer ph_test",
        },
    )
    # Middleware allows through; route returns 200 (no real auth in this mini-app)
    assert r.status_code == 200
