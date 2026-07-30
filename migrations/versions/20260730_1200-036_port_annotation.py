"""Port annotations for sticky host port roles (map M4).

Revision ID: 036_port_annotation
Revises: 035_password_reset_token
Create Date: 2026-07-30
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "036_port_annotation"
down_revision: Union[str, None] = "035_password_reset_token"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())
    if "portannotation" in tables:
        return
    op.create_table(
        "portannotation",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("server_id", sa.Integer(), nullable=True),
        sa.Column("nmap_device_id", sa.Integer(), nullable=True),
        sa.Column("host_port", sa.Integer(), nullable=False),
        sa.Column("proto", sa.String(), nullable=False),
        sa.Column("role_key", sa.String(), nullable=True),
        sa.Column("label", sa.String(), nullable=True),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("owner_project", sa.String(), nullable=True),
        sa.Column("owner_container", sa.String(), nullable=True),
        sa.Column("hide", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
    )
    op.create_index("ix_portannotation_server_id", "portannotation", ["server_id"])
    op.create_index(
        "ix_portannotation_nmap_device_id", "portannotation", ["nmap_device_id"]
    )
    op.create_index("ix_portannotation_host_port", "portannotation", ["host_port"])
    op.create_index("ix_portannotation_proto", "portannotation", ["proto"])
    op.create_index("ix_portannotation_role_key", "portannotation", ["role_key"])
    op.create_index("ix_portannotation_hide", "portannotation", ["hide"])
    # Soft uniqueness: app layer enforces; SQLite allows multiple NULLs in unique
    op.create_index(
        "ix_portannotation_server_port_proto",
        "portannotation",
        ["server_id", "host_port", "proto"],
    )
    op.create_index(
        "ix_portannotation_device_port_proto",
        "portannotation",
        ["nmap_device_id", "host_port", "proto"],
    )


def downgrade() -> None:
    try:
        op.drop_table("portannotation")
    except Exception:
        pass
