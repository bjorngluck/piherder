"""Managed certificates + certificate deploy targets (Pi-hole/NPM/certs).

Revision ID: 017_pihole_npm_certs
Revises: 016_service_templates
Create Date: 2026-07-13
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "017_pihole_npm_certs"
down_revision: Union[str, None] = "016_service_templates"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())

    if "managedcertificate" not in tables:
        conn.execute(
            text(
                """
                CREATE TABLE managedcertificate (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR NOT NULL,
                    source VARCHAR DEFAULT 'upload' NOT NULL,
                    source_integration_id INTEGER REFERENCES integration(id),
                    external_id VARCHAR,
                    domains_json TEXT,
                    not_before TIMESTAMP WITHOUT TIME ZONE,
                    not_after TIMESTAMP WITHOUT TIME ZONE,
                    fingerprint_sha256 VARCHAR,
                    fullchain_encrypted TEXT,
                    privkey_encrypted TEXT,
                    issuer VARCHAR,
                    serial VARCHAR,
                    last_pulled_at TIMESTAMP WITHOUT TIME ZONE,
                    last_renew_requested_at TIMESTAMP WITHOUT TIME ZONE,
                    last_renew_status VARCHAR,
                    last_error VARCHAR,
                    auto_renew BOOLEAN DEFAULT TRUE NOT NULL,
                    renew_days_before INTEGER DEFAULT 21 NOT NULL,
                    created_at TIMESTAMP WITHOUT TIME ZONE,
                    updated_at TIMESTAMP WITHOUT TIME ZONE
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_managedcertificate_source "
                "ON managedcertificate (source)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_managedcertificate_source_integration_id "
                "ON managedcertificate (source_integration_id)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_managedcertificate_external_id "
                "ON managedcertificate (external_id)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_managedcertificate_fingerprint_sha256 "
                "ON managedcertificate (fingerprint_sha256)"
            )
        )

    tables = set(inspect(conn).get_table_names())
    if "certificatetarget" not in tables:
        conn.execute(
            text(
                """
                CREATE TABLE certificatetarget (
                    id SERIAL PRIMARY KEY,
                    certificate_id INTEGER NOT NULL REFERENCES managedcertificate(id),
                    server_id INTEGER NOT NULL REFERENCES server(id),
                    remote_dir VARCHAR DEFAULT '~/certs' NOT NULL,
                    layout VARCHAR DEFAULT 'pair' NOT NULL,
                    fullchain_filename VARCHAR DEFAULT 'fullchain.pem' NOT NULL,
                    privkey_filename VARCHAR DEFAULT 'privkey.pem' NOT NULL,
                    combined_filename VARCHAR DEFAULT 'snakeoil.pem' NOT NULL,
                    pfx_filename VARCHAR DEFAULT 'Certificate.pfx' NOT NULL,
                    file_mode VARCHAR DEFAULT '600' NOT NULL,
                    file_owner VARCHAR,
                    file_group VARCHAR,
                    pfx_export_password_encrypted TEXT,
                    post_deploy_command TEXT,
                    enabled BOOLEAN DEFAULT TRUE NOT NULL,
                    last_deployed_at TIMESTAMP WITHOUT TIME ZONE,
                    last_deploy_status VARCHAR,
                    last_deploy_fingerprint VARCHAR,
                    last_deploy_message VARCHAR,
                    created_at TIMESTAMP WITHOUT TIME ZONE,
                    updated_at TIMESTAMP WITHOUT TIME ZONE
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_certificatetarget_certificate_id "
                "ON certificatetarget (certificate_id)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_certificatetarget_server_id "
                "ON certificatetarget (server_id)"
            )
        )


def downgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())
    if "certificatetarget" in tables:
        conn.execute(text("DROP TABLE IF EXISTS certificatetarget"))
    if "managedcertificate" in tables:
        conn.execute(text("DROP TABLE IF EXISTS managedcertificate"))
