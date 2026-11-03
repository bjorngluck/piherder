"""Self-backup restores onto an empty database and the app still starts."""
from __future__ import annotations

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.database import get_engine
from app.main import app
from app.models import Server, User
from app.services import app_settings as aset
from app.services import herder_backup as hb


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def test_restore_drill_boots_with_the_host_and_settings(tmp_path, monkeypatch):
    import app.database as database

    source = _engine()
    fresh = _engine()
    previous = database.engine
    database.engine = source
    saved: dict = {}
    monkeypatch.setattr(hb, "HERDER_BACKUP_DIR", tmp_path)
    monkeypatch.setattr(hb, "archive_dir_candidates", lambda: [tmp_path])
    monkeypatch.setattr(hb, "_fix_postgres_sequences", lambda: None)
    monkeypatch.setattr(hb, "_restore_avatars_from_tar", lambda path: 0)
    monkeypatch.setattr(
        "app.db_migrate.run_alembic_upgrade",
        lambda: None,
    )

    def _save(partial):
        saved.update(partial or {})
        return dict(saved)

    monkeypatch.setattr(aset, "replace_settings", _save)
    monkeypatch.setattr(aset, "clear_cache", lambda: None)
    try:
        with Session(source) as session:
            session.add(
                User(
                    email="user@example.com",
                    hashed_password="hashed",
                    role="admin",
                    is_active=True,
                )
            )
            session.add(
                Server(
                    name="host-a",
                    hostname="host-a.example.com",
                    ssh_username="pi",
                    os_type="debian",
                )
            )
            session.commit()
        aset._cache = {"keep": 4, "timezone": "UTC"}
        monkeypatch.setattr(aset, "load_settings", lambda: {"keep": 4, "timezone": "UTC"})

        archive = hb.create_herder_backup(include_audit=False, config_only=True)
        assert archive.is_file()

        database.engine = fresh
        result = hb.restore_herder_backup(str(archive), dry_run=False)
        assert result["restored_servers"] == 1
        assert result["restored_users"] == 1
        assert result["restored_herder_config"] is True
        assert saved.get("keep") == 4

        from fastapi.testclient import TestClient

        with TestClient(app) as client:
            health = client.get("/health")
            assert health.status_code == 200
            assert get_engine() is fresh
            with Session(fresh) as session:
                row = session.exec(select(Server)).one()
                user = session.exec(select(User)).one()
            assert row.name == "host-a"
            assert row.hostname == "host-a.example.com"
            assert user.email == "user@example.com"
    finally:
        database.engine = previous
        aset._cache = None
