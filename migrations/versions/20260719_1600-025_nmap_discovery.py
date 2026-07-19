"""LAN discovery (nmap) tables.

Revision ID: 025_nmap_discovery
Revises: 024_topology_annotations
Create Date: 2026-07-19
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "025_nmap_discovery"
down_revision: Union[str, None] = "024_topology_annotations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())

    if "nmapscanschedule" not in tables:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS nmapscanschedule (
                    id SERIAL PRIMARY KEY,
                    integration_id INTEGER NOT NULL REFERENCES integration(id),
                    name VARCHAR(120) NOT NULL,
                    intensity VARCHAR(32) NOT NULL DEFAULT 'discovery',
                    cron VARCHAR(64),
                    interval_hours INTEGER,
                    enabled BOOLEAN NOT NULL DEFAULT FALSE,
                    scope_json TEXT,
                    last_run_at TIMESTAMP WITHOUT TIME ZONE,
                    last_job_id INTEGER,
                    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
                )
                """
            )
        )
        for idx in (
            "CREATE INDEX IF NOT EXISTS ix_nmapscanschedule_integration_id "
            "ON nmapscanschedule (integration_id)",
            "CREATE INDEX IF NOT EXISTS ix_nmapscanschedule_intensity "
            "ON nmapscanschedule (intensity)",
        ):
            conn.execute(text(idx))

    if "nmapscanrun" not in tables:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS nmapscanrun (
                    id SERIAL PRIMARY KEY,
                    integration_id INTEGER NOT NULL REFERENCES integration(id),
                    job_id INTEGER REFERENCES job(id),
                    schedule_id INTEGER REFERENCES nmapscanschedule(id),
                    intensity VARCHAR(32) NOT NULL DEFAULT 'discovery',
                    targets_json TEXT,
                    status VARCHAR(32) NOT NULL DEFAULT 'pending',
                    hosts_up INTEGER NOT NULL DEFAULT 0,
                    hosts_total INTEGER NOT NULL DEFAULT 0,
                    ports_open INTEGER NOT NULL DEFAULT 0,
                    summary_json TEXT,
                    artifact_path VARCHAR(512),
                    error TEXT,
                    started_at TIMESTAMP WITHOUT TIME ZONE,
                    finished_at TIMESTAMP WITHOUT TIME ZONE,
                    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
                )
                """
            )
        )
        for idx in (
            "CREATE INDEX IF NOT EXISTS ix_nmapscanrun_integration_id ON nmapscanrun (integration_id)",
            "CREATE INDEX IF NOT EXISTS ix_nmapscanrun_job_id ON nmapscanrun (job_id)",
            "CREATE INDEX IF NOT EXISTS ix_nmapscanrun_schedule_id ON nmapscanrun (schedule_id)",
            "CREATE INDEX IF NOT EXISTS ix_nmapscanrun_intensity ON nmapscanrun (intensity)",
            "CREATE INDEX IF NOT EXISTS ix_nmapscanrun_status ON nmapscanrun (status)",
        ):
            conn.execute(text(idx))

    if "nmapdevice" not in tables:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS nmapdevice (
                    id SERIAL PRIMARY KEY,
                    integration_id INTEGER NOT NULL REFERENCES integration(id),
                    identity_key VARCHAR(128) NOT NULL,
                    ip_address VARCHAR(64) NOT NULL,
                    hostname VARCHAR(255),
                    mac_address VARCHAR(32),
                    state VARCHAR(32) NOT NULL DEFAULT 'new',
                    linked_server_id INTEGER REFERENCES server(id),
                    os_summary VARCHAR(255),
                    ports_json TEXT,
                    last_seen_at TIMESTAMP WITHOUT TIME ZONE,
                    first_seen_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
                    last_run_id INTEGER REFERENCES nmapscanrun(id),
                    notes VARCHAR(500),
                    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
                )
                """
            )
        )
        for idx in (
            "CREATE INDEX IF NOT EXISTS ix_nmapdevice_integration_id ON nmapdevice (integration_id)",
            "CREATE INDEX IF NOT EXISTS ix_nmapdevice_identity_key ON nmapdevice (identity_key)",
            "CREATE INDEX IF NOT EXISTS ix_nmapdevice_ip_address ON nmapdevice (ip_address)",
            "CREATE INDEX IF NOT EXISTS ix_nmapdevice_mac_address ON nmapdevice (mac_address)",
            "CREATE INDEX IF NOT EXISTS ix_nmapdevice_state ON nmapdevice (state)",
            "CREATE INDEX IF NOT EXISTS ix_nmapdevice_linked_server_id ON nmapdevice (linked_server_id)",
            "CREATE INDEX IF NOT EXISTS ix_nmapdevice_last_run_id ON nmapdevice (last_run_id)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_nmapdevice_integration_identity "
            "ON nmapdevice (integration_id, identity_key)",
        ):
            conn.execute(text(idx))

    if "nmapscriptresult" not in tables:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS nmapscriptresult (
                    id SERIAL PRIMARY KEY,
                    device_id INTEGER NOT NULL REFERENCES nmapdevice(id),
                    run_id INTEGER REFERENCES nmapscanrun(id),
                    script_id VARCHAR(128) NOT NULL,
                    output TEXT,
                    cve_ids_json TEXT,
                    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
                )
                """
            )
        )
        for idx in (
            "CREATE INDEX IF NOT EXISTS ix_nmapscriptresult_device_id ON nmapscriptresult (device_id)",
            "CREATE INDEX IF NOT EXISTS ix_nmapscriptresult_run_id ON nmapscriptresult (run_id)",
            "CREATE INDEX IF NOT EXISTS ix_nmapscriptresult_script_id ON nmapscriptresult (script_id)",
        ):
            conn.execute(text(idx))


def downgrade() -> None:
    conn = op.get_bind()
    for table in (
        "nmapscriptresult",
        "nmapdevice",
        "nmapscanrun",
        "nmapscanschedule",
    ):
        conn.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))
