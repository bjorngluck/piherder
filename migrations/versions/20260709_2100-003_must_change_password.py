"""User.must_change_password for first-login reset.

Revision ID: 003_must_change_password
Revises: 002_apply_rbac
Create Date: 2026-07-09
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "003_must_change_password"
down_revision: Union[str, None] = "002_apply_rbac"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(conn, table: str, column: str) -> bool:
    if table not in inspect(conn).get_table_names():
        return False
    return column in [c["name"] for c in inspect(conn).get_columns(table)]


def upgrade() -> None:
    conn = op.get_bind()
    if not _has_column(conn, "user", "must_change_password"):
        conn.execute(
            text('ALTER TABLE "user" ADD COLUMN must_change_password BOOLEAN DEFAULT FALSE')
        )


def downgrade() -> None:
    conn = op.get_bind()
    if _has_column(conn, "user", "must_change_password"):
        op.drop_column("user", "must_change_password")
