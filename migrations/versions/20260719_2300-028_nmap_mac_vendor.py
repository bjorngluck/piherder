"""Nmap device: store MAC vendor from nmap XML.

Revision ID: 028_nmap_mac_vendor
Revises: 027_nmap_script_port
Create Date: 2026-07-19
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "028_nmap_mac_vendor"
down_revision: Union[str, None] = "027_nmap_script_port"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())
    if "nmapdevice" not in tables:
        return
    cols = {c["name"] for c in inspect(conn).get_columns("nmapdevice")}
    if "mac_vendor" not in cols:
        conn.execute(
            text(
                "ALTER TABLE nmapdevice "
                "ADD COLUMN IF NOT EXISTS mac_vendor VARCHAR(128)"
            )
        )


def downgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())
    if "nmapdevice" not in tables:
        return
    cols = {c["name"] for c in inspect(conn).get_columns("nmapdevice")}
    if "mac_vendor" in cols:
        conn.execute(text("ALTER TABLE nmapdevice DROP COLUMN IF EXISTS mac_vendor"))
