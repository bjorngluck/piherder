"""Demo fleet seed pack (Stream D)."""
from __future__ import annotations

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import Integration, Job, NmapDevice, Server, User
from app.services import demo_seed as seed


@pytest.fixture()
def session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'seed.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_seed_creates_fleet(session, monkeypatch):
    monkeypatch.setattr(seed, "demo_mode", lambda: True)
    monkeypatch.setattr(
        "app.services.app_settings.save_settings",
        lambda partial: partial,
    )
    monkeypatch.setattr(
        "app.services.app_settings.load_settings",
        lambda: {"force_2fa": False},
    )
    summary = seed.seed_demo_fleet(
        session,
        force=True,
        password="TestDemo1ok",
        email="demo@test.local",
    )
    assert summary["skipped"] is False
    assert summary["servers"] == 6
    servers = list(session.exec(select(Server)).all())
    assert len(servers) == 6
    assert any(s.hostname == "lab-core.demo" for s in servers)
    core = next(s for s in servers if s.name == "lab-core")
    assert core.docker_inventory_status == "ok"
    assert core.docker_inventory_json and "piherder" in core.docker_inventory_json

    user = session.exec(select(User).where(User.email == "demo@test.local")).first()
    assert user is not None
    assert user.role == "viewer"
    assert (user.display_name or "").lower().find("demo") >= 0

    jobs = list(session.exec(select(Job)).all())
    assert len(jobs) >= 5
    assert any(j.status == "failed" for j in jobs)

    integs = list(session.exec(select(Integration)).all())
    assert any(i.type == "nmap" for i in integs)

    devices = list(session.exec(select(NmapDevice)).all())
    assert len(devices) >= 6


def test_seed_idempotent_without_force(session, monkeypatch):
    monkeypatch.setattr(seed, "demo_mode", lambda: True)
    monkeypatch.setattr("app.services.app_settings.save_settings", lambda p: p)
    monkeypatch.setattr("app.services.app_settings.load_settings", lambda: {})
    seed.seed_demo_fleet(session, force=True, password="x", email="a@b.c")
    n1 = len(list(session.exec(select(Server)).all()))
    again = seed.seed_demo_fleet(session, force=False, password="x", email="a@b.c")
    assert again["skipped"] is True
    n2 = len(list(session.exec(select(Server)).all()))
    assert n1 == n2 == 6


def test_seed_refuses_without_demo_mode(session, monkeypatch):
    monkeypatch.setattr(seed, "demo_mode", lambda: False)
    with pytest.raises(RuntimeError, match="DEMO_MODE"):
        seed.seed_demo_fleet(session, force=True, password="x", email="a@b.c")


def test_ensure_demo_seeded_respects_flag(session, monkeypatch):
    monkeypatch.setattr("app.services.demo_seed.demo_mode", lambda: False)
    assert seed.ensure_demo_seeded(session) is None

    monkeypatch.setattr("app.services.demo_seed.demo_mode", lambda: True)
    monkeypatch.setattr("app.services.app_settings.save_settings", lambda p: p)
    monkeypatch.setattr("app.services.app_settings.load_settings", lambda: {})
    result = seed.ensure_demo_seeded(session)
    assert result is not None
    assert result["servers"] == 6
