"""Singleton app_setting table for operational Settings UI (DB-backed).

Revision ID: 009_app_setting
Revises: 008_api_token_cidrs
Create Date: 2026-07-10
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "009_app_setting"
down_revision: Union[str, None] = "008_api_token_cidrs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    tables = inspect(conn).get_table_names()
    if "appsetting" not in tables:
        conn.execute(
            text(
                """
                CREATE TABLE appsetting (
                    id SERIAL PRIMARY KEY,
                    data_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TIMESTAMP WITHOUT TIME ZONE
                )
                """
            )
        )
    conn.commit()


def downgrade() -> None:
    conn = op.get_bind()
    if "appsetting" in inspect(conn).get_table_names():
        conn.execute(text("DROP TABLE appsetting"))
    conn.commit()
