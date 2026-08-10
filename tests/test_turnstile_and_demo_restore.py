"""Turnstile helpers + demo restore gate."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi import BackgroundTasks
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import Server, User
from app.security.auth import get_password_hash
from app.services import demo as demo_svc
from app.services import demo_seed as seed
from app.services import turnstile as ts
from app.security import headers as hdr


def test_turnstile_disabled_when_keys_empty(monkeypatch):
    monkeypatch.setattr(ts.settings, "PIHERDER_TURNSTILE_SITE_KEY", "")
    monkeypatch.setattr(ts.settings, "PIHERDER_TURNSTILE_SECRET_KEY", "")
    assert ts.turnstile_enabled() is False
    ok, code = ts.verify_turnstile_token(None)
    assert ok is True and code == ""


def test_turnstile_requires_token_when_enabled(monkeypatch):
    monkeypatch.setattr(ts.settings, "PIHERDER_TURNSTILE_SITE_KEY", "site")
    monkeypatch.setattr(ts.settings, "PIHERDER_TURNSTILE_SECRET_KEY", "secret")
    assert ts.turnstile_enabled() is True
    ok, code = ts.verify_turnstile_token("")
    assert ok is False
    assert code == "missing-input-response"


def test_turnstile_verify_success(monkeypatch):
    monkeypatch.setattr(ts.settings, "PIHERDER_TURNSTILE_SITE_KEY", "site")
    monkeypatch.setattr(ts.settings, "PIHERDER_TURNSTILE_SECRET_KEY", "secret")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"success": True}

    with patch("app.services.turnstile.httpx.Client") as client_cls:
        client = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)
        client.post.return_value = mock_resp
        client_cls.return_value = client
        ok, code = ts.verify_turnstile_token("tok", remoteip="1.2.3.4")
    assert ok is True
    assert code == ""
    client.post.assert_called_once()


def test_turnstile_http_400_with_json_is_not_unreachable(monkeypatch):
    """CF returns HTTP 400 + JSON for invalid-input-secret — not a network outage."""
    monkeypatch.setattr(ts.settings, "PIHERDER_TURNSTILE_SITE_KEY", "site")
    monkeypatch.setattr(ts.settings, "PIHERDER_TURNSTILE_SECRET_KEY", "bad-secret")

    mock_resp = MagicMock()
    mock_resp.status_code = 400
    mock_resp.json.return_value = {
        "success": False,
        "error-codes": ["invalid-input-secret"],
    }

    with patch("app.services.turnstile.httpx.Client") as client_cls:
        client = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)
        client.post.return_value = mock_resp
        client_cls.return_value = client
        ok, code = ts.verify_turnstile_token("tok", remoteip="1.2.3.4")
    assert ok is False
    assert code == "invalid-input-secret"


def test_turnstile_transport_error_is_unreachable(monkeypatch):
    monkeypatch.setattr(ts.settings, "PIHERDER_TURNSTILE_SITE_KEY", "site")
    monkeypatch.setattr(ts.settings, "PIHERDER_TURNSTILE_SECRET_KEY", "secret")

    with patch("app.services.turnstile.httpx.Client") as client_cls:
        client = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)
        client.post.side_effect = httpx.ConnectError("dns fail")
        client_cls.return_value = client
        with patch("app.services.turnstile.time.sleep"):
            ok, code = ts.verify_turnstile_token("tok", remoteip="1.2.3.4")
    assert ok is False
    assert code == "verify-unreachable"


def test_csp_includes_turnstile_when_on(monkeypatch):
    monkeypatch.setattr(hdr.settings, "PIHERDER_TURNSTILE_SITE_KEY", "site")
    monkeypatch.setattr(hdr.settings, "PIHERDER_TURNSTILE_SECRET_KEY", "secret")
    policy = hdr.build_csp()
    assert "challenges.cloudflare.com" in policy


def test_csp_no_turnstile_when_off(monkeypatch):
    monkeypatch.setattr(hdr.settings, "PIHERDER_TURNSTILE_SITE_KEY", "")
    monkeypatch.setattr(hdr.settings, "PIHERDER_TURNSTILE_SECRET_KEY", "")
    policy = hdr.build_csp()
    assert "challenges.cloudflare.com" not in policy


def test_force_reseed_wipes_and_rebuilds(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.app_settings.save_settings", lambda p: p)
    monkeypatch.setattr("app.services.app_settings.load_settings", lambda: {})
    engine = create_engine(
        f"sqlite:///{tmp_path / 'r.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        seed.seed_demo_fleet(session, force=True, password="a", email="a@b.c")
        assert len(list(session.exec(select(Server)).all())) == 6
        # mutate
        s = session.exec(select(Server)).first()
        s.name = "trashed"
        session.add(s)
        session.commit()
        seed.seed_demo_fleet(session, force=True, password="a", email="a@b.c")
        names = {x.name for x in session.exec(select(Server)).all()}
        assert "trashed" not in names
        assert "lab-core" in names
