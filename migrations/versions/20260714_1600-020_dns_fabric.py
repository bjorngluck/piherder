"""Server DNS identity + ServiceDnsRecord for end-to-end DNS fabric.

Revision ID: 020_dns_fabric
Revises: 019_cert_target_label
Create Date: 2026-07-14
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "020_dns_fabric"
down_revision: Union[str, None] = "019_cert_target_label"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())

    if "server" in tables:
        cols = {c["name"] for c in inspect(conn).get_columns("server")}
        if "dns_name" not in cols:
            conn.execute(text("ALTER TABLE server ADD COLUMN IF NOT EXISTS dns_name VARCHAR(253)"))
        if "dns_manage_a" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE server ADD COLUMN IF NOT EXISTS "
                    "dns_manage_a BOOLEAN NOT NULL DEFAULT false"
                )
            )
        if "dns_ip_override" not in cols:
            conn.execute(
                text("ALTER TABLE server ADD COLUMN IF NOT EXISTS dns_ip_override VARCHAR(64)")
            )
        try:
            conn.execute(
                text("CREATE INDEX IF NOT EXISTS ix_server_dns_name ON server (dns_name)")
            )
        except Exception:
            pass

    if "servicednsrecord" not in tables:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS servicednsrecord (
                    id SERIAL PRIMARY KEY,
                    fqdn VARCHAR(253) NOT NULL,
                    record_type VARCHAR NOT NULL DEFAULT 'cname',
                    target_server_id INTEGER NOT NULL REFERENCES server(id),
                    backend_server_id INTEGER NOT NULL REFERENCES server(id),
                    stack_deployment_id INTEGER REFERENCES stackdeployment(id),
                    docker_project VARCHAR(200),
                    label VARCHAR(200),
                    managed_on_pihole BOOLEAN NOT NULL DEFAULT true,
                    via_proxy BOOLEAN NOT NULL DEFAULT false,
                    npm_hint VARCHAR(300),
                    certificate_id INTEGER REFERENCES managedcertificate(id),
                    external_dns_status VARCHAR(32) NOT NULL DEFAULT 'checklist',
                    notes TEXT,
                    last_synced_at TIMESTAMP WITHOUT TIME ZONE,
                    last_sync_status VARCHAR,
                    last_sync_detail TEXT,
                    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
                )
                """
            )
        )
        for idx in (
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_servicednsrecord_fqdn ON servicednsrecord (fqdn)",
            "CREATE INDEX IF NOT EXISTS ix_servicednsrecord_target_server_id ON servicednsrecord (target_server_id)",
            "CREATE INDEX IF NOT EXISTS ix_servicednsrecord_backend_server_id ON servicednsrecord (backend_server_id)",
            "CREATE INDEX IF NOT EXISTS ix_servicednsrecord_stack_deployment_id ON servicednsrecord (stack_deployment_id)",
            "CREATE INDEX IF NOT EXISTS ix_servicednsrecord_docker_project ON servicednsrecord (docker_project)",
            "CREATE INDEX IF NOT EXISTS ix_servicednsrecord_certificate_id ON servicednsrecord (certificate_id)",
            "CREATE INDEX IF NOT EXISTS ix_servicednsrecord_record_type ON servicednsrecord (record_type)",
        ):
            try:
                conn.execute(text(idx))
            except Exception:
                pass


def downgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())
    if "servicednsrecord" in tables:
        conn.execute(text("DROP TABLE IF EXISTS servicednsrecord"))
