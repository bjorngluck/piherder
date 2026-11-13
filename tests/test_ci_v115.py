"""CI workflow shape and the shared test schema helper."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.database import get_session
from app.main import app
from app.models import IntegrationBinding, Server, User
from app.security.auth import create_user_access_token, get_password_hash
from app.services.integrations.generic_url import GenericProbeResult
from app.services.integrations.registry import ROLE_SERVICE, TYPE_GENERIC_URL

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")


def test_workflow_runs_on_dev_branches_with_lint_and_migrations():
    assert 'v*-dev' in WORKFLOW
    assert "ruff check app tests migrations" in WORKFLOW
    assert "alembic upgrade head" in WORKFLOW
    assert "alembic downgrade base" in WORKFLOW
    assert "set -euo pipefail" in WORKFLOW
    migration = WORKFLOW.split("migrations:", 1)[1]
    assert "|| true" not in migration
    assert "continue-on-error" not in migration


def test_postgres_test_schema_uses_alembic_not_create_all():
    from tests.schema_setup import apply_test_schema

    engine = MagicMock()
    engine.dialect.name = "postgresql"
    engine.url = "postgresql://piherder:piherder@127.0.0.1:55432/piherder"
    with (
        patch("tests.schema_setup.upgrade_engine") as upgrade,
        patch("sqlmodel.SQLModel.metadata.create_all") as create_all,
    ):
        apply_test_schema(engine)
    upgrade.assert_called_once_with(engine)
    create_all.assert_not_called()


def test_sqlite_test_schema_uses_create_all():
    from tests.schema_setup import apply_test_schema

    engine = MagicMock()
    engine.dialect.name = "sqlite"
    with (
        patch("tests.schema_setup.upgrade_engine") as upgrade,
        patch("sqlmodel.SQLModel.metadata.create_all") as create_all,
    ):
        apply_test_schema(engine)
    upgrade.assert_not_called()
    create_all.assert_called_once_with(engine)


def _client(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'ci.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def _session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    client = TestClient(app, raise_server_exceptions=True)
    return engine, client


def _admin(engine) -> User:
    with Session(engine) as session:
        user = User(
            email="user@example.com",
            hashed_password=get_password_hash("SmokeTest1ok"),
            role="admin",
            is_active=True,
            must_change_password=False,
            totp_enabled=False,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        return user


def _ok(*args, **kwargs):
    del args
    return GenericProbeResult(
        ok=True,
        status_code=200,
        final_url="https://app.example.com/",
        product=kwargs.get("product", "custom"),
        health_path="/",
    )


def _bad(*args, **kwargs):
    del args
    return GenericProbeResult(
        ok=False,
        error="down",
        status_code=500,
        product=kwargs.get("product", "custom"),
    )


def test_generic_link_pages_and_fleet_services(tmp_path, monkeypatch):
    import app.routers.fleet_services as fleet
    import app.routers.integrations_generic as generic

    engine, client = _client(tmp_path)
    user = _admin(engine)
    client.cookies.set("access_token", create_user_access_token(user))
    monkeypatch.setattr(generic.gen, "probe", _ok)
    try:
        form = client.get("/integrations/new/generic?product=n8n")
        assert form.status_code == 200
        assert "n8n" in form.text

        created = client.post(
            "/integrations/new/generic",
            data={
                "name": "n8n",
                "base_url": "https://app.example.com",
                "product": "n8n",
                "health_path": "/healthz",
                "notes": "lab",
                "api_key": "",
                "poll_interval_sec": "120",
                "tls_verify": "on",
                "enabled": "on",
            },
            follow_redirects=False,
        )
        assert created.status_code == 303
        assert "msg=created" in (created.headers.get("location") or "")

        with Session(engine) as session:
            from sqlmodel import select

            from app.models import Integration

            row = session.exec(select(Integration)).first()
            assert row is not None and row.type == TYPE_GENERIC_URL
            integration_id = row.id
            host = Server(
                name="host-a",
                hostname="host-a.example.com",
                ip_address="192.0.2.10",
            )
            session.add(host)
            session.commit()
            session.refresh(host)
            server_id = host.id

        detail = client.get(f"/integrations/{integration_id}")
        assert detail.status_code == 200
        assert "n8n" in detail.text

        bound = client.post(
            f"/integrations/{integration_id}/generic/bind",
            data={
                "server_id": str(server_id),
                "label": "n8n",
                "path": "/workflow",
                "docker_project": "app",
                "docker_container": "app-1",
            },
            follow_redirects=False,
        )
        assert bound.status_code == 303
        assert "binding_saved" in (bound.headers.get("location") or "")

        deep = client.post(
            f"/integrations/{integration_id}/generic/bind",
            data={
                "server_id": str(server_id),
                "label": "docs",
                "path": "https://docs.example.com/guide",
            },
            follow_redirects=False,
        )
        assert deep.status_code == 303

        missing = client.post(
            "/integrations/999/generic/bind",
            data={"server_id": str(server_id), "label": "x", "path": "/"},
            follow_redirects=False,
        )
        assert missing.status_code == 404

        services = client.get("/services")
        assert services.status_code == 200

        with Session(engine) as session:
            binding = session.exec(select(IntegrationBinding)).first()
            assert binding is not None and binding.role == ROLE_SERVICE
            binding_id = binding.id

        svg = b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'
        uploaded = client.post(
            f"/services/{binding_id}/logo",
            data={"next": "/services"},
            files={"file": ("logo.svg", svg, "image/svg+xml")},
            follow_redirects=False,
        )
        assert uploaded.status_code == 303
        assert "logo_saved" in (uploaded.headers.get("location") or "")

        logo = client.get(f"/services/logo/{binding_id}")
        assert logo.status_code == 200
        assert b"<svg" in logo.content

        monkeypatch.setattr(fleet.integ_reg, "maybe_discover_logo", lambda *a, **k: False)
        discovered = client.post(
            f"/services/{binding_id}/logo/discover",
            data={"next": "/services"},
            follow_redirects=False,
        )
        assert discovered.status_code == 303
        assert "error=logo" in (discovered.headers.get("location") or "")

        removed = client.post(
            f"/services/{binding_id}/logo/delete",
            data={"next": "/services?tab=1"},
            follow_redirects=False,
        )
        assert removed.status_code == 303
        assert "logo_deleted" in (removed.headers.get("location") or "")

        gone = client.get("/services/logo/999")
        assert gone.status_code == 404

        bad_upload = client.post(
            f"/services/{binding_id}/logo",
            data={"next": "/services"},
            files={"file": ("note.txt", b"hello", "text/plain")},
            follow_redirects=False,
        )
        assert bad_upload.status_code == 303
        assert "error=logo" in (bad_upload.headers.get("location") or "")

        with Session(engine) as session:
            binding = session.get(IntegrationBinding, binding_id)
            assert binding is not None
        cleared = client.post(
            f"/integrations/{integration_id}/generic/bind/{binding_id}/remove",
            follow_redirects=False,
        )
        assert cleared.status_code == 303
        assert "binding_cleared" in (cleared.headers.get("location") or "")
    finally:
        app.dependency_overrides.pop(get_session, None)


def test_generic_create_failure_paths(tmp_path, monkeypatch):
    import app.routers.integrations_generic as generic

    engine, client = _client(tmp_path)
    user = _admin(engine)
    client.cookies.set("access_token", create_user_access_token(user))
    try:
        monkeypatch.setattr(generic.gen, "probe", _ok)
        test_ok = client.post(
            "/integrations/new/generic",
            data={
                "name": "lab",
                "base_url": "https://app.example.com",
                "product": "custom",
                "test_only": "on",
            },
            follow_redirects=False,
        )
        assert test_ok.status_code == 303
        assert "test_ok" in (test_ok.headers.get("location") or "")

        monkeypatch.setattr(generic.gen, "probe", _bad)
        test_bad = client.post(
            "/integrations/new/generic",
            data={
                "name": "lab",
                "base_url": "https://app.example.com",
                "product": "custom",
                "test_only": "on",
            },
            follow_redirects=False,
        )
        assert test_bad.status_code == 303
        assert "test_failed" in (test_bad.headers.get("location") or "")

        refused = client.post(
            "/integrations/new/generic",
            data={
                "name": "lab",
                "base_url": "https://app.example.com",
                "product": "custom",
            },
            follow_redirects=False,
        )
        assert refused.status_code == 303
        assert "test_failed" in (refused.headers.get("location") or "")

        skipped = client.post(
            "/integrations/new/generic",
            data={
                "name": "lab",
                "base_url": "https://app.example.com",
                "product": "home_assistant",
                "skip_test": "on",
                "enabled": "off",
            },
            follow_redirects=False,
        )
        assert skipped.status_code == 303
        assert "msg=created" in (skipped.headers.get("location") or "")

        invalid = client.post(
            "/integrations/new/generic",
            data={"name": "lab", "base_url": "not-a-url", "product": "custom"},
            follow_redirects=False,
        )
        assert invalid.status_code == 303
        assert "error=invalid" in (invalid.headers.get("location") or "")

        missing_unbind = client.post(
            "/integrations/1/generic/bind/999/remove",
            follow_redirects=False,
        )
        assert missing_unbind.status_code == 404
    finally:
        app.dependency_overrides.pop(get_session, None)
