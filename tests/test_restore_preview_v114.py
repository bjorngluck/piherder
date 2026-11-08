"""Dry-run restore preview: a read, not a host write."""
from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.main import app
from app.models import Server, User
from app.security.auth import get_password_hash
from app.services import api_tokens as tok_svc


def _engine(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'preview.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _session(engine):
    def _open():
        with Session(engine) as session:
            yield session

    return _open


def test_restore_preview_is_a_read_and_does_not_write(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    monkeypatch.setattr(
        "app.services.server_job_lock.try_acquire_server_lock",
        lambda *_args, **_kwargs: "preview-tok",
    )
    monkeypatch.setattr(
        "app.services.server_job_lock.release_server_lock",
        lambda *_args, **_kwargs: True,
    )
    engine = _engine(tmp_path)
    app.dependency_overrides[get_session] = _session(engine)
    client = TestClient(app, raise_server_exceptions=False)
    try:
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
            host = Server(
                name="Host A",
                hostname="host-a.example.com",
                ssh_username="pi",
                ip_address="192.0.2.10",
                backup_enabled=True,
            )
            quiet = Server(
                name="Host B",
                hostname="host-b.example.com",
                ssh_username="pi",
                ip_address="192.0.2.11",
                backup_enabled=False,
            )
            session.add(host)
            session.add(quiet)
            session.commit()
            session.refresh(user)
            session.refresh(host)
            session.refresh(quiet)
            host_id = host.id
            quiet_id = quiet.id
            _read, read_plain = tok_svc.create_api_token(
                session, name="read", created_by=user, scopes=["read"]
            )
            _jobs, jobs_plain = tok_svc.create_api_token(
                session, name="jobs", created_by=user, scopes=["jobs"]
            )
            _docker, docker_plain = tok_svc.create_api_token(
                session, name="docker", created_by=user, scopes=["read", "feature:docker"]
            )
        read = {"Authorization": f"Bearer {read_plain}"}
        denied = client.get(
            f"/api/v1/servers/{host_id}/backup/restore-preview",
            headers={"Authorization": f"Bearer {jobs_plain}"},
            params={"source": "/data/app"},
        )
        assert denied.status_code == 403
        limited = client.get(
            f"/api/v1/servers/{host_id}/backup/restore-preview",
            headers={"Authorization": f"Bearer {docker_plain}"},
            params={"source": "/data/app"},
        )
        assert limited.status_code == 403
        missing = client.get(
            f"/api/v1/servers/{host_id}/backup/restore-preview",
            headers=read,
        )
        assert missing.status_code == 400
        off = client.get(
            f"/api/v1/servers/{quiet_id}/backup/restore-preview",
            headers=read,
            params={"source": "/data/app"},
        )
        assert off.status_code == 400
        unknown = client.get(
            f"/api/v1/servers/{host_id}/backup/restore-preview",
            headers=read,
            params={"source": "/data/app", "destination_id": 9},
        )
        assert unknown.status_code == 400
        assert "not available" in unknown.json()["detail"]
        with patch(
            "app.services.backup_restore.preview_restore",
            return_value={
                "rc": 0,
                "dry_run": True,
                "wrote": False,
                "source": "/data/app",
                "remote": "pi@host-a.example.com:/data/app/",
                "destination": "/srv/secret/tree",
                "output": "pi@host-a.example.com",
                "changes": [{"path": "app.db", "action": "update"}],
                "change_count": 1,
                "truncated": False,
                "summary": "Dry-run restore ok · /data/app",
                "error": None,
            },
        ) as preview:
            shown = client.get(
                f"/api/v1/servers/{host_id}/backup/restore-preview",
                headers=read,
                params={"source": "/data/app"},
            )
        preview.assert_called_once()
        assert preview.call_args.kwargs["pull_from"] is None
        assert shown.status_code == 200
        body = shown.json()
        assert body["ok"] is True
        assert body["wrote"] is False
        assert body["dry_run"] is True
        assert body["changes"] == [{"path": "app.db", "action": "update"}]
        assert "host-a.example.com" not in shown.text
        assert "/srv/secret" not in shown.text
        assert "remote" not in body
        assert "destination" not in body
        assert "output" not in body
        paths = [row["path"] for row in tok_svc.api_meta_dict()["endpoints"]]
        preview_row = [
            row for row in tok_svc.api_meta_dict()["endpoints"]
            if row["path"] == "/api/v1/servers/{id}/backup/restore-preview"
        ][0]
        assert "/api/v1/servers/{id}/backup/restore-preview" in paths
        assert "120 second" in preview_row["summary"]
        assert "409" in preview_row["summary"]
        monkeypatch.setattr(
            "app.services.server_job_lock.try_acquire_server_lock",
            lambda *_args, **_kwargs: None,
        )
        busy = client.get(
            f"/api/v1/servers/{host_id}/backup/restore-preview",
            headers=read,
            params={"source": "/data/app"},
        )
        assert busy.status_code == 409
        assert busy.json()["detail"] == "This host is busy"
    finally:
        app.dependency_overrides.pop(get_session, None)
