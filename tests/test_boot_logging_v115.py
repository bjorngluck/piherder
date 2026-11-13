"""Boot logging stays intact after Alembic configures logging, and boot stops when upgrade fails."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.db_migrate import configure_migration_logging
from app.main import app

ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = ROOT / "alembic.ini"


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def _snapshot_logging():
    root = logging.getLogger()
    loggers = {}
    for name, obj in logging.root.manager.loggerDict.items():
        if isinstance(obj, logging.Logger):
            loggers[name] = (obj.level, obj.disabled, list(obj.handlers), obj.propagate)
    return {
        "root_level": root.level,
        "root_handlers": list(root.handlers),
        "root_disabled": root.disabled,
        "manager_disable": logging.root.manager.disable,
        "loggers": loggers,
    }


def _restore_logging(snap) -> None:
    root = logging.getLogger()
    keep = set(snap["root_handlers"])
    for handler in list(root.handlers):
        if handler not in keep:
            handler.close()
    root.handlers[:] = list(snap["root_handlers"])
    root.setLevel(snap["root_level"])
    root.disabled = snap["root_disabled"]
    logging.root.manager.disable = snap["manager_disable"]
    for name, (level, disabled, handlers, propagate) in snap["loggers"].items():
        logger = logging.getLogger(name)
        owned = set(handlers)
        for handler in list(logger.handlers):
            if handler not in owned:
                handler.close()
        logger.handlers[:] = list(handlers)
        logger.setLevel(level)
        logger.disabled = disabled
        logger.propagate = propagate


def _after_migration_logging(logger: logging.Logger, level: int):
    """Attach a handler, then apply the logging setup migrations use."""
    logger.setLevel(level)
    logger.disabled = False
    capture = _Capture()
    capture.setLevel(level)
    logger.addHandler(capture)
    configure_migration_logging(str(ALEMBIC_INI))
    return capture


def test_scheduler_error_emits_after_migrations():
    env = (ROOT / "migrations" / "env.py").read_text(encoding="utf-8")
    assert "configure_migration_logging" in env
    import app.services.scheduler as scheduler_mod

    snap = _snapshot_logging()
    capture = None
    try:
        capture = _after_migration_logging(scheduler_mod.logger, logging.ERROR)
        scheduler_mod.logger.error("scheduler still logs")
        assert "scheduler still logs" in capture.messages
    finally:
        if capture is not None:
            scheduler_mod.logger.removeHandler(capture)
        _restore_logging(snap)


def test_failed_login_logs_after_migrations(tmp_path, monkeypatch):
    import app.routers.auth as auth_routes

    engine = create_engine(
        f"sqlite:///{tmp_path / 'login.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def _session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    monkeypatch.setattr(auth_routes, "rate_limit_auth", lambda *a, **k: True)
    snap = _snapshot_logging()
    capture = None
    client = TestClient(app, raise_server_exceptions=False)
    try:
        capture = _after_migration_logging(auth_routes.logger, logging.WARNING)
        response = client.post(
            "/auth/login",
            data={"email": "user@example.com", "password": "WrongPass1ok"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "error=invalid" in (response.headers.get("location") or "")
        assert "login failed" in capture.messages
    finally:
        if capture is not None:
            auth_routes.logger.removeHandler(capture)
        _restore_logging(snap)
        app.dependency_overrides.pop(get_session, None)


def test_boot_aborts_on_migration_failure(monkeypatch):
    from app import main as main_mod

    called = {"init": 0}

    def boom():
        raise RuntimeError("migration failed")

    def init_db():
        called["init"] += 1

    monkeypatch.setattr("app.db_migrate.run_alembic_upgrade", boom)
    monkeypatch.setattr(main_mod, "init_db", init_db)

    async def _run():
        async with main_mod.lifespan(object()):
            return True

    with pytest.raises(RuntimeError, match="migration failed"):
        asyncio.run(_run())
    assert called["init"] == 0
