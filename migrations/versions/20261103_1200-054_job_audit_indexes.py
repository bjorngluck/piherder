"""Indexes for job and audit log list filters.

Revision ID: 054_job_audit_indexes
Revises: 053_alert_mute
Create Date: 2026-11-03
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect

revision: str = "054_job_audit_indexes"
down_revision: Union[str, None] = "053_alert_mute"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None

_JOB_INDEX = "ix_job_server_status_created"
_AUDIT_INDEX = "ix_auditlog_server_action_started"


def _index_names(insp, table: str) -> set[str]:
    return {ix.get("name") for ix in insp.get_indexes(table)}


def upgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)
    tables = set(insp.get_table_names())
    if "job" in tables and _JOB_INDEX not in _index_names(insp, "job"):
        op.create_index(
            _JOB_INDEX,
            "job",
            ["server_id", "status", "created_at"],
        )
    if "auditlog" in tables and _AUDIT_INDEX not in _index_names(insp, "auditlog"):
        op.create_index(
            _AUDIT_INDEX,
            "auditlog",
            ["server_id", "action", "started_at"],
        )


def downgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)
    tables = set(insp.get_table_names())
    if "job" in tables and _JOB_INDEX in _index_names(insp, "job"):
        op.drop_index(_JOB_INDEX, table_name="job")
    if "auditlog" in tables and _AUDIT_INDEX in _index_names(insp, "auditlog"):
        op.drop_index(_AUDIT_INDEX, table_name="auditlog")
