"""WebAuthn / passkey credentials (v1.2 Stream I).

Revision ID: 037_webauthn_credentials
Revises: 036_port_annotation
Create Date: 2026-08-08
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "037_webauthn_credentials"
down_revision: Union[str, None] = "036_port_annotation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())
    if "webauthncredential" in tables:
        return
    op.create_table(
        "webauthncredential",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("credential_id", sa.String(length=1024), nullable=False),
        sa.Column("public_key", sa.String(length=4096), nullable=False),
        sa.Column("sign_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("transports", sa.String(length=256), nullable=True),
        sa.Column("nickname", sa.String(length=128), nullable=True),
        sa.Column("aaguid", sa.String(length=64), nullable=True),
        sa.Column("backup_eligible", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("backup_state", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_webauthncredential_user_id", "webauthncredential", ["user_id"]
    )
    op.create_index(
        "ix_webauthncredential_credential_id",
        "webauthncredential",
        ["credential_id"],
        unique=True,
    )


def downgrade() -> None:
    try:
        op.drop_table("webauthncredential")
    except Exception:
        pass
