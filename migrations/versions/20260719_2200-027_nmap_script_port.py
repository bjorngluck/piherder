"""Nmap script results: port/protocol for findings cross-link.

Revision ID: 027_nmap_script_port
Revises: 026_nmap_schedule_options
Create Date: 2026-07-19
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "027_nmap_script_port"
down_revision: Union[str, None] = "026_nmap_schedule_options"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())
    if "nmapscriptresult" not in tables:
        return
    cols = {c["name"] for c in inspect(conn).get_columns("nmapscriptresult")}
    if "port" not in cols:
        conn.execute(
            text(
                "ALTER TABLE nmapscriptresult "
                "ADD COLUMN IF NOT EXISTS port INTEGER"
            )
        )
    if "protocol" not in cols:
        conn.execute(
            text(
                "ALTER TABLE nmapscriptresult "
                "ADD COLUMN IF NOT EXISTS protocol VARCHAR(16)"
            )
        )
    # Helpful for filtering findings by port
    try:
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_nmapscriptresult_port "
                "ON nmapscriptresult (port)"
            )
        )
    except Exception:
        pass


def downgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())
    if "nmapscriptresult" not in tables:
        return
    try:
        conn.execute(text("DROP INDEX IF EXISTS ix_nmapscriptresult_port"))
    except Exception:
        pass
    cols = {c["name"] for c in inspect(conn).get_columns("nmapscriptresult")}
    if "protocol" in cols:
        conn.execute(
            text("ALTER TABLE nmapscriptresult DROP COLUMN IF EXISTS protocol")
        )
    if "port" in cols:
        conn.execute(text("ALTER TABLE nmapscriptresult DROP COLUMN IF EXISTS port"))
