"""ManagedCertificate.edge_apply_enabled — self-managed Caddy map opt-in.

Revision ID: 023_cert_edge_apply_enabled
Revises: 022_cert_edge_write_mode
Create Date: 2026-07-18
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "023_cert_edge_apply_enabled"
down_revision: Union[str, None] = "022_cert_edge_write_mode"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())
    if "managedcertificate" not in tables:
        return
    cols = {c["name"] for c in inspect(conn).get_columns("managedcertificate")}
    if "edge_apply_enabled" not in cols:
        conn.execute(
            text(
                "ALTER TABLE managedcertificate "
                "ADD COLUMN edge_apply_enabled BOOLEAN NOT NULL DEFAULT false"
            )
        )
        # Backfill: prior successful edge apply opts in for renew re-apply
        conn.execute(
            text(
                """
                UPDATE managedcertificate
                SET edge_apply_enabled = true
                WHERE last_edge_deploy_status = 'success'
                   OR (last_edge_deploy_fingerprint IS NOT NULL
                       AND last_edge_deploy_fingerprint <> '')
                """
            )
        )


def downgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())
    if "managedcertificate" not in tables:
        return
    cols = {c["name"] for c in inspect(conn).get_columns("managedcertificate")}
    if "edge_apply_enabled" in cols:
        conn.execute(text("ALTER TABLE managedcertificate DROP COLUMN edge_apply_enabled"))
