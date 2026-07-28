"""User.session_version for JWT invalidation (admin credential recovery).

Revision ID: 031_user_session_version
Revises: 030_nmap_kind_map_role
Create Date: 2026-07-28
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "031_user_session_version"
down_revision: Union[str, None] = "030_nmap_kind_map_role"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())
    if "user" not in tables:
        return
    cols = {c["name"] for c in inspect(conn).get_columns("user")}
    if "session_version" not in cols:
        conn.execute(
            text(
                'ALTER TABLE "user" '
                "ADD COLUMN IF NOT EXISTS session_version INTEGER DEFAULT 0 NOT NULL"
            )
        )
        # Backfill any nulls if the DB dialect ignored NOT NULL default edge cases
        conn.execute(
            text(
                'UPDATE "user" SET session_version = 0 WHERE session_version IS NULL'
            )
        )


def downgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())
    if "user" not in tables:
        return
    cols = {c["name"] for c in inspect(conn).get_columns("user")}
    if "session_version" in cols:
        conn.execute(text('ALTER TABLE "user" DROP COLUMN IF EXISTS session_version'))
