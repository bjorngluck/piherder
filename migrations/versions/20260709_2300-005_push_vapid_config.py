"""PushVapidConfig table for auto-managed VAPID keys.

Revision ID: 005_push_vapid_config
Revises: 004_push_subscriptions
Create Date: 2026-07-09
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "005_push_vapid_config"
down_revision: Union[str, None] = "004_push_subscriptions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(conn, table: str) -> bool:
    return table in inspect(conn).get_table_names()


def upgrade() -> None:
    conn = op.get_bind()
    if not _has_table(conn, "pushvapidconfig"):
        conn.execute(
            text(
                """
                CREATE TABLE pushvapidconfig (
                    id SERIAL PRIMARY KEY,
                    public_key VARCHAR NOT NULL,
                    private_key_encrypted VARCHAR NOT NULL,
                    contact VARCHAR NOT NULL DEFAULT 'mailto:piherder@localhost',
                    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'utc'),
                    source VARCHAR NOT NULL DEFAULT 'generated'
                )
                """
            )
        )


def downgrade() -> None:
    conn = op.get_bind()
    if _has_table(conn, "pushvapidconfig"):
        op.drop_table("pushvapidconfig")
