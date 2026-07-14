"""Alembic environment — uses PiHerder settings.DATABASE_URL and SQLModel metadata."""
from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Ensure project root is importable when running `alembic` from container or host
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlmodel import SQLModel  # noqa: E402
from app.config import settings  # noqa: E402
from app import models  # noqa: F401,E402 — register all tables on metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override ini placeholder with real URL
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
        # SQLAlchemy 2.x: ensure migration DDL + alembic_version stamp are committed.
        # Without this, upgrades can log as applied while the transaction rolls back
        # on connection close — leaving the app schema behind the model (500s).
        try:
            connection.commit()
        except Exception:
            pass


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
