"""Nmap device: operator display_name for map labels.

Revision ID: 029_nmap_display_name
Revises: 028_nmap_mac_vendor
Create Date: 2026-07-20
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "029_nmap_display_name"
down_revision: Union[str, None] = "028_nmap_mac_vendor"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())
    if "nmapdevice" not in tables:
        return
    cols = {c["name"] for c in inspect(conn).get_columns("nmapdevice")}
    if "display_name" not in cols:
        conn.execute(
            text(
                "ALTER TABLE nmapdevice "
                "ADD COLUMN IF NOT EXISTS display_name VARCHAR(128)"
            )
        )


def downgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())
    if "nmapdevice" not in tables:
        return
    cols = {c["name"] for c in inspect(conn).get_columns("nmapdevice")}
    if "display_name" in cols:
        conn.execute(text("ALTER TABLE nmapdevice DROP COLUMN IF EXISTS display_name"))
