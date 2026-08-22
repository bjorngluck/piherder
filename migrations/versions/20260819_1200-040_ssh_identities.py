"""SSH identities per host (fleet + optional privileged).

Revision ID: 040_ssh_identities
Revises: 039_ssh_hostkey_pin
Create Date: 2026-08-19
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "040_ssh_identities"
down_revision: Union[str, None] = "039_ssh_hostkey_pin"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)
    tables = set(insp.get_table_names())
    if "serversshidentity" not in tables:
        op.create_table(
            "serversshidentity",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("server_id", sa.Integer(), nullable=False),
            sa.Column("role", sa.String(length=16), nullable=False),
            sa.Column("label", sa.String(length=32), nullable=False),
            sa.Column("username", sa.String(length=64), nullable=False),
            sa.Column("private_key_encrypted", sa.Text(), nullable=True),
            sa.Column("public_key", sa.Text(), nullable=True),
            sa.Column("key_fingerprint", sa.String(length=128), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index(
            "ix_serversshidentity_server_id", "serversshidentity", ["server_id"]
        )
        op.create_index("ix_serversshidentity_role", "serversshidentity", ["role"])
        op.create_index(
            "uq_serversshidentity_server_role",
            "serversshidentity",
            ["server_id", "role"],
            unique=True,
        )

    if "server" not in tables:
        return

    # Backfill one fleet row per existing server (idempotent).
    existing = conn.execute(sa.text("SELECT server_id FROM serversshidentity WHERE role = 'fleet'"))
    have = {int(r[0]) for r in existing}
    rows = conn.execute(
        sa.text(
            "SELECT id, ssh_username, ssh_private_key_encrypted, ssh_public_key "
            "FROM server"
        )
    )
    now = sa.text("CURRENT_TIMESTAMP")
    for sid, username, priv, pub in rows:
        sid = int(sid)
        if sid in have:
            continue
        user = (username or "pi").strip() or "pi"
        conn.execute(
            sa.text(
                "INSERT INTO serversshidentity "
                "(server_id, role, label, username, private_key_encrypted, public_key, "
                "key_fingerprint, enabled, created_at, updated_at) "
                "VALUES (:sid, 'fleet', 'Fleet', :user, :priv, :pub, NULL, 1, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"sid": sid, "user": user, "priv": priv, "pub": pub},
        )


def downgrade() -> None:
    try:
        op.drop_index("uq_serversshidentity_server_role", table_name="serversshidentity")
    except Exception:
        pass
    try:
        op.drop_table("serversshidentity")
    except Exception:
        pass
