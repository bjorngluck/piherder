"""UserFavourite pins for host feature shortcuts (J).

Revision ID: 033_user_favourites
Revises: 032_cert_target_verify
Create Date: 2026-07-29
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "033_user_favourites"
down_revision: Union[str, None] = "032_cert_target_verify"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())
    if "userfavourite" in tables:
        return
    conn.execute(
        text(
            """
            CREATE TABLE userfavourite (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES "user"(id),
                kind VARCHAR(32) NOT NULL DEFAULT 'server_feature',
                server_id INTEGER REFERENCES server(id),
                feature VARCHAR(32) NOT NULL,
                label VARCHAR(128),
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP WITHOUT TIME ZONE
            )
            """
        )
    )
    conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_userfavourite_user_id ON userfavourite (user_id)")
    )
    conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_userfavourite_server_id ON userfavourite (server_id)")
    )
    conn.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_userfavourite_pin "
            "ON userfavourite (user_id, kind, server_id, feature)"
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())
    if "userfavourite" in tables:
        conn.execute(text("DROP TABLE IF EXISTS userfavourite"))
