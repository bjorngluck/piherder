"""Admin alert: user create, API token create, and a real 2FA setting change."""
from __future__ import annotations

from sqlmodel import Session, SQLModel, create_engine, select

from app.models import Notification, User
from app.services import admin_alerts as alerts
from app.services import alert_policy as apol
from app.services.request_ip import reset_request_client_ip, set_request_client_ip


def _engine():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return engine


def _user(session: Session, email: str, *, name: str = "Ada") -> User:
    row = User(
        email=email,
        hashed_password="not-a-secret",
        role="admin",
        display_name=name,
        is_active=True,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _rows(session: Session) -> list[Notification]:
    return list(
        session.exec(
            select(Notification)
            .where(Notification.type == alerts.TYPE_ID)
            .order_by(Notification.id)
        ).all()
    )


def test_catalog_is_warning_and_not_pinned():
    spec = apol.CATALOG_BY_ID["admin_change"]
    assert spec.category == "admin"
    assert spec.default_severity == "warning"
    assert "admin_change" not in apol.PINNED_CRITICAL
    assert apol.CATEGORY_LABELS["admin"] == "Admin"


def test_body_names_actor_and_ip_and_keeps_them_when_long():
    body = alerts._with_actor("x" * 400, "Ada (off@test.local)", "192.0.2.15")
    assert body.endswith(" Actor: Ada (off@test.local). Client IP: 192.0.2.15.")
    assert len(body) <= 300
    assert "ph_" not in body


def test_user_and_token_alerts_name_actor_and_ip():
    engine = _engine()
    token = set_request_client_ip("192.0.2.15")
    try:
        with Session(engine) as session:
            admin = _user(session, "off@test.local")
            created = _user(session, "reader@test.local", name="Reader")
            alerts.notify_user_created(
                session,
                actor=admin,
                email=created.email,
                role="viewer",
                user_id=int(created.id),
            )
            alerts.notify_api_token_created(
                session,
                actor=admin,
                token_id=9,
                name="mcp-agent",
            )
            rows = _rows(session)
            assert len(rows) == 2
            user_row = next(r for r in rows if r.title == "User created")
            token_row = next(r for r in rows if r.title == "API token created")
            assert "Ada (off@test.local)" in (user_row.body or "")
            assert "192.0.2.15" in (user_row.body or "")
            assert "reader@test.local" in (user_row.body or "")
            assert "viewer" in (user_row.body or "")
            assert user_row.link_url == "/auth/users"
            assert user_row.severity == "warning"
            assert "mcp-agent" in (token_row.body or "")
            assert "192.0.2.15" in (token_row.body or "")
            assert token_row.fingerprint == "admin_api_token_created:9"
            assert "ph_" not in (token_row.body or "")
    finally:
        reset_request_client_ip(token)


def test_two_creates_stay_separate_and_unknown_ip_is_named():
    engine = _engine()
    token = set_request_client_ip(None)
    try:
        with Session(engine) as session:
            admin = _user(session, "off@test.local")
            alerts.notify_api_token_created(
                session, actor=admin, token_id=1, name="one"
            )
            alerts.notify_api_token_created(
                session, actor=admin, token_id=2, name="two"
            )
            rows = _rows(session)
            assert {r.fingerprint for r in rows} == {
                "admin_api_token_created:1",
                "admin_api_token_created:2",
            }
            assert all("Client IP: unknown." in (r.body or "") for r in rows)
    finally:
        reset_request_client_ip(token)


def test_2fa_alert_only_on_a_real_change_and_each_change_stays():
    engine = _engine()
    token = set_request_client_ip("192.0.2.20")
    try:
        with Session(engine) as session:
            admin = _user(session, "off@test.local")
            alerts.notify_2fa_setting_change(
                session,
                actor=admin,
                prev_scope="off",
                new_scope="off",
                prev_template=False,
                new_template=False,
            )
            assert _rows(session) == []
            alerts.notify_2fa_setting_change(
                session,
                actor=admin,
                prev_scope="off",
                new_scope="admins",
                prev_template=False,
                new_template=False,
            )
            alerts.notify_2fa_setting_change(
                session,
                actor=admin,
                prev_scope="admins",
                new_scope="admins",
                prev_template=False,
                new_template=True,
            )
            rows = _rows(session)
            assert len(rows) == 2
            assert rows[0].fingerprint != rows[1].fingerprint
            assert "Force 2FA changed from off to admins" in (rows[0].body or "")
            assert "Template 2FA" not in (rows[0].body or "")
            assert "Template 2FA changed from off to on" in (rows[1].body or "")
            assert "Force 2FA" not in (rows[1].body or "")
            assert "Ada (off@test.local)" in (rows[1].body or "")
            assert "192.0.2.20" in (rows[1].body or "")
    finally:
        reset_request_client_ip(token)


def test_muted_admin_category_writes_nothing():
    from app.services import app_settings as app_cfg

    engine = _engine()
    previous = app_cfg.load_settings().get("alert_type_policy")
    app_cfg.save_settings(
        {"alert_type_policy": {"categories": {"admin": {"enabled": False}}}}
    )
    try:
        with Session(engine) as session:
            admin = _user(session, "off@test.local")
            alerts.notify_user_created(
                session,
                actor=admin,
                email="reader@test.local",
                role="viewer",
                user_id=2,
            )
            assert _rows(session) == []
    finally:
        app_cfg.save_settings({"alert_type_policy": previous or {}})


def test_alert_failure_does_not_raise(monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("inbox down")

    monkeypatch.setattr("app.services.notifications.upsert_notification", _boom)
    engine = _engine()
    with Session(engine) as session:
        admin = _user(session, "off@test.local")
        alerts.notify_user_created(
            session,
            actor=admin,
            email="reader@test.local",
            role="viewer",
            user_id=2,
        )


def _http_client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy.pool import StaticPool

    from app.database import get_session
    from app.main import app
    from app.security.auth import create_user_access_token, get_password_hash

    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    engine = create_engine(
        f"sqlite:///{tmp_path / 'admin-alert.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def _session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    client = TestClient(app, raise_server_exceptions=True)
    return app, client, engine, get_password_hash, create_user_access_token


def test_http_user_token_and_2fa_open_the_alert(tmp_path, monkeypatch):
    from app.services import app_settings as app_cfg

    app, client, engine, get_password_hash, create_user_access_token = _http_client(
        tmp_path, monkeypatch
    )
    saved = app_cfg.load_settings()
    app_cfg.save_settings(
        {
            "force_2fa": False,
            "force_2fa_scope": "off",
            "template_require_2fa": False,
            "alert_type_policy": {},
        }
    )
    password = "SecretPass1"
    try:
        with Session(engine) as session:
            admin = User(
                email="off@test.local",
                hashed_password=get_password_hash(password),
                role="admin",
                display_name="Ada",
                is_active=True,
                must_change_password=False,
            )
            session.add(admin)
            session.commit()
            session.refresh(admin)
            client.cookies.set("access_token", create_user_access_token(admin))

        created = client.post(
            "/auth/users/create",
            data={
                "email": "reader@test.local",
                "password": password,
                "role": "viewer",
                "display_name": "Reader",
            },
        )
        assert created.status_code == 200, created.text[:400]
        token_resp = client.post(
            "/api/v1/tokens",
            json={"name": "mcp-agent", "scopes": ["read"]},
        )
        assert token_resp.status_code == 201, token_resp.text[:400]
        secret = token_resp.json()["secret"]
        assert secret.startswith("ph_")

        first = client.post(
            "/herder-backups/security",
            data={"force_2fa_scope": "admins", "template_require_2fa": ""},
            follow_redirects=False,
        )
        assert first.status_code == 303, first.text[:300]
        assert "security_saved=1" in first.headers.get("location", "")
        second = client.post(
            "/herder-backups/security",
            data={"force_2fa_scope": "admins", "template_require_2fa": "on"},
            follow_redirects=False,
        )
        assert second.status_code == 303
        third = client.post(
            "/herder-backups/security",
            data={"force_2fa_scope": "admins", "template_require_2fa": "on"},
            follow_redirects=False,
        )
        assert third.status_code == 303

        with Session(engine) as session:
            rows = _rows(session)
        by_title = {}
        for row in rows:
            by_title.setdefault(row.title, []).append(row)
        assert len(by_title["User created"]) == 1
        user_body = by_title["User created"][0].body or ""
        assert "Ada (off@test.local)" in user_body
        assert "reader@test.local" in user_body
        assert "Client IP:" in user_body
        assert password not in user_body
        token_body = by_title["API token created"][0].body or ""
        assert "mcp-agent" in token_body
        assert secret not in token_body
        assert secret[:12] not in token_body
        assert "Ada (off@test.local)" in token_body
        changes = by_title["2FA setting changed"]
        assert len(changes) == 2
        assert "Force 2FA changed from off to admins" in (changes[0].body or "")
        assert "Template 2FA changed from off to on" in (changes[1].body or "")
        assert "Force 2FA" not in (changes[1].body or "")
    finally:
        app.dependency_overrides.clear()
        app_cfg.save_settings(
            {
                "force_2fa": saved.get("force_2fa", False),
                "force_2fa_scope": saved.get("force_2fa_scope") or "off",
                "template_require_2fa": bool(saved.get("template_require_2fa")),
                "alert_type_policy": saved.get("alert_type_policy") or {},
            }
        )


def test_http_registration_opens_the_alert(tmp_path, monkeypatch):
    app, client, engine, _hash, _token = _http_client(tmp_path, monkeypatch)
    try:
        resp = client.post(
            "/auth/register",
            data={"email": "off@test.local", "password": "SecretPass1"},
            follow_redirects=False,
        )
        assert resp.status_code == 303, resp.text[:400]
        assert resp.headers.get("location", "").endswith("/auth/login")
        with Session(engine) as session:
            rows = _rows(session)
        assert len(rows) == 1
        body = rows[0].body or ""
        assert "Registered off@test.local as admin" in body
        assert "SecretPass1" not in body
        assert "Client IP:" in body
    finally:
        app.dependency_overrides.clear()


def test_scope_gain_and_promotion_name_the_change():
    engine = _engine()
    token = set_request_client_ip("192.0.2.15")
    try:
        with Session(engine) as session:
            admin = _user(session, "off@test.local")
            alerts.notify_api_token_scopes_gained(
                session, actor=admin, token_id=4, name="ops", gained=[]
            )
            alerts.notify_api_token_scopes_gained(
                session,
                actor=admin,
                token_id=4,
                name="ops",
                gained=["files", "edit", "admin"],
            )
            alerts.notify_user_promoted_to_admin(
                session, actor=admin, email="reader@test.local", user_id=8
            )
            rows = _rows(session)
        assert len(rows) == 2
        scope = next(r for r in rows if r.title == "API token scopes gained")
        body = scope.body or ""
        assert "gained scopes: admin, edit, files" in body
        assert "The admin scope was added" in body
        assert "The files scope was added" in body
        assert "ph_" not in body
        assert "192.0.2.15" in body
        promo = next(r for r in rows if r.title == "User promoted to admin")
        assert "Promoted reader@test.local to admin." in (promo.body or "")
        assert promo.link_url == "/auth/users"
    finally:
        reset_request_client_ip(token)


def test_token_update_and_role_change_open_escalation_alerts(tmp_path, monkeypatch):
    from app.services import api_tokens as tok

    app, client, engine, get_password_hash, create_user_access_token = _http_client(
        tmp_path, monkeypatch
    )
    try:
        with Session(engine) as session:
            admin = User(
                email="off@test.local",
                hashed_password=get_password_hash("SecretPass1"),
                role="admin",
                display_name="Ada",
                is_active=True,
                must_change_password=False,
            )
            reader = User(
                email="reader@test.local",
                hashed_password=get_password_hash("SecretPass1"),
                role="operator",
                is_active=True,
                must_change_password=False,
            )
            session.add(admin)
            session.add(reader)
            session.commit()
            session.refresh(admin)
            session.refresh(reader)
            client.cookies.set("access_token", create_user_access_token(admin))
            row, plain = tok.create_api_token(
                session, name="ops", created_by=admin, scopes=["read"]
            )
            token_id = row.id
            prefix = row.token_prefix
            reader_id = reader.id

        def _update(scopes):
            data = {"name": "ops"}
            data.update(scopes)
            return client.post(
                f"/herder-backups/api-tokens/{token_id}/update",
                data=data,
                follow_redirects=False,
            )

        gained = _update(
            {"scope_read": "on", "scope_admin": "on", "scope_files": "on"}
        )
        assert gained.status_code == 303
        assert "token_updated=1" in gained.headers.get("location", "")
        same = _update(
            {"scope_read": "on", "scope_admin": "on", "scope_files": "on"}
        )
        assert same.status_code == 303
        dropped = _update({"scope_read": "on"})
        assert dropped.status_code == 303

        promoted = client.post(
            f"/auth/users/{reader_id}/role",
            data={"role": "admin"},
            follow_redirects=False,
        )
        assert promoted.status_code == 303
        assert "role_saved" in promoted.headers.get("location", "")
        changed = client.post(
            f"/auth/users/{reader_id}/role",
            data={"role": "operator"},
            follow_redirects=False,
        )
        assert changed.status_code == 303

        with Session(engine) as session:
            rows = _rows(session)
        scope_rows = [r for r in rows if r.title == "API token scopes gained"]
        promo_rows = [r for r in rows if r.title == "User promoted to admin"]
        assert len(scope_rows) == 1
        body = scope_rows[0].body or ""
        assert "The admin scope was added" in body
        assert "The files scope was added" in body
        assert plain not in body
        assert prefix not in body
        assert len(promo_rows) == 1
        assert "reader@test.local" in (promo_rows[0].body or "")
    finally:
        app.dependency_overrides.clear()
