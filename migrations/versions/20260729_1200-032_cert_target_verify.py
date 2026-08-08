"""CertificateTarget last_verify_* + verify_url for post-deploy validation.

Revision ID: 032_cert_target_verify
Revises: 031_user_session_version
Create Date: 2026-07-29
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "032_cert_target_verify"
down_revision: Union[str, None] = "031_user_session_version"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())
    if "certificatetarget" not in tables:
        return
    cols = {c["name"] for c in inspect(conn).get_columns("certificatetarget")}
    adds = [
        ("verify_url", "VARCHAR(500)"),
        ("last_verify_status", "VARCHAR"),
        ("last_verify_message", "TEXT"),
        ("last_verify_at", "TIMESTAMP"),
    ]
    for col, ddl in adds:
        if col not in cols:
            conn.execute(text(f"ALTER TABLE certificatetarget ADD COLUMN {col} {ddl}"))


def downgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())
    if "certificatetarget" not in tables:
        return
    cols = {c["name"] for c in inspect(conn).get_columns("certificatetarget")}
    for col in (
        "last_verify_at",
        "last_verify_message",
        "last_verify_status",
        "verify_url",
    ):
        if col in cols:
            conn.execute(text(f"ALTER TABLE certificatetarget DROP COLUMN {col}"))