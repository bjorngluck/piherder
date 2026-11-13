"""051 and 052 drop a column only when it is there. Real errors surface."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

_ROOT = Path(__file__).resolve().parents[1]
_051 = _ROOT / "migrations/versions/20261023_1600-051_docker_prune_schedule.py"
_052 = _ROOT / "migrations/versions/20261027_1200-052_schedule_catchup.py"
_040 = _ROOT / "migrations/versions/20260819_1200-040_ssh_identities.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _downgrade_source(path: Path) -> str:
    text_src = path.read_text(encoding="utf-8")
    start = text_src.index("def downgrade")
    return text_src[start:]


def test_downgrade_sources_do_not_swallow_errors():
    for path in (_040, _051, _052):
        body = _downgrade_source(path)
        assert "except" not in body


def test_051_downgrade_skips_a_missing_column_and_drops_a_present_one():
    mod = _load(_051, "mig_051_downgrade")
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE server (id INTEGER PRIMARY KEY)"))
        ctx = MigrationContext.configure(conn)
        mod.op = Operations(ctx)
        mod.downgrade()
        names = {c["name"] for c in inspect(conn).get_columns("server")}
        assert "docker_prune_schedule" not in names

        conn.execute(text("ALTER TABLE server ADD COLUMN docker_prune_schedule VARCHAR"))
        mod.downgrade()
        names = {c["name"] for c in inspect(conn).get_columns("server")}
        assert "docker_prune_schedule" not in names


def test_051_downgrade_lets_a_drop_error_surface():
    mod = _load(_051, "mig_051_downgrade_error")
    engine = create_engine("sqlite://")

    class _Boom:
        def __init__(self, conn):
            self.conn = conn

        def get_bind(self):
            return self.conn

        def drop_column(self, table, name):
            raise RuntimeError(f"drop failed {table}.{name}")

    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE server ("
                "id INTEGER PRIMARY KEY, docker_prune_schedule VARCHAR)"
            )
        )
        mod.op = _Boom(conn)
        with pytest.raises(RuntimeError, match="drop failed"):
            mod.downgrade()


def test_052_downgrade_skips_missing_columns_and_drops_present_ones():
    mod = _load(_052, "mig_052_downgrade")
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE server (id INTEGER PRIMARY KEY)"))
        ctx = MigrationContext.configure(conn)
        mod.op = Operations(ctx)
        mod.downgrade()
        assert "backup_catchup_enabled" not in {
            c["name"] for c in inspect(conn).get_columns("server")
        }

        conn.execute(
            text(
                "ALTER TABLE server ADD COLUMN backup_catchup_enabled BOOLEAN "
                "NOT NULL DEFAULT 0"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE server ADD COLUMN container_check_catchup_enabled "
                "BOOLEAN NOT NULL DEFAULT 0"
            )
        )
        mod.downgrade()
        names = {c["name"] for c in inspect(conn).get_columns("server")}
        assert "backup_catchup_enabled" not in names
        assert "container_check_catchup_enabled" not in names


def test_052_downgrade_lets_a_drop_error_surface():
    mod = _load(_052, "mig_052_downgrade_error")
    engine = create_engine("sqlite://")

    class _Boom:
        def __init__(self, conn):
            self.conn = conn

        def get_bind(self):
            return self.conn

        def drop_column(self, table, name):
            raise RuntimeError(f"drop failed {table}.{name}")

    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE server ("
                "id INTEGER PRIMARY KEY, "
                "backup_catchup_enabled BOOLEAN NOT NULL DEFAULT 0, "
                "container_check_catchup_enabled BOOLEAN NOT NULL DEFAULT 0)"
            )
        )
        mod.op = _Boom(conn)
        with pytest.raises(RuntimeError, match="drop failed"):
            mod.downgrade()
