"""Service templates catalog + stack desired-state deployments.

Revision ID: 016_service_templates
Revises: 015_service_logo
Create Date: 2026-07-12
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "016_service_templates"
down_revision: Union[str, None] = "015_service_logo"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())

    if "servicetemplate" not in tables:
        conn.execute(
            text(
                """
                CREATE TABLE servicetemplate (
                    id SERIAL PRIMARY KEY,
                    slug VARCHAR NOT NULL,
                    name VARCHAR NOT NULL,
                    description VARCHAR,
                    category VARCHAR DEFAULT 'other' NOT NULL,
                    version VARCHAR DEFAULT '1.0.0' NOT NULL,
                    source VARCHAR DEFAULT 'builtin' NOT NULL,
                    enabled BOOLEAN DEFAULT TRUE NOT NULL,
                    definition_json TEXT,
                    checksum VARCHAR,
                    created_at TIMESTAMP WITHOUT TIME ZONE,
                    updated_at TIMESTAMP WITHOUT TIME ZONE
                )
                """
            )
        )
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_servicetemplate_slug ON servicetemplate (slug)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_servicetemplate_category ON servicetemplate (category)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_servicetemplate_source ON servicetemplate (source)"))

    tables = set(inspect(conn).get_table_names())
    if "stackdeployment" not in tables:
        conn.execute(
            text(
                """
                CREATE TABLE stackdeployment (
                    id SERIAL PRIMARY KEY,
                    server_id INTEGER NOT NULL REFERENCES server(id),
                    project_name VARCHAR NOT NULL,
                    template_id INTEGER REFERENCES servicetemplate(id),
                    template_slug VARCHAR,
                    template_version VARCHAR,
                    config_version INTEGER DEFAULT 1 NOT NULL,
                    variables_json TEXT,
                    secrets_encrypted TEXT,
                    files_json TEXT,
                    drift_status VARCHAR DEFAULT 'unknown' NOT NULL,
                    last_deployed_at TIMESTAMP WITHOUT TIME ZONE,
                    last_validated_at TIMESTAMP WITHOUT TIME ZONE,
                    created_at TIMESTAMP WITHOUT TIME ZONE,
                    updated_at TIMESTAMP WITHOUT TIME ZONE
                )
                """
            )
        )
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_stackdeployment_server_id ON stackdeployment (server_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_stackdeployment_project_name ON stackdeployment (project_name)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_stackdeployment_template_id ON stackdeployment (template_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_stackdeployment_template_slug ON stackdeployment (template_slug)"))
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_stackdeployment_server_project "
                "ON stackdeployment (server_id, project_name)"
            )
        )

    conn.commit()


def downgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())
    if "stackdeployment" in tables:
        conn.execute(text("DROP TABLE IF EXISTS stackdeployment"))
    if "servicetemplate" in tables:
        conn.execute(text("DROP TABLE IF EXISTS servicetemplate"))
    conn.commit()
