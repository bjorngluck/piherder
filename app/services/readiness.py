"""Process readiness and the worker ping used by the container healthcheck."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import text
from sqlmodel import Session

from ..database import get_engine


def check_database() -> bool:
    with Session(get_engine()) as session:
        session.execute(text("SELECT 1"))
    return True


def check_redis() -> bool:
    """Ping the same Redis URLs the boot banner checks.

    The first two are the broker and result backend Celery resolved,
    including the localhost default when those variables are empty.
    One failure is not ready.
    """
    import redis

    from ..security.boot_secrets import redis_urls_from_env

    urls = redis_urls_from_env()
    if not urls:
        return False
    for url in urls:
        client = redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
        try:
            if not client.ping():
                return False
        finally:
            try:
                client.close()
            except Exception:
                pass
    return True


def migration_matches_head() -> bool:
    """True when the database revision is the migration head."""
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    root = Path(__file__).resolve().parents[2]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    script = ScriptDirectory.from_config(cfg)
    heads = set(script.get_heads())
    with get_engine().connect() as conn:
        context = MigrationContext.configure(conn)
        current = set(context.get_current_heads())
    return bool(current) and current == heads


def worker_responds() -> bool:
    """True when at least one Celery worker answers ping."""
    from ..celery_app import celery

    inspector = celery.control.inspect(timeout=5.0)
    if inspector is None:
        return False
    return bool(inspector.ping())


def readiness() -> dict[str, str]:
    """Check database, Redis, and migration head. Values are ok or error."""
    checks = {"database": "error", "redis": "error", "migrations": "error"}
    try:
        if check_database():
            checks["database"] = "ok"
    except Exception:
        checks["database"] = "error"
    try:
        if check_redis():
            checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "error"
    try:
        if migration_matches_head():
            checks["migrations"] = "ok"
    except Exception:
        checks["migrations"] = "error"
    return checks
