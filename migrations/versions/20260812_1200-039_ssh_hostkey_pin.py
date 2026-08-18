"""SSH host-key pin (TOFU) on server.

Revision ID: 039_ssh_hostkey_pin
Revises: 038_oidc_identities
Create Date: 2026-08-12
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "039_ssh_hostkey_pin"
down_revision: Union[str, None] = "038_oidc_identities"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)
    if "server" not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns("server")}
    if "ssh_hostkey_type" not in cols:
        op.add_column(
            "server",
            sa.Column("ssh_hostkey_type", sa.String(length=64), nullable=True),
        )
    if "ssh_hostkey_b64" not in cols:
        op.add_column(
            "server",
            sa.Column("ssh_hostkey_b64", sa.Text(), nullable=True),
        )
    if "ssh_hostkey_fp" not in cols:
        op.add_column(
            "server",
            sa.Column("ssh_hostkey_fp", sa.String(length=128), nullable=True),
        )


def downgrade() -> None:
    for col in ("ssh_hostkey_fp", "ssh_hostkey_b64", "ssh_hostkey_type"):
        try:
            op.drop_column("server", col)
        except Exception:
            pass
