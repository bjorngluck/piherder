"""Topology categories, tags, visual stacks, container annotations.

Revision ID: 024_topology_annotations
Revises: 023_cert_edge_apply_enabled
Create Date: 2026-07-19
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "024_topology_annotations"
down_revision: Union[str, None] = "023_cert_edge_apply_enabled"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())

    if "topologycategory" not in tables:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS topologycategory (
                    id SERIAL PRIMARY KEY,
                    key VARCHAR(64) NOT NULL,
                    label VARCHAR(80) NOT NULL,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    enabled BOOLEAN NOT NULL DEFAULT TRUE,
                    is_system BOOLEAN NOT NULL DEFAULT FALSE,
                    color_token VARCHAR(32),
                    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_topologycategory_key "
                "ON topologycategory (key)"
            )
        )
        for idx in (
            "CREATE INDEX IF NOT EXISTS ix_topologycategory_sort_order ON topologycategory (sort_order)",
            "CREATE INDEX IF NOT EXISTS ix_topologycategory_enabled ON topologycategory (enabled)",
        ):
            try:
                conn.execute(text(idx))
            except Exception:
                pass

    if "topologytag" not in tables:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS topologytag (
                    id SERIAL PRIMARY KEY,
                    key VARCHAR(64) NOT NULL,
                    label VARCHAR(80) NOT NULL,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    enabled BOOLEAN NOT NULL DEFAULT TRUE,
                    is_system BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_topologytag_key ON topologytag (key)"
            )
        )
        for idx in (
            "CREATE INDEX IF NOT EXISTS ix_topologytag_sort_order ON topologytag (sort_order)",
            "CREATE INDEX IF NOT EXISTS ix_topologytag_enabled ON topologytag (enabled)",
        ):
            try:
                conn.execute(text(idx))
            except Exception:
                pass

    if "visualservicestack" not in tables:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS visualservicestack (
                    id SERIAL PRIMARY KEY,
                    server_id INTEGER NOT NULL REFERENCES server(id),
                    compose_project VARCHAR(200) NOT NULL,
                    name VARCHAR(120) NOT NULL,
                    slug VARCHAR(80) NOT NULL,
                    is_default BOOLEAN NOT NULL DEFAULT FALSE,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
                )
                """
            )
        )
        for idx in (
            "CREATE INDEX IF NOT EXISTS ix_visualservicestack_server_id ON visualservicestack (server_id)",
            "CREATE INDEX IF NOT EXISTS ix_visualservicestack_compose_project ON visualservicestack (compose_project)",
            "CREATE INDEX IF NOT EXISTS ix_visualservicestack_slug ON visualservicestack (slug)",
            "CREATE INDEX IF NOT EXISTS ix_visualservicestack_is_default ON visualservicestack (is_default)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_visualservicestack_srv_proj_slug "
            "ON visualservicestack (server_id, compose_project, slug)",
        ):
            try:
                conn.execute(text(idx))
            except Exception:
                pass

    if "containerannotation" not in tables:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS containerannotation (
                    id SERIAL PRIMARY KEY,
                    server_id INTEGER NOT NULL REFERENCES server(id),
                    compose_project VARCHAR(200) NOT NULL,
                    container_key VARCHAR(200) NOT NULL,
                    category_key VARCHAR(64),
                    visual_stack_id INTEGER REFERENCES visualservicestack(id),
                    sort_index INTEGER,
                    notes VARCHAR(500),
                    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
                    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
                )
                """
            )
        )
        for idx in (
            "CREATE INDEX IF NOT EXISTS ix_containerannotation_server_id ON containerannotation (server_id)",
            "CREATE INDEX IF NOT EXISTS ix_containerannotation_compose_project ON containerannotation (compose_project)",
            "CREATE INDEX IF NOT EXISTS ix_containerannotation_container_key ON containerannotation (container_key)",
            "CREATE INDEX IF NOT EXISTS ix_containerannotation_category_key ON containerannotation (category_key)",
            "CREATE INDEX IF NOT EXISTS ix_containerannotation_visual_stack_id ON containerannotation (visual_stack_id)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_containerannotation_srv_proj_key "
            "ON containerannotation (server_id, compose_project, container_key)",
        ):
            try:
                conn.execute(text(idx))
            except Exception:
                pass

    if "containerannotationtag" not in tables:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS containerannotationtag (
                    id SERIAL PRIMARY KEY,
                    annotation_id INTEGER NOT NULL REFERENCES containerannotation(id) ON DELETE CASCADE,
                    tag_key VARCHAR(64) NOT NULL
                )
                """
            )
        )
        for idx in (
            "CREATE INDEX IF NOT EXISTS ix_containerannotationtag_annotation_id "
            "ON containerannotationtag (annotation_id)",
            "CREATE INDEX IF NOT EXISTS ix_containerannotationtag_tag_key "
            "ON containerannotationtag (tag_key)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_containerannotationtag_ann_tag "
            "ON containerannotationtag (annotation_id, tag_key)",
        ):
            try:
                conn.execute(text(idx))
            except Exception:
                pass

    # Seed system categories (idempotent)
    cats = [
        ("edge", "Edge", 0),
        ("app", "App", 1),
        ("queue", "Queue", 2),
        ("cache", "Cache", 3),
        ("data", "Data", 4),
        ("tooling", "Tooling", 5),
    ]
    for key, label, so in cats:
        conn.execute(
            text(
                """
                INSERT INTO topologycategory (key, label, sort_order, enabled, is_system)
                SELECT :k, :l, :s, TRUE, TRUE
                WHERE NOT EXISTS (SELECT 1 FROM topologycategory WHERE key = :k)
                """
            ),
            {"k": key, "l": label, "s": so},
        )

    tags = [
        ("web", "Web", 0),
        ("db", "DB", 1),
        ("worker", "Worker", 2),
        ("proxy", "Proxy", 3),
        ("cache", "Cache", 4),
        ("queue", "Queue", 5),
        ("edge", "Edge", 6),
        ("test", "Test", 7),
        ("other", "Other", 8),
    ]
    for key, label, so in tags:
        conn.execute(
            text(
                """
                INSERT INTO topologytag (key, label, sort_order, enabled, is_system)
                SELECT :k, :l, :s, TRUE, TRUE
                WHERE NOT EXISTS (SELECT 1 FROM topologytag WHERE key = :k)
                """
            ),
            {"k": key, "l": label, "s": so},
        )


def downgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())
    for t in (
        "containerannotationtag",
        "containerannotation",
        "visualservicestack",
        "topologytag",
        "topologycategory",
    ):
        if t in tables:
            conn.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))
