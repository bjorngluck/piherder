"""Opt-in catch-up for a missed backup or image check. Default off.

Revision ID: 052_schedule_catchup
Revises: 051_docker_prune_schedule
Create Date: 2026-10-27
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "052_schedule_catchup"
down_revision: Union[str, None] = "051_docker_prune_schedule"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


def _add_bool(table: str, name: str) -> None:
    conn = op.get_bind()
    insp = inspect(conn)
    if table not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns(table)}
    if name in cols:
        return
    op.add_column(
        table,
        sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def upgrade() -> None:
    _add_bool("server", "backup_catchup_enabled")
    _add_bool("server", "container_check_catchup_enabled")


def downgrade() -> None:
    for name in ("container_check_catchup_enabled", "backup_catchup_enabled"):
        try:
            op.drop_column("server", name)
        except Exception:
            pass
