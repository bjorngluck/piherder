"""PushSubscription + PushPreference tables for Web Push.

Revision ID: 004_push_subscriptions
Revises: 003_must_change_password
Create Date: 2026-07-09
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "004_push_subscriptions"
down_revision: Union[str, None] = "003_must_change_password"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(conn, table: str) -> bool:
    return table in inspect(conn).get_table_names()


def upgrade() -> None:
    conn = op.get_bind()
    if not _has_table(conn, "pushsubscription"):
        conn.execute(
            text(
                """
                CREATE TABLE pushsubscription (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES "user"(id),
                    endpoint VARCHAR NOT NULL,
                    p256dh VARCHAR NOT NULL,
                    auth VARCHAR NOT NULL,
                    user_agent VARCHAR,
                    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'utc'),
                    last_success_at TIMESTAMP WITHOUT TIME ZONE,
                    disabled_at TIMESTAMP WITHOUT TIME ZONE
                )
                """
            )
        )
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_pushsubscription_endpoint ON pushsubscription (endpoint)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_pushsubscription_user_id ON pushsubscription (user_id)"))

    if not _has_table(conn, "pushpreference"):
        conn.execute(
            text(
                """
                CREATE TABLE pushpreference (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES "user"(id),
                    push_enabled BOOLEAN NOT NULL DEFAULT FALSE,
                    backup_failed BOOLEAN NOT NULL DEFAULT TRUE,
                    os_updates BOOLEAN NOT NULL DEFAULT TRUE,
                    reboot_pending BOOLEAN NOT NULL DEFAULT TRUE,
                    container_updates BOOLEAN NOT NULL DEFAULT TRUE,
                    herder_backup_failed BOOLEAN NOT NULL DEFAULT TRUE,
                    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'utc')
                )
                """
            )
        )
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_pushpreference_user_id ON pushpreference (user_id)"))


def downgrade() -> None:
    conn = op.get_bind()
    if _has_table(conn, "pushsubscription"):
        op.drop_table("pushsubscription")
    if _has_table(conn, "pushpreference"):
        op.drop_table("pushpreference")
