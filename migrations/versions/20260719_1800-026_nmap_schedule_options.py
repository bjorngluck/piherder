"""Nmap schedule options_json (vuln scripts / scan flags).

Revision ID: 026_nmap_schedule_options
Revises: 025_nmap_discovery
Create Date: 2026-07-19
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "026_nmap_schedule_options"
down_revision: Union[str, None] = "025_nmap_discovery"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())
    if "nmapscanschedule" not in tables:
        return
    cols = {c["name"] for c in inspect(conn).get_columns("nmapscanschedule")}
    if "options_json" not in cols:
        conn.execute(
            text(
                "ALTER TABLE nmapscanschedule "
                "ADD COLUMN IF NOT EXISTS options_json TEXT"
            )
        )


def downgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())
    if "nmapscanschedule" not in tables:
        return
    cols = {c["name"] for c in inspect(conn).get_columns("nmapscanschedule")}
    if "options_json" in cols:
        conn.execute(text("ALTER TABLE nmapscanschedule DROP COLUMN IF EXISTS options_json"))
