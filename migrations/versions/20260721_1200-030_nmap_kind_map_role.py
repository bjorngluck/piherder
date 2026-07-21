"""Nmap device: kind_override + map_role (gateway, etc.).

Revision ID: 030_nmap_kind_map_role
Revises: 029_nmap_display_name
Create Date: 2026-07-21
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "030_nmap_kind_map_role"
down_revision: Union[str, None] = "029_nmap_display_name"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())
    if "nmapdevice" not in tables:
        return
    cols = {c["name"] for c in inspect(conn).get_columns("nmapdevice")}
    if "kind_override" not in cols:
        conn.execute(
            text(
                "ALTER TABLE nmapdevice "
                "ADD COLUMN IF NOT EXISTS kind_override VARCHAR(32)"
            )
        )
    if "map_role" not in cols:
        conn.execute(
            text(
                "ALTER TABLE nmapdevice "
                "ADD COLUMN IF NOT EXISTS map_role VARCHAR(32)"
            )
        )


def downgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())
    if "nmapdevice" not in tables:
        return
    cols = {c["name"] for c in inspect(conn).get_columns("nmapdevice")}
    if "map_role" in cols:
        conn.execute(text("ALTER TABLE nmapdevice DROP COLUMN IF EXISTS map_role"))
    if "kind_override" in cols:
        conn.execute(
            text("ALTER TABLE nmapdevice DROP COLUMN IF EXISTS kind_override")
        )
