"""Global update-check helpers (pure + light sqlite)."""
from __future__ import annotations

from datetime import datetime

from sqlmodel import Session, SQLModel, create_engine, select
from sqlalchemy.pool import StaticPool

from app.models import Server
from app.services.update_check_config import (
    DEFAULT_MIDNIGHT_CRON,
    apply_global_update_checks_to_all,
    staggered_cron,
)


def test_staggered_cron_minutes():
    assert staggered_cron("0 3 * * *", 1) == "1 3 * * *"
    assert staggered_cron("0 3 * * *", 60) == "0 3 * * *"  # % 60
    assert staggered_cron("0 3 * * *", 5, offset=15) == "20 3 * * *"
    # bad cron falls back to midnight then staggers
    out = staggered_cron("not-cron", 7)
    parts = out.split()
    assert len(parts) == 5
    assert parts[0] == "7"


def test_apply_global_update_checks_to_all(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'uc.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        a = Server(
            name="a",
            hostname="10.0.0.1",
            ssh_username="pi",
            created_at=datetime.utcnow(),
        )
        b = Server(
            name="b",
            hostname="10.0.0.2",
            ssh_username="pi",
            created_at=datetime.utcnow(),
            os_patch_enabled=False,
            container_patch_enabled=False,
            backup_enabled=False,
        )
        s.add(a)
        s.add(b)
        s.commit()

        counts = apply_global_update_checks_to_all(
            s,
            os_enabled=True,
            os_cron=DEFAULT_MIDNIGHT_CRON,
            container_enabled=True,
            container_cron="0 2 * * *",
            jitter=True,
            enable_feature_flags=True,
            enable_backups=True,
        )
        s.commit()
        rows = list(s.exec(select(Server)).all())
        assert len(rows) == 2
        assert all(r.os_check_enabled for r in rows)
        assert all(r.container_check_enabled for r in rows)
        assert counts["os_applied"] == 2
        assert counts["container_applied"] == 2
        # feature flags flipped on for hosts that were off
        assert any(r.os_patch_enabled for r in rows)
