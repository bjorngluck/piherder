"""Cert edge self-deploy fields + fleet map write_mode (stage_sudo).

Revision ID: 022_cert_edge_write_mode
Revises: 021_runtime_edge
Create Date: 2026-07-18
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "022_cert_edge_write_mode"
down_revision: Union[str, None] = "021_runtime_edge"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())
    if "managedcertificate" in tables:
        cols = {c["name"] for c in inspect(conn).get_columns("managedcertificate")}
        for col, ddl in (
            ("last_edge_deploy_at", "TIMESTAMP WITHOUT TIME ZONE"),
            ("last_edge_deploy_status", "VARCHAR"),
            ("last_edge_deploy_fingerprint", "VARCHAR"),
            ("last_edge_deploy_message", "VARCHAR"),
        ):
            if col not in cols:
                conn.execute(
                    text(f"ALTER TABLE managedcertificate ADD COLUMN {col} {ddl}")
                )
    if "certificatetarget" in tables:
        cols = {c["name"] for c in inspect(conn).get_columns("certificatetarget")}
        if "write_mode" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE certificatetarget "
                    "ADD COLUMN write_mode VARCHAR(32) NOT NULL DEFAULT 'direct'"
                )
            )


def downgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())
    if "certificatetarget" in tables:
        cols = {c["name"] for c in inspect(conn).get_columns("certificatetarget")}
        if "write_mode" in cols:
            conn.execute(text("ALTER TABLE certificatetarget DROP COLUMN write_mode"))
    if "managedcertificate" in tables:
        cols = {c["name"] for c in inspect(conn).get_columns("managedcertificate")}
        for col in (
            "last_edge_deploy_message",
            "last_edge_deploy_fingerprint",
            "last_edge_deploy_status",
            "last_edge_deploy_at",
        ):
            if col in cols:
                conn.execute(text(f"ALTER TABLE managedcertificate DROP COLUMN {col}"))
