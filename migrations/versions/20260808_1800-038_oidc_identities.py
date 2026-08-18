"""OIDC identities + password_login_enabled (v1.2 Stream S).

Revision ID: 038_oidc_identities
Revises: 037_webauthn_credentials
Create Date: 2026-08-08
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "038_oidc_identities"
down_revision: Union[str, None] = "037_webauthn_credentials"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)
    tables = set(insp.get_table_names())

    if "user" in tables:
        cols = {c["name"] for c in insp.get_columns("user")}
        if "password_login_enabled" not in cols:
            op.add_column(
                "user",
                sa.Column(
                    "password_login_enabled",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.true(),
                ),
            )

    if "oidcidentity" not in tables:
        op.create_table(
            "oidcidentity",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("issuer", sa.String(length=512), nullable=False),
            sa.Column("subject", sa.String(length=512), nullable=False),
            sa.Column("email_at_link", sa.String(length=320), nullable=True),
            sa.Column("display_name_at_link", sa.String(length=256), nullable=True),
            sa.Column("claims_json", sa.Text(), nullable=True),
            sa.Column("linked_at", sa.DateTime(), nullable=False),
            sa.Column("last_login_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_oidcidentity_user_id", "oidcidentity", ["user_id"])
        op.create_index("ix_oidcidentity_issuer", "oidcidentity", ["issuer"])
        op.create_index("ix_oidcidentity_subject", "oidcidentity", ["subject"])
        op.create_index(
            "uq_oidcidentity_issuer_subject",
            "oidcidentity",
            ["issuer", "subject"],
            unique=True,
        )


def downgrade() -> None:
    try:
        op.drop_table("oidcidentity")
    except Exception:
        pass
    try:
        op.drop_column("user", "password_login_enabled")
    except Exception:
        pass
