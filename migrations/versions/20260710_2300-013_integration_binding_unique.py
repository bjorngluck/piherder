"""Allow multiple service bindings per server (unique includes external_id).

Revision ID: 013_integ_bind_unique
Revises: 012_integrations
Create Date: 2026-07-10
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "013_integ_bind_unique"
down_revision: Union[str, None] = "012_integrations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())
    if "integrationbinding" not in tables:
        conn.commit()
        return
    # Drop old unique (integration_id, server_id, role) if present
    conn.execute(
        text(
            "ALTER TABLE integrationbinding "
            "DROP CONSTRAINT IF EXISTS uq_integration_server_role"
        )
    )
    # Also drop if named differently (Postgres unique index)
    conn.execute(
        text("DROP INDEX IF EXISTS uq_integration_server_role")
    )
    conn.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_integ_bind_server_role_ext "
            "ON integrationbinding (integration_id, server_id, role, external_id)"
        )
    )
    conn.commit()


def downgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())
    if "integrationbinding" not in tables:
        conn.commit()
        return
    conn.execute(text("DROP INDEX IF EXISTS uq_integ_bind_server_role_ext"))
    conn.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_integration_server_role "
            "ON integrationbinding (integration_id, server_id, role)"
        )
    )
    conn.commit()
