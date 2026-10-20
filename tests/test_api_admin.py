"""Read-only admin API. Scope admin is not implied by read and not granted by MCP."""
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import AuditLog, User
from app.security.auth import get_password_hash
from app.services import api_tokens as tok
from app.services.mcp_oauth import parse_oauth_scopes
from app.routers.api_admin import audit_admin_dict, token_admin_dict, user_admin_dict


def test_admin_scope_is_not_implied_by_read_or_oauth():
    assert tok.normalize_scopes(["admin"]) == ["admin"]
    assert "read" not in tok.normalize_scopes(["admin"])
    assert tok.normalize_scopes(None) == ["jobs", "read"]
    assert "admin" not in parse_oauth_scopes("admin jobs edit")
    assert parse_oauth_scopes("admin") == ["read"]
    assert "admin" not in parse_oauth_scopes("read files")


def test_public_dicts_omit_secrets():
    user = User(
        id=3,
        email="reader@test.local",
        hashed_password="hashed-secret",
        totp_secret_encrypted="totp-secret",
        session_version=4,
        totp_enabled=True,
        role="operator",
    )
    body = user_admin_dict(user)
    assert body["email"] == "reader@test.local"
    assert body["totp_enabled"] is True
    for key in ("hashed_password", "totp_secret_encrypted", "session_version", "avatar_path"):
        assert key not in body
    assert "hashed-secret" not in str(body)
    assert "totp-secret" not in str(body)

    row, plain = _token_row()
    listed = token_admin_dict(row)
    assert "token_prefix" not in listed
    assert "token_hash" not in listed
    assert plain not in str(listed)
    assert row.token_prefix not in str(listed)
    assert listed["scopes"] == ["admin"]

    log = AuditLog(
        id=1,
        action="note",
        status="success",
        details="d" * 600,
        output_snippet="o" * 600,
        client_ip="192.0.2.10",
    )
    audited = audit_admin_dict(log)
    assert len(audited["details"]) == 500
    assert "output_snippet" not in audited
    assert "o" * 20 not in str(audited)
    assert audited["client_ip"] == "192.0.2.10"


def _token_row():
    from app.models import ApiToken

    plain = tok.generate_plaintext_token()
    row = ApiToken(
        id=9,
        name="ops",
        token_prefix=plain[:12],
        token_hash=tok.hash_token(plain),
        scopes="admin",
    )
    return row, plain


def test_admin_gets_require_scope_and_skip_read_audit(tmp_path, monkeypatch):
    from app.database import get_session
    from app.main import app

    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    monkeypatch.setattr("app.services.api_read_audit.reads_enabled", lambda cfg=None: True)
    engine = create_engine(
        f"sqlite:///{tmp_path / 'admin-api.db'}",
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
                email="off@test.local",
                hashed_password=get_password_hash("SmokeTest1ok"),
                role="admin",
                is_active=True,
                totp_secret_encrypted="do-not-leak",
                totp_enabled=True,
                session_version=2,
            )
            s.add(user)
            s.commit()
            s.refresh(user)
            s.add(
                AuditLog(
                    user_id=user.id,
                    action="login",
                    status="success",
                    details="x" * 520,
                    output_snippet="y" * 20,
                    client_ip="192.0.2.8",
                )
            )
            s.commit()
            _read, read_plain = tok.create_api_token(
                s, name="reader", created_by=user, scopes=["read"]
            )
            admin_row, admin_plain = tok.create_api_token(
                s, name="auditor", created_by=user, scopes=["admin"]
            )
            uid = user.id
            prefix = admin_row.token_prefix

        read_headers = {"Authorization": f"Bearer {read_plain}"}
        admin_headers = {"Authorization": f"Bearer {admin_plain}"}

        denied = client.get("/api/v1/admin/users", headers=read_headers)
        assert denied.status_code == 403
        assert "admin" in denied.json()["detail"]

        listed = client.get("/api/v1/admin/users", headers=admin_headers)
        assert listed.status_code == 200
        payload = listed.json()
        assert payload["limit"] == 50
        assert payload["total"] >= 1
        match = next(u for u in payload["users"] if u["id"] == uid)
        assert match["email"] == "off@test.local"
        assert "hashed_password" not in match
        assert "do-not-leak" not in listed.text
        assert "session_version" not in match

        one = client.get(f"/api/v1/admin/users/{uid}", headers=admin_headers)
        assert one.status_code == 200
        assert one.json()["totp_enabled"] is True
        missing = client.get("/api/v1/admin/users/99999", headers=admin_headers)
        assert missing.status_code == 404

        tokens = client.get("/api/v1/admin/tokens", headers=admin_headers)
        assert tokens.status_code == 200
        assert "token_prefix" not in tokens.text
        assert prefix not in tokens.text
        assert admin_plain not in tokens.text
        capped = client.get("/api/v1/admin/tokens?limit=500", headers=admin_headers)
        assert capped.json()["limit"] == 100

        audit = client.get("/api/v1/admin/audit", headers=admin_headers)
        assert audit.status_code == 200
        rows = audit.json()["audit"]
        login = next(r for r in rows if r["action"] == "login")
        assert len(login["details"]) == 500
        assert login["client_ip"] == "192.0.2.8"

        posted = client.post("/api/v1/admin/users", headers=admin_headers)
        assert posted.status_code == 405

        with Session(engine) as s:
            admin_reads = [
                r
                for r in s.exec(select(AuditLog)).all()
                if r.action == "api_read"
            ]
        assert admin_reads
        assert {r.details for r in admin_reads} >= {
            "GET /api/v1/admin/users",
            "GET /api/v1/admin/tokens",
            "GET /api/v1/admin/audit",
        }
        assert all(r.api_token_name == "auditor" for r in admin_reads)

        health = client.get("/api/v1/health", headers=read_headers)
        assert health.status_code == 200
        with Session(engine) as s:
            actions = [r.action for r in s.exec(select(AuditLog)).all()]
        assert "api_read" in actions
    finally:
        app.dependency_overrides.clear()


