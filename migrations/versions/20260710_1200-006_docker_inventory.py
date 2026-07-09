"""Docker inventory snapshot columns on server.

Revision ID: 006_docker_inventory
Revises: 005_push_vapid_config
Create Date: 2026-07-10
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "006_docker_inventory"
down_revision: Union[str, None] = "005_push_vapid_config"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(conn, table: str, column: str) -> bool:
    cols = [c["name"] for c in inspect(conn).get_columns(table)]
    return column in cols


def upgrade() -> None:
    conn = op.get_bind()
    if not _has_column(conn, "server", "docker_inventory_json"):
        conn.execute(text("ALTER TABLE server ADD COLUMN docker_inventory_json TEXT"))
    if not _has_column(conn, "server", "docker_inventory_at"):
        conn.execute(
            text("ALTER TABLE server ADD COLUMN docker_inventory_at TIMESTAMP WITHOUT TIME ZONE")
        )
    if not _has_column(conn, "server", "docker_inventory_status"):
        conn.execute(
            text(
                "ALTER TABLE server ADD COLUMN docker_inventory_status VARCHAR "
                "NOT NULL DEFAULT 'never'"
            )
        )
    if not _has_column(conn, "server", "docker_inventory_error"):
        conn.execute(text("ALTER TABLE server ADD COLUMN docker_inventory_error TEXT"))
    conn.commit()


def downgrade() -> None:
    conn = op.get_bind()
    for col in (
        "docker_inventory_error",
        "docker_inventory_status",
        "docker_inventory_at",
        "docker_inventory_json",
    ):
        if _has_column(conn, "server", col):
            conn.execute(text(f"ALTER TABLE server DROP COLUMN {col}"))
    conn.commit()
