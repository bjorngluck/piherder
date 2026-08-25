"""v1.4 Stream M — host lock (M1). No live SSH."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.database import get_session
from app.main import app
from app.models import AuditLog, ComposeProjectMeta, Server, User
from app.security.auth import create_access_token, get_password_hash
from app.security.encryption import encrypt_str
from app.services.service_migrate import host_lock as hl


@pytest.fixture()
def lock_db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'lock.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_compose_project_name_rejects_paths():
    with pytest.raises(hl.HostLockError):
        hl.compose_project_name("../etc")
    with pytest.raises(hl.HostLockError):
        hl.compose_project_name("/home/pi/docker/foo")
    with pytest.raises(hl.HostLockError):
        hl.compose_project_name("foo/bar")
    with pytest.raises(hl.HostLockError):
        hl.compose_project_name("")
    assert hl.compose_project_name(" grafana ") == "grafana"


def test_haos_is_implicit_lock(lock_db):
    srv = Server(name="ha", hostname="ha.local", os_type="haos")
    lock_db.add(srv)
    lock_db.commit()
    lock_db.refresh(srv)
    st = hl.lock_state(lock_db, srv, "anything")
    assert st["locked"] is True
    assert st["implicit"] is True
    assert st["reason"] == "haos"
    with pytest.raises(hl.HostLockError) as e:
        hl.assert_unlocked(lock_db, srv, "anything")
    assert e.value.status_code == 403


def test_haos_cannot_lock_or_unlock(lock_db):
    srv = Server(name="ha", hostname="ha.local", os_type="haos")
    lock_db.add(srv)
    lock_db.commit()
    lock_db.refresh(srv)
    with pytest.raises(hl.HostLockError):
        hl.set_host_lock(lock_db, srv, "frigate", reason="hardware")
    with pytest.raises(hl.HostLockError):
        hl.unlock_host(lock_db, srv, "frigate")


def test_lock_unlock_persist(lock_db):
    srv = Server(name="pi", hostname="pi.local", os_type="debian")
    lock_db.add(srv)
    lock_db.commit()
    lock_db.refresh(srv)
    hl.set_host_lock(
        lock_db,
        srv,
        "frigate",
        reason="hardware",
        note="Coral TPU",
        user_id=1,
    )
    lock_db.commit()
    st = hl.lock_state(lock_db, srv, "frigate")
    assert st["locked"] is True
    assert st["reason"] == "hardware"
    assert st["note"] == "Coral TPU"
    with pytest.raises(hl.HostLockError):
        hl.assert_unlocked(lock_db, srv, "frigate")
    # second lock updates same unique row
    hl.set_host_lock(lock_db, srv, "frigate", reason="operator", note="wait")
    lock_db.commit()
    rows = lock_db.exec(
        select(ComposeProjectMeta).where(ComposeProjectMeta.server_id == srv.id)
    ).all()
    assert len(rows) == 1
    assert rows[0].lock_reason == "operator"
    hl.unlock_host(lock_db, srv, "frigate")
    lock_db.commit()
    st2 = hl.lock_state(lock_db, srv, "frigate")
    assert st2["locked"] is False
    hl.assert_unlocked(lock_db, srv, "frigate")


def test_lock_reason_enum(lock_db):
    srv = Server(name="pi", hostname="pi.local")
    lock_db.add(srv)
    lock_db.commit()
    lock_db.refresh(srv)
    with pytest.raises(hl.HostLockError):
        hl.set_host_lock(lock_db, srv, "grafana", reason="haos")
    with pytest.raises(hl.HostLockError):
        hl.set_host_lock(lock_db, srv, "grafana", reason="nope")


def test_annotate_projects(lock_db):
    srv = Server(name="pi", hostname="pi.local")
    lock_db.add(srv)
    lock_db.commit()
    lock_db.refresh(srv)
    hl.set_host_lock(lock_db, srv, "frigate", reason="hardware")
    lock_db.commit()
    projects = [{"name": "frigate"}, {"name": "grafana"}]
    hl.annotate_projects(lock_db, srv, projects)
    assert projects[0]["host_lock"]["locked"] is True
    assert projects[1]["host_lock"]["locked"] is False


def test_migrate_enabled_default_off():
    assert hl.migrate_enabled() is False


@pytest.fixture()
def lock_client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.security.auth.force_2fa_required", lambda: False)
    monkeypatch.setattr(
        "app.services.account_stepup.force_2fa_applies",
        lambda *a, **k: False,
    )
    engine = create_engine(
        f"sqlite:///{tmp_path / 'lockhttp.db'}",
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
            email="admin@lock.test",
            hashed_password=get_password_hash("SmokeTest1ok"),
            role="admin",
            is_active=True,
            must_change_password=False,
            totp_enabled=True,
        )
        viewer = User(
            email="viewer@lock.test",
            hashed_password=get_password_hash("SmokeTest1ok"),
            role="viewer",
            is_active=True,
            must_change_password=False,
            totp_enabled=True,
        )
        s.add(admin)
        s.add(viewer)
        s.commit()
        s.refresh(admin)
        s.refresh(viewer)
        pi = Server(
            name="Lab Pi",
            hostname="lab.local",
            ssh_username="pi",
            ssh_password_encrypted=encrypt_str("x"),
            container_patch_enabled=True,
            os_type="debian",
        )
        ha = Server(
            name="HAOS",
            hostname="ha.local",
            ssh_username="root",
            ssh_password_encrypted=encrypt_str("x"),
            os_type="haos",
        )
        s.add(pi)
        s.add(ha)
        s.commit()
        s.refresh(pi)
        s.refresh(ha)
        ids = {
            "admin": admin.id,
            "viewer": viewer.id,
            "pi": pi.id,
            "ha": ha.id,
        }
    try:
        yield client, ids, engine
    finally:
        app.dependency_overrides.clear()


def _cookie(uid: int) -> dict[str, str]:
    return {"access_token": create_access_token({"sub": str(uid)})}


def test_http_lock_unlock_admin(lock_client):
    client, ids, engine = lock_client
    r = client.post(
        f"/servers/{ids['pi']}/docker/host-lock",
        data={"project": "frigate", "reason": "hardware", "note": "Coral TPU"},
        cookies=_cookie(ids["admin"]),
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "host_locked" in (r.headers.get("location") or "")
    with Session(engine) as s:
        row = s.exec(
            select(ComposeProjectMeta).where(
                ComposeProjectMeta.server_id == ids["pi"],
                ComposeProjectMeta.compose_project == "frigate",
            )
        ).first()
        assert row is not None
        assert row.host_locked is True
        assert row.lock_reason == "hardware"
        audit = s.exec(
            select(AuditLog).where(AuditLog.action == "service_host_lock")
        ).first()
        assert audit is not None
        assert "frigate" in (audit.details or "")
        assert "Coral" in (audit.details or "")
    r2 = client.post(
        f"/servers/{ids['pi']}/docker/host-unlock",
        data={"project": "frigate"},
        cookies=_cookie(ids["admin"]),
        follow_redirects=False,
    )
    assert r2.status_code == 303
    assert "host_unlocked" in (r2.headers.get("location") or "")
    with Session(engine) as s:
        row = s.exec(
            select(ComposeProjectMeta).where(
                ComposeProjectMeta.server_id == ids["pi"],
                ComposeProjectMeta.compose_project == "frigate",
            )
        ).first()
        assert row.host_locked is False
        unlock = s.exec(
            select(AuditLog).where(AuditLog.action == "service_host_unlock")
        ).first()
        assert unlock is not None


def test_http_lock_viewer_403(lock_client):
    client, ids, _engine = lock_client
    r = client.post(
        f"/servers/{ids['pi']}/docker/host-lock",
        data={"project": "grafana", "reason": "operator"},
        cookies=_cookie(ids["viewer"]),
        follow_redirects=False,
    )
    assert r.status_code == 403


def test_http_lock_haos_refused(lock_client):
    client, ids, engine = lock_client
    r = client.post(
        f"/servers/{ids['ha']}/docker/host-lock",
        data={"project": "addon", "reason": "operator"},
        cookies=_cookie(ids["admin"]),
        follow_redirects=False,
    )
    assert r.status_code == 303
    loc = r.headers.get("location") or ""
    assert "error=host_lock" in loc
    with Session(engine) as s:
        n = len(
            s.exec(
                select(ComposeProjectMeta).where(
                    ComposeProjectMeta.server_id == ids["ha"]
                )
            ).all()
        )
        assert n == 0


def test_http_lock_rejects_path_project(lock_client):
    client, ids, _engine = lock_client
    r = client.post(
        f"/servers/{ids['pi']}/docker/host-lock",
        data={"project": "../etc", "reason": "operator"},
        cookies=_cookie(ids["admin"]),
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "error=host_lock" in (r.headers.get("location") or "")
