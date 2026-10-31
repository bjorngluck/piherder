"""v1.14 coverage for Grafana and NPM integration handlers."""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from sqlmodel import Session
from starlette.requests import Request

from app.database import get_engine
from app.models import Integration, Server, User
from app.routers import integrations_grafana as grafana
from app.routers import integrations_npm as npm
from app.security.auth import get_password_hash
from app.services.integrations import registry as reg


def _run(coro):
    return asyncio.run(coro)


def _request(query: str = "tab=inventory&mode=clone&kind=logs&server_id=1") -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": "/integrations/1",
        "raw_path": b"/integrations/1",
        "query_string": query.encode(),
        "headers": [(b"host", b"example.com")],
        "client": ("192.0.2.10", 443),
        "server": ("example.com", 443),
        "root_path": "",
    }
    return Request(scope)


def test_grafana_and_npm_handlers(monkeypatch):
    row = Integration(name="Grafana", type="grafana", base_url="https://grafana.example.com", config_json="{}")

    def _create(*_a, **_k):
        row.id = 7
        return row

    monkeypatch.setattr(reg, "create_grafana", _create)
    monkeypatch.setattr("app.services.integrations.poll.poll_integration", lambda *_a, **_k: None)
    with Session(get_engine()) as session:
        user = User(
            email="integrations@example.com",
            hashed_password=get_password_hash("SmokeTest1ok"),
            role="admin",
            is_active=True,
            must_change_password=False,
            totp_enabled=False,
        )
        server = Server(name="Host A", hostname="host-a.example.com", ssh_username="user")
        session.add(user)
        session.add(server)
        integration = Integration(
            name="NPM",
            type=reg.TYPE_NPM,
            base_url="https://npm.example.com",
            config_json="{}",
        )
        session.add(integration)
        session.commit()
        session.refresh(user)
        session.refresh(server)
        session.refresh(integration)

        created = _run(
            grafana.grafana_create(
                session, user, "Grafana", "https://grafana.example.com", "key-example",
                60, "on", "on", "", "", "", "",
            )
        )
        assert created.status_code == 303
        monkeypatch.setattr(reg, "create_grafana", lambda *_a, **_k: (_ for _ in ()).throw(ValueError("url")))
        bad = _run(
            grafana.grafana_create(
                session, user, "Grafana", "not-a-url", "", 60, None, "on", "", "", "", "",
            )
        )
        assert "invalid" in bad.headers["location"]
        monkeypatch.setattr(reg, "create_grafana", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("db")))
        failed = _run(
            grafana.grafana_create(
                session, user, "Grafana", "https://grafana.example.com", "", 60, None, "on", "", "", "", "",
            )
        )
        assert "save_failed" in failed.headers["location"]

        monkeypatch.setattr(reg, "list_bindings", lambda *_a, **_k: [])
        monkeypatch.setattr(reg, "dashboards_from_cache", lambda *_a, **_k: [{"uid": "abc", "title": "Home", "url": "https://grafana.example.com"}])
        monkeypatch.setattr(reg, "parse_last_status", lambda *_a, **_k: {"proxy_hosts": [{"id": 4, "label": "app"}]})
        monkeypatch.setattr(reg, "preferred_display_names", lambda *_a, **_k: {"abc": "Home", "old": "Gone"})
        monkeypatch.setattr(reg, "docker_inventory_options", lambda *_a, **_k: [])
        monkeypatch.setattr(reg, "has_credentials", lambda *_a, **_k: True)
        monkeypatch.setattr(reg, "poll_interval_sec", lambda *_a, **_k: 60)
        monkeypatch.setattr(reg, "tls_verify", lambda *_a, **_k: True)
        monkeypatch.setattr(reg, "query_template", lambda *_a, **_k: "")
        monkeypatch.setattr(reg, "query_template_container_host", lambda *_a, **_k: "")
        monkeypatch.setattr(reg, "query_template_container", lambda *_a, **_k: "")
        monkeypatch.setattr(reg, "query_template_logs", lambda *_a, **_k: "")
        monkeypatch.setattr("app.services.integrations.grafana.open_grafana_url", lambda *_a, **_k: "https://grafana.example.com")
        page = _run(grafana.render_grafana_detail(_request(), session, user, row))
        assert page.status_code == 200

        with pytest.raises(HTTPException):
            _run(npm.npm_create_proxy_host(999999, session, user, "app.example.com", "192.0.2.10", "80", "http", "on", "", None))

        monkeypatch.setattr(npm, "_npm_session", lambda *_a, **_k: ("token-example", True))
        monkeypatch.setattr(npm, "_refresh_npm", lambda *_a, **_k: None)
        monkeypatch.setattr(
            "app.services.integrations.npm.create_proxy_host",
            lambda *_a, **_k: {"id": 4, "domain_names": ["app.example.com"]},
        )
        made = _run(
            npm.npm_create_proxy_host(
                integration.id, session, user, "app.example.com", "192.0.2.10", "80", "http", "on", "", None
            )
        )
        assert made.status_code == 303
        monkeypatch.setattr(
            "app.services.integrations.npm.create_proxy_host",
            lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("npm")),
        )
        broken = _run(
            npm.npm_create_proxy_host(
                integration.id, session, user, "app.example.com", "192.0.2.10", "80", "http", "on", "", None
            )
        )
        assert "create_failed" in broken.headers["location"]
        monkeypatch.setattr(
            "app.services.integrations.npm.update_proxy_host",
            lambda *_a, **_k: {"id": 4},
        )
        updated = _run(
            npm.npm_update_proxy_host(
                integration.id, "4", session, user, "app.example.com", "192.0.2.10", "80", "http", "on", "", None
            )
        )
        assert updated.status_code == 303
        monkeypatch.setattr(
            "app.services.integrations.npm.delete_proxy_host",
            lambda *_a, **_k: "4",
        )
        deleted = _run(npm.npm_delete_proxy_host(integration.id, "4", session, user))
        assert deleted.status_code == 303
