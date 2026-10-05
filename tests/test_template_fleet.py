"""Template page lists every host stack recorded from that template."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.main import app
from app.models import Server, ServiceTemplate, StackDeployment, User
from app.security.auth import create_user_access_token, get_password_hash


def _definition() -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "slug": "web",
            "name": "Web",
            "version": "1.2.0",
            "files": [{"path": "docker-compose.yml", "from": "files/docker-compose.yml"}],
            "file_contents": {"files/docker-compose.yml": "services: {}\n"},
            "variables": [],
        }
    )


def test_template_page_lists_fleet_stacks(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    monkeypatch.setattr("app.services.account_stepup.force_2fa_applies", lambda *_a, **_k: False)
    engine = create_engine(
        f"sqlite:///{tmp_path / 'fleet.db'}",
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
        with Session(engine) as s:
            user = User(
                email="fleet@test.local",
                hashed_password=get_password_hash("SmokeTest1ok"),
                role="admin",
                is_active=True,
                must_change_password=False,
                totp_enabled=True,
            )
            a = Server(name="alpha", hostname="alpha.local")
            b = Server(name="beta", hostname="beta.local")
            s.add(user)
            s.add(a)
            s.add(b)
            s.commit()
            s.refresh(user)
            s.refresh(a)
            s.refresh(b)
            tpl = ServiceTemplate(
                slug="web",
                name="Web",
                source="user",
                definition_json=_definition(),
            )
            s.add(tpl)
            s.commit()
            s.refresh(tpl)
            s.add(
                StackDeployment(
                    server_id=b.id,
                    project_name="web-b",
                    template_id=tpl.id,
                    template_slug="web",
                    template_version="1.0.0",
                    config_version=2,
                    drift_status="drifted",
                )
            )
            s.add(
                StackDeployment(
                    server_id=a.id,
                    project_name="web-a",
                    template_id=tpl.id,
                    template_slug="web",
                    template_version="1.2.0",
                    config_version=4,
                    drift_status="in_sync",
                )
            )
            s.add(
                StackDeployment(
                    server_id=a.id,
                    project_name="sidecar",
                    template_slug="sidecar",
                )
            )
            s.commit()
            client.cookies.set("access_token", create_user_access_token(user))

        page = client.get("/templates/web")
        assert page.status_code == 200, page.text[:400]
        assert 'data-testid="template-fleet"' in page.text
        assert page.text.index("alpha") < page.text.index("beta")
        assert "web-a" in page.text
        assert "web-b" in page.text
        assert "sidecar" not in page.text
        assert "in_sync" in page.text

        catalog = client.get("/templates")
        assert catalog.status_code == 200
        assert "2 stacks on the fleet" in catalog.text
    finally:
        app.dependency_overrides.clear()
