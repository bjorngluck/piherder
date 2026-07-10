"""IntegrationBinding docker_project + docker_container scope for service monitors.

Revision ID: 014_binding_docker_scope
Revises: 013_integ_bind_unique
Create Date: 2026-07-10
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "014_binding_docker_scope"
down_revision: Union[str, None] = "013_integ_bind_unique"
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
    if "integrationbinding" not in tables:
        conn.commit()
        return
    cols = _cols(conn, "integrationbinding")
    if "docker_project" not in cols:
        conn.execute(
            text("ALTER TABLE integrationbinding ADD COLUMN docker_project VARCHAR")
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_integrationbinding_docker_project "
                "ON integrationbinding (docker_project)"
            )
        )
    if "docker_container" not in cols:
        conn.execute(
            text("ALTER TABLE integrationbinding ADD COLUMN docker_container VARCHAR")
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_integrationbinding_docker_container "
                "ON integrationbinding (docker_container)"
            )
        )
    # Unique includes docker scope (NULL treated as '' for uniqueness via coalesced index)
    conn.execute(text("DROP INDEX IF EXISTS uq_integ_bind_server_role_ext"))
    conn.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_integ_bind_scope "
            "ON integrationbinding ("
            "  integration_id, server_id, role, external_id, "
            "  COALESCE(docker_project, ''), COALESCE(docker_container, '')"
            ")"
        )
    )
    conn.commit()


def downgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())
    if "integrationbinding" not in tables:
        conn.commit()
        return
    conn.execute(text("DROP INDEX IF EXISTS uq_integ_bind_scope"))
    cols = _cols(conn, "integrationbinding")
    if "docker_container" in cols:
        conn.execute(text("ALTER TABLE integrationbinding DROP COLUMN docker_container"))
    if "docker_project" in cols:
        conn.execute(text("ALTER TABLE integrationbinding DROP COLUMN docker_project"))
    conn.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_integ_bind_server_role_ext "
            "ON integrationbinding (integration_id, server_id, role, external_id)"
        )
    )
    conn.commit()
