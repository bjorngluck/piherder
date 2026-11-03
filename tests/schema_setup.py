"""Schema for the shared test engine.

Postgres runs Alembic. SQLite cannot execute the migration SQL, so those
engines still use create_all. The CI migration job runs upgrade and downgrade
on Postgres, and a downgrade error fails that job.
"""
from __future__ import annotations

from pathlib import Path


def apply_test_schema(engine) -> None:
    dialect = getattr(getattr(engine, "dialect", None), "name", "")
    if dialect == "postgresql":
        upgrade_engine(engine)
        return
    from sqlmodel import SQLModel

    SQLModel.metadata.create_all(engine)


def upgrade_engine(engine) -> None:
    """Apply Alembic revisions to head on this engine."""
    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", str(engine.url))
    command.upgrade(cfg, "head")
