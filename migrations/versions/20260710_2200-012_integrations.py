"""Integration + IntegrationBinding tables; PushPreference.integration_down.

Revision ID: 012_integrations
Revises: 011_host_deps
Create Date: 2026-07-10
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "012_integrations"
down_revision: Union[str, None] = "011_host_deps"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(conn, table: str) -> set[str]:
    try:
        return {c["name"] for c in inspect(conn).get_columns(table)}
    except Exception:
        return set()


def upgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())

    if "integration" not in tables:
        conn.execute(
            text(
                """
                CREATE TABLE integration (
                    id SERIAL PRIMARY KEY,
                    type VARCHAR NOT NULL,
                    name VARCHAR NOT NULL,
                    base_url VARCHAR NOT NULL,
                    enabled BOOLEAN NOT NULL DEFAULT TRUE,
                    config_json TEXT,
                    credentials_encrypted TEXT,
                    last_status_json TEXT,
                    last_polled_at TIMESTAMP WITHOUT TIME ZONE,
                    last_error TEXT,
                    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
                    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc')
                )
                """
            )
        )
        conn.execute(text("CREATE INDEX ix_integration_type ON integration (type)"))

    tables = set(inspect(conn).get_table_names())
    if "integrationbinding" not in tables and "integration_binding" not in tables:
        # SQLModel default table name is classname lowercased → integrationbinding
        conn.execute(
            text(
                """
                CREATE TABLE integrationbinding (
                    id SERIAL PRIMARY KEY,
                    integration_id INTEGER NOT NULL REFERENCES integration(id) ON DELETE CASCADE,
                    server_id INTEGER NOT NULL REFERENCES server(id) ON DELETE CASCADE,
                    role VARCHAR NOT NULL DEFAULT 'ssh_reachability',
                    external_id VARCHAR NOT NULL,
                    external_label VARCHAR,
                    external_meta_json TEXT,
                    last_state VARCHAR,
                    last_message TEXT,
                    last_checked_at TIMESTAMP WITHOUT TIME ZONE,
                    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
                    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
                    CONSTRAINT uq_integration_server_role UNIQUE (integration_id, server_id, role)
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_integrationbinding_integration_id "
                "ON integrationbinding (integration_id)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_integrationbinding_server_id "
                "ON integrationbinding (server_id)"
            )
        )
        conn.execute(
            text("CREATE INDEX ix_integrationbinding_role ON integrationbinding (role)")
        )

    if "pushpreference" in tables:
        cols = _cols(conn, "pushpreference")
        if "integration_down" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE pushpreference "
                    "ADD COLUMN integration_down BOOLEAN NOT NULL DEFAULT TRUE"
                )
            )

    conn.commit()


def downgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())
    if "integrationbinding" in tables:
        conn.execute(text("DROP TABLE IF EXISTS integrationbinding"))
    if "integration" in tables:
        conn.execute(text("DROP TABLE IF EXISTS integration"))
    if "pushpreference" in tables:
        cols = _cols(conn, "pushpreference")
        if "integration_down" in cols:
            conn.execute(text("ALTER TABLE pushpreference DROP COLUMN integration_down"))
    conn.commit()
