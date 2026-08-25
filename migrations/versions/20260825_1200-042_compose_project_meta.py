"""Per-host compose project host lock (v1.4 M1).

Revision ID: 042_compose_project_meta
Revises: 041_console_transcripts
Create Date: 2026-08-25
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "042_compose_project_meta"
down_revision: Union[str, None] = "041_console_transcripts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)
    tables = set(insp.get_table_names())
    if "composeprojectmeta" in tables:
        return
    op.create_table(
        "composeprojectmeta",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("server_id", sa.Integer(), sa.ForeignKey("server.id"), nullable=False),
        sa.Column("compose_project", sa.String(length=128), nullable=False),
        sa.Column("host_locked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("lock_reason", sa.String(length=16), nullable=True),
        sa.Column("lock_note", sa.String(length=255), nullable=True),
        sa.Column("locked_at", sa.DateTime(), nullable=True),
        sa.Column("locked_by_user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "server_id",
            "compose_project",
            name="uq_composeprojectmeta_server_project",
        ),
    )
    op.create_index(
        "ix_composeprojectmeta_server_id", "composeprojectmeta", ["server_id"]
    )
    op.create_index(
        "ix_composeprojectmeta_compose_project",
        "composeprojectmeta",
        ["compose_project"],
    )


def downgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)
    if "composeprojectmeta" not in set(insp.get_table_names()):
        return
    op.drop_index(
        "ix_composeprojectmeta_compose_project", table_name="composeprojectmeta"
    )
    op.drop_index("ix_composeprojectmeta_server_id", table_name="composeprojectmeta")
    op.drop_table("composeprojectmeta")
