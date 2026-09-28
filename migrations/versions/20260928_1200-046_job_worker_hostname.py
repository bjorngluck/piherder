"""Job row records the Celery worker (or web process) that claimed it.

Revision ID: 046_job_worker_hostname
Revises: 045_host_resources
Create Date: 2026-09-28
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "046_job_worker_hostname"
down_revision: Union[str, None] = "045_host_resources"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)
    if "job" not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns("job")}
    if "worker_hostname" not in cols:
        op.add_column(
            "job",
            sa.Column("worker_hostname", sa.String(length=200), nullable=True),
        )


def downgrade() -> None:
    try:
        op.drop_column("job", "worker_hostname")
    except Exception:
        pass
