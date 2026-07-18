"""RuntimeEdge table for topology accept/dismiss/manual edges (P2–P3).

Revision ID: 021_runtime_edge
Revises: 020_dns_fabric
Create Date: 2026-07-18
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "021_runtime_edge"
down_revision: Union[str, None] = "020_dns_fabric"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())
    if "runtimeedge" in tables:
        return
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS runtimeedge (
                id SERIAL PRIMARY KEY,
                from_server_id INTEGER NOT NULL REFERENCES server(id),
                from_project VARCHAR(200) NOT NULL,
                from_container VARCHAR(200),
                to_server_id INTEGER NOT NULL REFERENCES server(id),
                to_project VARCHAR(200) NOT NULL,
                to_container VARCHAR(200),
                kind VARCHAR(32) NOT NULL DEFAULT 'depends_on',
                source VARCHAR(32) NOT NULL DEFAULT 'manual',
                confidence INTEGER NOT NULL DEFAULT 100,
                note VARCHAR(500),
                dismissed_at TIMESTAMP WITHOUT TIME ZONE,
                created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
                created_by_user_id INTEGER REFERENCES "user"(id)
            )
            """
        )
    )
    for idx in (
        "CREATE INDEX IF NOT EXISTS ix_runtimeedge_from_server_id ON runtimeedge (from_server_id)",
        "CREATE INDEX IF NOT EXISTS ix_runtimeedge_to_server_id ON runtimeedge (to_server_id)",
        "CREATE INDEX IF NOT EXISTS ix_runtimeedge_from_project ON runtimeedge (from_project)",
        "CREATE INDEX IF NOT EXISTS ix_runtimeedge_to_project ON runtimeedge (to_project)",
        "CREATE INDEX IF NOT EXISTS ix_runtimeedge_kind ON runtimeedge (kind)",
        "CREATE INDEX IF NOT EXISTS ix_runtimeedge_source ON runtimeedge (source)",
        "CREATE INDEX IF NOT EXISTS ix_runtimeedge_dismissed_at ON runtimeedge (dismissed_at)",
        "CREATE INDEX IF NOT EXISTS ix_runtimeedge_created_by_user_id ON runtimeedge (created_by_user_id)",
    ):
        try:
            conn.execute(text(idx))
        except Exception:
            pass


def downgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())
    if "runtimeedge" in tables:
        conn.execute(text("DROP TABLE IF EXISTS runtimeedge"))
