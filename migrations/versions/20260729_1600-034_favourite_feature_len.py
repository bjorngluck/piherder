"""Widen UserFavourite.feature for integration ids / app page keys.

Revision ID: 034_favourite_feature_len
Revises: 033_user_favourites
Create Date: 2026-07-29
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "034_favourite_feature_len"
down_revision: Union[str, None] = "033_user_favourites"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())
    if "userfavourite" not in tables:
        return
    # PostgreSQL: widen feature for allowlisted keys + integration ids
    try:
        conn.execute(
            text(
                "ALTER TABLE userfavourite ALTER COLUMN feature TYPE VARCHAR(64)"
            )
        )
    except Exception:
        # SQLite / already wide — ignore
        pass


def downgrade() -> None:
    pass
