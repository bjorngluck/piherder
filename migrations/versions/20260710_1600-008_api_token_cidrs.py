"""API token allowed_cidrs column for IP allowlists.

Revision ID: 008_api_token_cidrs
Revises: 007_api_tokens
Create Date: 2026-07-10
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "008_api_token_cidrs"
down_revision: Union[str, None] = "007_api_tokens"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(conn, table: str, column: str) -> bool:
    if table not in inspect(conn).get_table_names():
        return False
    return column in [c["name"] for c in inspect(conn).get_columns(table)]


def upgrade() -> None:
    conn = op.get_bind()
    if not _has_column(conn, "apitoken", "allowed_cidrs"):
        conn.execute(text("ALTER TABLE apitoken ADD COLUMN allowed_cidrs TEXT"))
    conn.commit()


def downgrade() -> None:
    conn = op.get_bind()
    if _has_column(conn, "apitoken", "allowed_cidrs"):
        conn.execute(text("ALTER TABLE apitoken DROP COLUMN allowed_cidrs"))
    conn.commit()
