"""Add CertificateTarget.label for service-map display name.

Revision ID: 019_cert_target_label
Revises: 018_audit_client_ip
Create Date: 2026-07-14
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "019_cert_target_label"
down_revision: Union[str, None] = "018_audit_client_ip"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())
    if "certificatetarget" not in tables:
        return
    cols = {c["name"] for c in inspect(conn).get_columns("certificatetarget")}
    if "label" not in cols:
        conn.execute(
            text(
                "ALTER TABLE certificatetarget "
                "ADD COLUMN IF NOT EXISTS label VARCHAR"
            )
        )


def downgrade() -> None:
    # Non-destructive: leave column in place for SQLite/Postgres simplicity
    pass
