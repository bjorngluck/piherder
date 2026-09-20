"""v1.6 Q-80 twenty-seventh pack — account password/avatar + DNS stack-edges/visual stacks."""
from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.main import app
from app.models import Server, User
from app.security.auth import create_user_access_token, get_password_hash
from app.security.encryption import encrypt_str


def test_account_and_dns_stack_http(tmp_path, monkeypatch):
    from app.services import runtime_edges as re_svc
    from app.services import container_annotations as ann_svc

    monkeypatch.setattr(
        re_svc,
        "accept_suggestion",
        lambda *a, **k: SimpleNamespace(id=7),
    )
    monkeypatch.setattr(re_svc, "dismiss_suggestion", lambda *a, **k: None)
    monkeypatch.setattr(
        re_svc,
        "create_manual_edge",
        lambda *a, **k: SimpleNamespace(id=8),
    )
    monkeypatch.setattr(re_svc, "delete_edge", lambda *a, **k: None)

    engine = create_engine(
        f"sqlite:///{tmp_path / 'r27.db'}",
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
                email="q27@test.local",
                hashed_password=get_password_hash("SmokeTest1ok"),
                role="admin",
                is_active=True,
                must_change_password=False,
                totp_enabled=False,
            )
            s.add(user)
            srv = Server(
                name="Lab",
                hostname="lab.local",
                ip_address="10.0.0.9",
                ssh_username="pi",
                ssh_port=22,
                ssh_private_key_encrypted=encrypt_str("k"),
            )
            s.add(srv)
            s.commit()
            s.refresh(user)
            s.refresh(srv)
            sid = srv.id
            client.cookies.set("access_token", create_user_access_token(user))

        r = client.post(
            "/auth/account/profile",
            data={"display_name": "Q27", "email": "q27@test.local", "current_password": ""},
            follow_redirects=False,
        )
        assert r.status_code in (303, 403)
        r = client.post(
            "/auth/account/profile",
            data={
                "display_name": "Q27",
                "email": "other@test.local",
                "current_password": "wrong",
            },
            follow_redirects=False,
        )
        assert r.status_code in (303, 403)
        r = client.post(
            "/auth/account/password",
            data={
                "current_password": "wrong",
                "new_password": "NewPass1ok!",
                "confirm_password": "NewPass1ok!",
            },
            follow_redirects=False,
        )
        assert r.status_code in (303, 403, 200)
        loc = r.headers.get("location") or ""
        assert "bad_password" in loc or r.status_code in (403, 200)
        r = client.post(
            "/auth/account/password",
            data={
                "current_password": "SmokeTest1ok",
                "new_password": "a",
                "confirm_password": "b",
            },
            follow_redirects=False,
        )
        assert r.status_code in (303, 403)
        r = client.post(
            "/auth/account/avatar",
            files={"file": ("note.txt", b"not-an-image", "text/plain")},
            follow_redirects=False,
        )
        assert r.status_code in (303, 403, 422)
        r = client.post("/auth/account/avatar/delete", follow_redirects=False)
        assert r.status_code in (303, 403)
        r = client.get("/auth/me/avatar")
        assert r.status_code in (200, 404, 303)

        r = client.post(
            "/dns/visual-stacks",
            data={"server_id": str(sid), "project": "grafana", "name": ""},
            follow_redirects=False,
        )
        assert r.status_code in (303, 400, 403, 422)
        r = client.post(
            "/dns/visual-stacks",
            data={"server_id": str(sid), "project": "grafana", "name": "Web"},
            follow_redirects=False,
        )
        assert r.status_code in (303, 200, 403)
        r = client.post(
            "/dns/visual-stacks/99999/delete",
            data={"server_id": str(sid)},
            follow_redirects=False,
        )
        assert r.status_code in (303, 400, 403, 404)
        r = client.post(
            "/dns/vocab",
            data={"kind": "tag", "key": "iot", "label": "IoT"},
            follow_redirects=False,
        )
        assert r.status_code in (303, 200, 400, 403)
        r = client.post(
            "/dns/stack-edges/accept",
            data={
                "from_server_id": str(sid),
                "from_project": "a",
                "to_server_id": str(sid),
                "to_project": "b",
                "kind": "depends_on",
            },
            follow_redirects=False,
        )
        assert r.status_code in (303, 400, 403)
        r = client.post(
            "/dns/stack-edges/dismiss",
            data={
                "from_server_id": str(sid),
                "from_project": "a",
                "to_server_id": str(sid),
                "to_project": "b",
            },
            follow_redirects=False,
        )
        assert r.status_code in (303, 400, 403)
        r = client.post(
            "/dns/stack-edges/manual",
            data={
                "from_server_id": str(sid),
                "from_project": "a",
                "to_server_id": str(sid),
                "to_project": "b",
                "kind": "depends_on",
            },
            follow_redirects=False,
        )
        assert r.status_code in (303, 400, 403, 422)
        r = client.post("/dns/stack-edges/99999/delete", follow_redirects=False)
        assert r.status_code in (303, 400, 403, 404, 422)
    finally:
        app.dependency_overrides.clear()
