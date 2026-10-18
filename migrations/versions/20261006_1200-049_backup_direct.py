"""Per-host direct backup opt-in (v1.11 Path C).

Revision ID: 049_backup_direct
Revises: 048_mcp_oauth
Create Date: 2026-10-06
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "049_backup_direct"
down_revision: Union[str, None] = "048_mcp_oauth"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)
    if "server" not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns("server")}
    if "backup_direct" not in cols:
        op.add_column(
            "server",
            sa.Column(
                "backup_direct",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )


def downgrade() -> None:
    try:
        op.drop_column("server", "backup_direct")
    except Exception:
        pass
