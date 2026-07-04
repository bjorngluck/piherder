from sqlmodel import SQLModel, create_engine, Session
from sqlalchemy import text, inspect
from .config import settings
from .models import DockerVersion  # ensure registered for create_all / alembic

engine = create_engine(settings.DATABASE_URL, echo=False)


def get_session():
    with Session(engine) as session:
        yield session


def ensure_server_columns():
    """Ensure additional columns exist (e.g. sort_order for manual ordering).
    Safe no-op if already present.
    """
    try:
        with engine.connect() as conn:
            # Postgres supports IF NOT EXISTS
            conn.execute(text(
                "ALTER TABLE server ADD COLUMN IF NOT EXISTS sort_order INTEGER DEFAULT 0"
            ))
            conn.commit()
    except Exception:
        # Fallback for SQLite or older DBs
        try:
            with engine.connect() as conn:
                insp = inspect(engine)
                cols = [c["name"] for c in insp.get_columns("server")]
                if "sort_order" not in cols:
                    conn.execute(text("ALTER TABLE server ADD COLUMN sort_order INTEGER DEFAULT 0"))
                    conn.commit()
        except Exception:
            pass  # will surface on query if truly broken


def init_db():
    # Called from lifespan or alembic
    SQLModel.metadata.create_all(engine)
    ensure_server_columns()


# Backwards-compatibility alias so that code expecting the classic FastAPI/SQLAlchemy
# `get_db` dependency (e.g. older Celery tasks, scripts, or notebooks) continues to work.
# Both are generator-based session providers.
get_db = get_session
