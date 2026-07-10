"""IntegrationBinding.logo_path for service icons.

Revision ID: 015_service_logo
Revises: 014_binding_docker_scope
Create Date: 2026-07-10
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "015_service_logo"
down_revision: Union[str, None] = "014_binding_docker_scope"
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
    if "logo_path" not in cols:
        conn.execute(text("ALTER TABLE integrationbinding ADD COLUMN logo_path VARCHAR"))
    conn.commit()


def downgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())
    if "integrationbinding" not in tables:
        conn.commit()
        return
    cols = _cols(conn, "integrationbinding")
    if "logo_path" in cols:
        conn.execute(text("ALTER TABLE integrationbinding DROP COLUMN logo_path"))
    conn.commit()
