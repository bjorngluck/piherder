"""Run Alembic migrations programmatically (web/celery startup)."""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def run_alembic_upgrade() -> None:
    """Apply migrations to head. Safe to call on every startup."""
    try:
        from alembic import command
        from alembic.config import Config
    except ImportError:
        logger.warning("alembic not installed — skip migrations")
        return

    root = Path(__file__).resolve().parents[1]
    ini = root / "alembic.ini"
    if not ini.is_file():
        logger.warning("alembic.ini missing — skip migrations")
        return

    cfg = Config(str(ini))
    cfg.set_main_option("script_location", str(root / "migrations"))
    try:
        from .config import settings

        cfg.set_main_option("sqlalchemy.url", settings.DATABASE_URL)
    except Exception as e:
        logger.warning("Could not set DATABASE_URL for alembic: %s", e)

    try:
        command.upgrade(cfg, "head")
        logger.info("Alembic migrations applied (head)")
    except Exception as e:
        logger.error("Alembic upgrade failed: %s", e)
        raise