def test_admin_read_audit_follows_the_flag_and_dedupes(tmp_path, monkeypatch):
    from app.database import get_session
    from app.main import app

    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    monkeypatch.setattr("app.services.api_read_audit.reads_enabled", lambda cfg=None: False)
    engine = create_engine(
        f"sqlite:///{tmp_path / 'admin-reads.db'}",
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
                email="off@test.local",
                hashed_password=get_password_hash("SmokeTest1ok"),
                role="admin",
                is_active=True,
            )
            s.add(user)
            s.commit()
            s.refresh(user)
            admin_row, admin_plain = tok.create_api_token(
                s, name="auditor", created_by=user, scopes=["admin"]
            )
            token_id = admin_row.id
            user_id = user.id
        headers = {"Authorization": f"Bearer {admin_plain}"}

        quiet = client.get("/api/v1/admin/users", headers=headers)
        assert quiet.status_code == 200
        with Session(engine) as s:
            assert [r for r in s.exec(select(AuditLog)).all() if r.action == "api_read"] == []

        monkeypatch.setattr("app.services.api_read_audit.reads_enabled", lambda cfg=None: True)
        first = client.get("/api/v1/admin/users", headers=headers)
        again = client.get("/api/v1/admin/users", headers=headers)
        other = client.get(f"/api/v1/admin/users/{user_id}", headers=headers)
        assert first.status_code == again.status_code == other.status_code == 200
        with Session(engine) as s:
            rows = [
                r
                for r in s.exec(select(AuditLog).order_by(AuditLog.id)).all()
                if r.action == "api_read"
            ]
        assert [r.details for r in rows] == [
            "GET /api/v1/admin/users",
            f"GET /api/v1/admin/users/{user_id}",
        ]
        assert rows[0].api_token_id == token_id
        assert rows[0].api_token_name == "auditor"
        assert rows[0].user_id == user_id
        assert rows[0].client_ip
        assert admin_plain not in (rows[0].details or "")

        with Session(engine) as s:
            stale = s.exec(
                select(AuditLog).where(AuditLog.details == "GET /api/v1/admin/users")
            ).one()
            stale.started_at = datetime.utcnow() - timedelta(minutes=6)
            s.add(stale)
            s.commit()
        later = client.get("/api/v1/admin/users", headers=headers)
        assert later.status_code == 200
        with Session(engine) as s:
            users_rows = [
                r
                for r in s.exec(select(AuditLog)).all()
                if r.action == "api_read" and r.details == "GET /api/v1/admin/users"
            ]
        assert len(users_rows) == 2
    finally:
        app.dependency_overrides.clear()
