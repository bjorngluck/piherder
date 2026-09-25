"""Host CPU / memory / disk snapshot columns.

Revision ID: 045_host_resources
Revises: 044_host_facts
Create Date: 2026-09-19
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "045_host_resources"
down_revision: Union[str, None] = "044_host_facts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)
    if "server" not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns("server")}
    adds = [
        ("cpu_cores", sa.Column("cpu_cores", sa.Integer(), nullable=True)),
        ("cpu_load", sa.Column("cpu_load", sa.Float(), nullable=True)),
        ("memory_total_bytes", sa.Column("memory_total_bytes", sa.BigInteger(), nullable=True)),
        ("memory_used_bytes", sa.Column("memory_used_bytes", sa.BigInteger(), nullable=True)),
        ("disk_total_bytes", sa.Column("disk_total_bytes", sa.BigInteger(), nullable=True)),
        ("disk_used_bytes", sa.Column("disk_used_bytes", sa.BigInteger(), nullable=True)),
    ]
    for name, col in adds:
        if name not in cols:
            op.add_column("server", col)


def downgrade() -> None:
    for name in (
        "disk_used_bytes",
        "disk_total_bytes",
        "memory_used_bytes",
        "memory_total_bytes",
        "cpu_load",
        "cpu_cores",
    ):
        try:
            op.drop_column("server", name)
        except Exception:
            pass
