"""Alert mute rules. One host or one alert type. Delivery only.

Revision ID: 053_alert_mute
Revises: 052_schedule_catchup
Create Date: 2026-10-27
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "053_alert_mute"
down_revision: Union[str, None] = "052_schedule_catchup"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("alertmuterule"):
        return
    op.create_table(
        "alertmuterule",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("server_id", sa.Integer(), sa.ForeignKey("server.id"), nullable=True),
        sa.Column("alert_type", sa.String(), nullable=True),
        sa.Column("starts_at", sa.DateTime(), nullable=True),
        sa.Column("ends_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_alertmuterule_kind", "alertmuterule", ["kind"])
    op.create_index("ix_alertmuterule_server_id", "alertmuterule", ["server_id"])
    op.create_index("ix_alertmuterule_alert_type", "alertmuterule", ["alert_type"])


def downgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("alertmuterule"):
        return
    op.drop_index("ix_alertmuterule_alert_type", table_name="alertmuterule")
    op.drop_index("ix_alertmuterule_server_id", table_name="alertmuterule")
    op.drop_index("ix_alertmuterule_kind", table_name="alertmuterule")
    op.drop_table("alertmuterule")
