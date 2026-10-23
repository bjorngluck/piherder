"""Per-host Docker prune cron. Empty means the schedule is off.

Revision ID: 051_docker_prune_schedule
Revises: 050_backup_direct_targets
Create Date: 2026-10-23
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "051_docker_prune_schedule"
down_revision: Union[str, None] = "050_backup_direct_targets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)
    if "server" not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns("server")}
    if "docker_prune_schedule" not in cols:
        op.add_column(
            "server",
            sa.Column("docker_prune_schedule", sa.String(), nullable=True),
        )


def downgrade() -> None:
    try:
        op.drop_column("server", "docker_prune_schedule")
    except Exception:
        pass
