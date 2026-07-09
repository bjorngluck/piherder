from sqlmodel import SQLModel, create_engine, Session
from sqlalchemy import text, inspect
from .config import settings
from .models import DockerVersion  # ensure registered for create_all / alembic

engine = create_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_size=10,
    max_overflow=10,
    pool_pre_ping=True,
)


def get_session():
    with Session(engine) as session:
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
