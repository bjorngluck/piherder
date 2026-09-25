"""Persist host OS/hardware snapshot on server.

Revision ID: 044_host_facts
Revises: 043_console_mux
Create Date: 2026-09-19
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "044_host_facts"
down_revision: Union[str, None] = "043_console_mux"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)
    if "server" not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns("server")}
    adds = [
        ("os_pretty", sa.Column("os_pretty", sa.String(length=160), nullable=True)),
        ("os_id", sa.Column("os_id", sa.String(length=32), nullable=True)),
        ("hardware", sa.Column("hardware", sa.String(length=160), nullable=True)),
        ("arch", sa.Column("arch", sa.String(length=32), nullable=True)),
        ("host_facts_json", sa.Column("host_facts_json", sa.Text(), nullable=True)),
        ("host_facts_at", sa.Column("host_facts_at", sa.DateTime(), nullable=True)),
        (
            "host_facts_status",
            sa.Column(
                "host_facts_status",
                sa.String(length=16),
                nullable=False,
                server_default="never",
            ),
        ),
        ("host_facts_error", sa.Column("host_facts_error", sa.String(length=500), nullable=True)),
    ]
    for name, col in adds:
        if name not in cols:
            op.add_column("server", col)


def downgrade() -> None:
    for name in (
        "host_facts_error",
        "host_facts_status",
        "host_facts_at",
        "host_facts_json",
        "arch",
        "hardware",
        "os_id",
        "os_pretty",
    ):
        try:
            op.drop_column("server", name)
        except Exception:
            pass
