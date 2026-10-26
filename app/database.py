import os

from sqlmodel import SQLModel, create_engine, Session
from sqlalchemy import text, inspect
from sqlalchemy.pool import StaticPool
from .config import settings
from .models import (  # ensure registered for create_all / alembic
    DockerVersion,
    AppSetting,
    ServiceTemplate,
    StackDeployment,
    ComposeProjectMeta,
)


def _sqlite_engine_permitted() -> bool:
    """PIHERDER_TESTING=1 permits the sqlite process engine. Nothing else reads it."""
    return (os.environ.get("PIHERDER_TESTING") or "").strip() == "1"


def _create_app_engine(url: str):
    """Postgres pool for the web app and the worker.

    A sqlite URL is the test suite only. Without PIHERDER_TESTING=1 the process
    refuses to start, so a web or worker launch cannot open a sqlite file.
    """
    if (url or "").startswith("sqlite"):
        if not _sqlite_engine_permitted():
            raise RuntimeError(
                "DATABASE_URL is sqlite. The web app and the worker refuse to start "
                "unless PIHERDER_TESTING=1."
            )
        return create_engine(
            url,
            echo=False,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    return create_engine(
        url,
        echo=False,
        pool_size=10,
        max_overflow=10,
        pool_pre_ping=True,
    )


engine = _create_app_engine(settings.DATABASE_URL)


def get_engine():
    """The process SQLAlchemy engine. Call this instead of importing ``engine``."""
    return engine


def get_session():
    with Session(get_engine()) as session:
        yield session


_schema_ready = False


def ensure_server_columns():
    """Run once at startup only — never from request handlers (ALTER TABLE locks the DB)."""
    global _schema_ready
    if _schema_ready:
        return
    try:
        insp = inspect(engine)
        cols = [c["name"] for c in insp.get_columns("server")]
        if "sort_order" not in cols:
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE server ADD COLUMN sort_order INTEGER DEFAULT 0"))
                conn.commit()
        _schema_ready = True
    except Exception:
        pass


def init_db():
    # Called from lifespan after Alembic (or as fallback). Prefer migrations for new columns.
    SQLModel.metadata.create_all(engine)
    ensure_server_columns()


# Backwards-compatibility alias so that code expecting the classic FastAPI/SQLAlchemy
# `get_db` dependency (e.g. older Celery tasks, scripts, or notebooks) continues to work.
# Both are generator-based session providers.
get_db = get_session
