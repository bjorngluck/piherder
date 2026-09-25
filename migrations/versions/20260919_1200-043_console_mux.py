"""Per-host web console mux opt-in (v1.6 Mux-1).

Revision ID: 043_console_mux
Revises: 042_compose_project_meta
Create Date: 2026-09-19
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "043_console_mux"
down_revision: Union[str, None] = "042_compose_project_meta"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)
    if "server" not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns("server")}
    if "console_mux_enabled" not in cols:
        op.add_column(
            "server",
            sa.Column(
                "console_mux_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )


def downgrade() -> None:
    try:
        op.drop_column("server", "console_mux_enabled")
    except Exception:
        pass
