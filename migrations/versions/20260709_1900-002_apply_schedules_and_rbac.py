"""Apply schedules (OS/container) + User.role for RBAC.

Revision ID: 002_apply_rbac
Revises: 001_backup_path_rules
Create Date: 2026-07-09
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "002_apply_rbac"
down_revision: Union[str, None] = "001_backup_path_rules"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(conn, name: str) -> bool:
    return name in inspect(conn).get_table_names()


def _has_column(conn, table: str, column: str) -> bool:
    if not _has_table(conn, table):
        return False
    cols = [c["name"] for c in inspect(conn).get_columns(table)]
    return column in cols


def _add_column_if_missing(conn, table: str, column: str, coltype: str) -> None:
    if _has_column(conn, table, column):
        return
    conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN {column} {coltype}'))


def upgrade() -> None:
    conn = op.get_bind()
    server_cols = [
        ("os_apply_enabled", "BOOLEAN DEFAULT FALSE"),
        ("os_apply_schedule", "VARCHAR"),
        ("os_apply_steps", "TEXT"),
        ("os_apply_only_if_updates", "BOOLEAN DEFAULT TRUE"),
        ("container_apply_enabled", "BOOLEAN DEFAULT FALSE"),
        ("container_apply_schedule", "VARCHAR"),
        ("container_apply_only_if_updates", "BOOLEAN DEFAULT TRUE"),
    ]
    for col, typ in server_cols:
        _add_column_if_missing(conn, "server", col, typ)

    _add_column_if_missing(conn, "user", "role", "VARCHAR DEFAULT 'admin'")
    # Existing rows: ensure non-null admin (first/legacy operators keep full access)
    if _has_column(conn, "user", "role"):
        conn.execute(text("UPDATE \"user\" SET role = 'admin' WHERE role IS NULL OR role = ''"))


def downgrade() -> None:
    conn = op.get_bind()
    for col in (
        "os_apply_enabled",
        "os_apply_schedule",
        "os_apply_steps",
        "os_apply_only_if_updates",
        "container_apply_enabled",
        "container_apply_schedule",
        "container_apply_only_if_updates",
    ):
        if _has_column(conn, "server", col):
            op.drop_column("server", col)
    if _has_column(conn, "user", "role"):
        op.drop_column("user", "role")
