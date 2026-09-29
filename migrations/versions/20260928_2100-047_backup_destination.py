"""Fleet backup destination (Google Drive copy of /backups).

Revision ID: 047_backup_destination
Revises: 046_job_worker_hostname
Create Date: 2026-09-28
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "047_backup_destination"
down_revision: Union[str, None] = "046_job_worker_hostname"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)
    if "backup_destination" in set(insp.get_table_names()):
        return
    op.create_table(
        "backup_destination",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("config_json", sa.Text(), nullable=True),
        sa.Column("selection_json", sa.Text(), nullable=True),
        sa.Column("credentials_encrypted", sa.Text(), nullable=True),
        sa.Column("after_host_backup", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("schedule", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_backup_destination_provider", "backup_destination", ["provider"])


def downgrade() -> None:
    try:
        op.drop_index("ix_backup_destination_provider", table_name="backup_destination")
    except Exception:
        pass
    try:
        op.drop_table("backup_destination")
    except Exception:
        pass
