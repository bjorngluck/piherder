"""User last_login_at + AuditLog API token actor fields.

Revision ID: 010_user_login_api_actor
Revises: 009_app_setting
Create Date: 2026-07-10
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "010_user_login_api_actor"
down_revision: Union[str, None] = "009_app_setting"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(conn, table: str) -> set[str]:
    try:
        return {c["name"] for c in inspect(conn).get_columns(table)}
    except Exception:
        return set()


def upgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())

    if "user" in tables:
        cols = _cols(conn, "user")
        if "last_login_at" not in cols:
            conn.execute(
                text(
                    'ALTER TABLE "user" ADD COLUMN last_login_at '
                    "TIMESTAMP WITHOUT TIME ZONE"
                )
            )

    if "auditlog" in tables:
        cols = _cols(conn, "auditlog")
        if "api_token_id" not in cols:
            # ON DELETE SET NULL so hard-deleting a token keeps audit history
            conn.execute(
                text(
                    "ALTER TABLE auditlog ADD COLUMN api_token_id INTEGER "
                    "REFERENCES apitoken(id) ON DELETE SET NULL"
                )
            )
        if "api_token_name" not in cols:
            conn.execute(text("ALTER TABLE auditlog ADD COLUMN api_token_name VARCHAR"))
        # Index for audit filter by token
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_auditlog_api_token_id "
                "ON auditlog (api_token_id)"
            )
        )

    conn.commit()


def downgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())
    if "auditlog" in tables:
        cols = _cols(conn, "auditlog")
        conn.execute(text("DROP INDEX IF EXISTS ix_auditlog_api_token_id"))
        if "api_token_name" in cols:
            conn.execute(text("ALTER TABLE auditlog DROP COLUMN api_token_name"))
        if "api_token_id" in cols:
            conn.execute(text("ALTER TABLE auditlog DROP COLUMN api_token_id"))
    if "user" in tables:
        cols = _cols(conn, "user")
        if "last_login_at" in cols:
            conn.execute(text('ALTER TABLE "user" DROP COLUMN last_login_at'))
    conn.commit()
