"""Password reset tokens for G1-lite email recovery.

Revision ID: 035_password_reset_token
Revises: 034_favourite_feature_len
Create Date: 2026-07-29
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "035_password_reset_token"
down_revision: Union[str, None] = "034_favourite_feature_len"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())
    if "passwordresettoken" in tables:
        return
    op.create_table(
        "passwordresettoken",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("request_ip", sa.String(), nullable=True),
    )
    op.create_index(
        "ix_passwordresettoken_user_id", "passwordresettoken", ["user_id"]
    )
    op.create_index(
        "ix_passwordresettoken_token_hash", "passwordresettoken", ["token_hash"]
    )


def downgrade() -> None:
    try:
        op.drop_table("passwordresettoken")
    except Exception:
        pass
