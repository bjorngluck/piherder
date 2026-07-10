"""Server host dependency check snapshot columns.

Revision ID: 011_host_deps
Revises: 010_user_login_api_actor
Create Date: 2026-07-10
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "011_host_deps"
down_revision: Union[str, None] = "010_user_login_api_actor"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(conn, table: str) -> set[str]:
    try:
        return {c["name"] for c in inspect(conn).get_columns(table)}
    except Exception:
        return set()


def upgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())
    if "server" not in tables:
        conn.commit()
        return
    cols = _cols(conn, "server")
    if "host_deps_json" not in cols:
        conn.execute(text("ALTER TABLE server ADD COLUMN host_deps_json TEXT"))
    if "host_deps_checked_at" not in cols:
        conn.execute(
            text(
                "ALTER TABLE server ADD COLUMN host_deps_checked_at "
                "TIMESTAMP WITHOUT TIME ZONE"
            )
        )
    conn.commit()


def downgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())
    if "server" not in tables:
        conn.commit()
        return
    cols = _cols(conn, "server")
    if "host_deps_checked_at" in cols:
        conn.execute(text("ALTER TABLE server DROP COLUMN host_deps_checked_at"))
    if "host_deps_json" in cols:
        conn.execute(text("ALTER TABLE server DROP COLUMN host_deps_json"))
    conn.commit()
