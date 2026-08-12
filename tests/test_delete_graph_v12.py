"""Deleting a user or server must clear every FK child (no IntegrityError)."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlmodel import Session, SQLModel, create_engine, select
from sqlalchemy.pool import StaticPool

from app.models import (
    CertificateTarget,
    Integration,
    ManagedCertificate,
    NmapDevice,
    OidcIdentity,
    PasswordResetToken,
    RuntimeEdge,
    Server,
    User,
    UserFavourite,
    WebAuthnCredential,
)
from app.services.server_lifecycle import delete_server_from_fleet
from app.services.user_admin import detach_and_delete_user


def _engine(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'del.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def test_delete_user_with_oidc_pin_and_reset(tmp_path, monkeypatch):
    engine = _engine(tmp_path)
    monkeypatch.setattr("app.services.user_admin.delete_avatar_files", lambda uid: None)
    with Session(engine) as s:
        u = User(email="gone@example.com", hashed_password="x", role="operator")
        s.add(u)
        s.commit()
        s.refresh(u)
        uid = int(u.id)
        s.add(
            WebAuthnCredential(
                user_id=uid, credential_id="cred1", public_key="pk"
            )
        )
        s.add(
            OidcIdentity(
                user_id=uid,
                issuer="https://idp.example",
                subject="sub-1",
            )
        )
        s.add(
            UserFavourite(
                user_id=uid,
                kind="app_page",
                feature="jobs",
            )
        )
        s.add(
            PasswordResetToken(
                user_id=uid,
                token_hash="abc",
                expires_at=datetime.utcnow() + timedelta(hours=1),
            )
        )
        s.commit()
        email = detach_and_delete_user(s, u)
        s.commit()
        assert email == "gone@example.com"
        assert s.get(User, uid) is None
        assert s.exec(select(OidcIdentity)).first() is None
        assert s.exec(select(UserFavourite)).first() is None
        assert s.exec(select(PasswordResetToken)).first() is None
        assert s.exec(select(WebAuthnCredential)).first() is None


def test_delete_server_with_cert_edge_pin_nmap(tmp_path, monkeypatch):
    engine = _engine(tmp_path)
    monkeypatch.setattr(
        "app.services.server_lifecycle._cancel_active_jobs", lambda *a, **k: 0
    )
    monkeypatch.setattr(
        "app.services.server_lifecycle._unregister_schedules", lambda *a, **k: None
    )
    with Session(engine) as s:
        host = Server(name="lab-1", hostname="lab-1.example", ssh_username="pi")
        other = Server(name="lab-2", hostname="lab-2.example", ssh_username="pi")
        s.add(host)
        s.add(other)
        s.commit()
        s.refresh(host)
        s.refresh(other)
        hid = int(host.id)
        cert = ManagedCertificate(name="lab.example")
        s.add(cert)
        s.commit()
        s.refresh(cert)
        s.add(
            CertificateTarget(
                certificate_id=int(cert.id),
                server_id=hid,
                remote_dir="~/certs",
            )
        )
        s.add(
            RuntimeEdge(
                from_server_id=hid,
                from_project="web",
                to_server_id=int(other.id),
                to_project="db",
            )
        )
        u = User(email="op@example.com", hashed_password="x")
        s.add(u)
        s.commit()
        s.refresh(u)
        s.add(
            UserFavourite(
                user_id=int(u.id),
                kind="server_feature",
                server_id=hid,
                feature="docker",
            )
        )
        integ = Integration(type="nmap", name="LAN", base_url="http://local")
        s.add(integ)
        s.commit()
        s.refresh(integ)
        s.add(
            NmapDevice(
                integration_id=int(integ.id),
                identity_key="ip:10.0.0.9",
                ip_address="10.0.0.9",
                linked_server_id=hid,
            )
        )
        s.commit()

        snap = delete_server_from_fleet(s, host, confirm_name="lab-1", user_id=int(u.id))
        assert snap["certificate_targets_removed"] == 1
        assert snap["runtime_edges_removed"] == 1
        assert snap["favourites_removed"] == 1
        assert snap["nmap_devices_unlinked"] == 1
        assert s.get(Server, hid) is None
        assert s.get(Server, int(other.id)) is not None
        dev = s.exec(select(NmapDevice)).first()
        assert dev is not None
        assert dev.linked_server_id is None
