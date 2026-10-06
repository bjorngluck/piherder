"""Which saved destinations a direct host pushes to.

Revision ID: 050_backup_direct_targets
Revises: 049_backup_direct
Create Date: 2026-10-06
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "050_backup_direct_targets"
down_revision: Union[str, None] = "049_backup_direct"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)
    if "server" not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns("server")}
    if "backup_direct_targets" not in cols:
        op.add_column(
            "server",
            sa.Column("backup_direct_targets", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    try:
        op.drop_column("server", "backup_direct_targets")
    except Exception:
        pass
