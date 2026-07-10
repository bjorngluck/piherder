"""API tokens table for /api/v1 Bearer auth.

Revision ID: 007_api_tokens
Revises: 006_docker_inventory
Create Date: 2026-07-10
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "007_api_tokens"
down_revision: Union[str, None] = "006_docker_inventory"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(conn, table: str) -> bool:
    return table in inspect(conn).get_table_names()


def upgrade() -> None:
    conn = op.get_bind()
    if not _has_table(conn, "apitoken"):
        conn.execute(
            text(
                """
                CREATE TABLE apitoken (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR NOT NULL,
                    token_prefix VARCHAR NOT NULL,
                    token_hash VARCHAR NOT NULL,
                    scopes VARCHAR NOT NULL DEFAULT 'read,jobs',
                    created_by_user_id INTEGER REFERENCES "user"(id),
                    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
                    last_used_at TIMESTAMP WITHOUT TIME ZONE,
                    revoked_at TIMESTAMP WITHOUT TIME ZONE,
                    expires_at TIMESTAMP WITHOUT TIME ZONE
                )
                """
            )
        )
        conn.execute(text("CREATE UNIQUE INDEX ix_apitoken_token_hash ON apitoken (token_hash)"))
        conn.execute(text("CREATE INDEX ix_apitoken_token_prefix ON apitoken (token_prefix)"))
    conn.commit()


def downgrade() -> None:
    conn = op.get_bind()
    if _has_table(conn, "apitoken"):
        conn.execute(text("DROP TABLE apitoken"))
    conn.commit()
