"""Baseline stamp helpers + backup_path_rules column.

Revision ID: 001_backup_path_rules
Revises:
Create Date: 2026-07-09

Idempotent: safe on existing PiHerder DBs that were created via create_all +
runtime ALTER TABLE. Also creates tables if missing (new installs).
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text

revision: str = "001_backup_path_rules"
down_revision: Union[str, None] = None
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
    # Quote identifiers; coltype is trusted (migration-controlled)
    conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN {column} {coltype}'))


def upgrade() -> None:
    conn = op.get_bind()

    # Prefer create_all for brand-new DBs (full SQLModel schema)
    from sqlmodel import SQLModel
    from app import models  # noqa: F401

    SQLModel.metadata.create_all(bind=conn)

    # Columns historically added via main.py runtime ALTER (idempotent)
    server_cols = [
        ("backup_schedule", "VARCHAR"),
        ("backup_dest_root", "VARCHAR"),
        ("backup_folder_name", "VARCHAR"),
        ("last_backup_at", "TIMESTAMP"),
        ("sort_order", "INTEGER DEFAULT 0"),
        ("os_check_enabled", "BOOLEAN DEFAULT FALSE"),
        ("os_check_schedule", "VARCHAR"),
        ("last_os_check_at", "TIMESTAMP"),
        ("os_updates_count", "INTEGER"),
        ("reboot_pending", "BOOLEAN DEFAULT FALSE"),
        ("os_updates_summary", "TEXT"),
        ("container_check_enabled", "BOOLEAN DEFAULT FALSE"),
        ("container_check_schedule", "VARCHAR"),
        ("last_container_check_at", "TIMESTAMP"),
        ("container_updates_count", "INTEGER"),
        ("container_updates_summary", "TEXT"),
        ("backup_path_rules", "TEXT"),
    ]
    user_cols = [
        ("display_name", "VARCHAR"),
        ("avatar_path", "VARCHAR"),
        ("updated_at", "TIMESTAMP"),
        ("totp_secret_encrypted", "TEXT"),
        ("totp_enabled", "BOOLEAN DEFAULT FALSE"),
        ("totp_confirmed_at", "TIMESTAMP"),
    ]
    for col, typ in server_cols:
        _add_column_if_missing(conn, "server", col, typ)
    for col, typ in user_cols:
        _add_column_if_missing(conn, "user", col, typ)


def downgrade() -> None:
    conn = op.get_bind()
    if _has_column(conn, "server", "backup_path_rules"):
        op.drop_column("server", "backup_path_rules")
