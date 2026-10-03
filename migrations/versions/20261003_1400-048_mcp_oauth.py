"""MCP OAuth clients, authorization codes, and refresh tokens.

Revision ID: 048_mcp_oauth
Revises: 047_backup_destination
Create Date: 2026-10-03
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "048_mcp_oauth"
down_revision: Union[str, None] = "047_backup_destination"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)
    tables = set(insp.get_table_names())
    if "mcp_oauth_client" not in tables:
        op.create_table(
            "mcp_oauth_client",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("client_id", sa.String(), nullable=False),
            sa.Column("client_name", sa.String(), nullable=False, server_default=""),
            sa.Column("redirect_uris_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("revoked_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_mcp_oauth_client_client_id", "mcp_oauth_client", ["client_id"], unique=True)
    if "mcp_oauth_code" not in tables:
        op.create_table(
            "mcp_oauth_code",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("code_hash", sa.String(), nullable=False),
            sa.Column("client_id", sa.String(), nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
            sa.Column("redirect_uri", sa.Text(), nullable=False),
            sa.Column("code_challenge", sa.String(), nullable=False),
            sa.Column("scopes", sa.String(), nullable=False, server_default="read"),
            sa.Column("resource", sa.Text(), nullable=False, server_default=""),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("used_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_mcp_oauth_code_code_hash", "mcp_oauth_code", ["code_hash"], unique=True)
        op.create_index("ix_mcp_oauth_code_client_id", "mcp_oauth_code", ["client_id"])
    if "mcp_oauth_refresh" not in tables:
        op.create_table(
            "mcp_oauth_refresh",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("token_hash", sa.String(), nullable=False),
            sa.Column("api_token_id", sa.Integer(), sa.ForeignKey("apitoken.id"), nullable=False),
            sa.Column("client_id", sa.String(), nullable=False),
            sa.Column("scopes", sa.String(), nullable=False, server_default="read"),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("revoked_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index(
            "ix_mcp_oauth_refresh_token_hash", "mcp_oauth_refresh", ["token_hash"], unique=True
        )
        op.create_index("ix_mcp_oauth_refresh_api_token_id", "mcp_oauth_refresh", ["api_token_id"])
        op.create_index("ix_mcp_oauth_refresh_client_id", "mcp_oauth_refresh", ["client_id"])


def downgrade() -> None:
    op.drop_table("mcp_oauth_refresh")
    op.drop_table("mcp_oauth_code")
    op.drop_table("mcp_oauth_client")
