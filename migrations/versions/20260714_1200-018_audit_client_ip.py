"""Add AuditLog.client_ip for request source on every audit event.

Revision ID: 018_audit_client_ip
Revises: 017_pihole_npm_certs
Create Date: 2026-07-14
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "018_audit_client_ip"
down_revision: Union[str, None] = "017_pihole_npm_certs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())
    if "auditlog" not in tables:
        return
    cols = {c["name"] for c in inspect(conn).get_columns("auditlog")}
    if "client_ip" not in cols:
        # IF NOT EXISTS for idempotent re-runs when version stamp lagged
        conn.execute(
            text("ALTER TABLE auditlog ADD COLUMN IF NOT EXISTS client_ip VARCHAR")
        )
    # Optional index for filtering by IP in large fleets
    try:
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_auditlog_client_ip ON auditlog (client_ip)"
            )
        )
    except Exception:
        pass


def downgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())
    if "auditlog" not in tables:
        return
    cols = {c["name"] for c in inspect(conn).get_columns("auditlog")}
    if "client_ip" in cols:
        try:
            conn.execute(text("DROP INDEX IF EXISTS ix_auditlog_client_ip"))
        except Exception:
            pass
        conn.execute(text("ALTER TABLE auditlog DROP COLUMN client_ip"))
