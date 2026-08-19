"""Console command-audit transcripts (Fernet body).

Revision ID: 041_console_transcripts
Revises: 040_ssh_identities
Create Date: 2026-08-19
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "041_console_transcripts"
down_revision: Union[str, None] = "040_ssh_identities"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)
    tables = set(insp.get_table_names())
    if "consoletranscript" in tables:
        return
    op.create_table(
        "consoletranscript",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_key", sa.String(length=64), nullable=False),
        sa.Column("audit_open_id", sa.Integer(), nullable=True),
        sa.Column("audit_close_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("server_id", sa.Integer(), nullable=True),
        sa.Column("identity_role", sa.String(length=16), nullable=True),
        sa.Column("identity_username", sa.String(length=64), nullable=True),
        sa.Column("mode", sa.String(length=32), nullable=False, server_default="commands"),
        sa.Column("command_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("byte_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("truncated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("body_encrypted", sa.Text(), nullable=True),
        sa.Column("purged_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_consoletranscript_session_key",
        "consoletranscript",
        ["session_key"],
        unique=True,
    )
    op.create_index("ix_consoletranscript_user_id", "consoletranscript", ["user_id"])
    op.create_index("ix_consoletranscript_server_id", "consoletranscript", ["server_id"])
    op.create_index(
        "ix_consoletranscript_created_at", "consoletranscript", ["created_at"]
    )


def downgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)
    if "consoletranscript" not in set(insp.get_table_names()):
        return
    op.drop_index("ix_consoletranscript_created_at", table_name="consoletranscript")
    op.drop_index("ix_consoletranscript_server_id", table_name="consoletranscript")
    op.drop_index("ix_consoletranscript_user_id", table_name="consoletranscript")
    op.drop_index("ix_consoletranscript_session_key", table_name="consoletranscript")
    op.drop_table("consoletranscript")
