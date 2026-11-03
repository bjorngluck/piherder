"""Job and audit log filter indexes are created and removed."""
from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlmodel import SQLModel

from app.models import AuditLog, Job

_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations/versions/20261103_1200-054_job_audit_indexes.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("mig_054_indexes", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _names(conn, table: str) -> set[str]:
    return {ix["name"] for ix in inspect(conn).get_indexes(table)}


def test_models_declare_the_filter_indexes():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine, tables=[Job.__table__, AuditLog.__table__])
    with engine.connect() as conn:
        assert "ix_job_server_status_created" in _names(conn, "job")
        assert "ix_auditlog_server_action_started" in _names(conn, "auditlog")


def test_migration_upgrade_and_downgrade_are_reversible():
    mod = _load()
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE job ("
                "id INTEGER PRIMARY KEY, server_id INTEGER, "
                "status VARCHAR, created_at DATETIME)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE auditlog ("
                "id INTEGER PRIMARY KEY, server_id INTEGER, "
                "action VARCHAR, started_at DATETIME)"
            )
        )
        ctx = MigrationContext.configure(conn)
        mod.op = Operations(ctx)
        mod.upgrade()
        assert "ix_job_server_status_created" in _names(conn, "job")
        assert "ix_auditlog_server_action_started" in _names(conn, "auditlog")
        job_cols = next(
            ix["column_names"]
            for ix in inspect(conn).get_indexes("job")
            if ix["name"] == "ix_job_server_status_created"
        )
        assert list(job_cols) == ["server_id", "status", "created_at"]
        audit_cols = next(
            ix["column_names"]
            for ix in inspect(conn).get_indexes("auditlog")
            if ix["name"] == "ix_auditlog_server_action_started"
        )
        assert list(audit_cols) == ["server_id", "action", "started_at"]

        mod.upgrade()
        assert "ix_job_server_status_created" in _names(conn, "job")

        mod.downgrade()
        assert "ix_job_server_status_created" not in _names(conn, "job")
        assert "ix_auditlog_server_action_started" not in _names(conn, "auditlog")
        mod.downgrade()
