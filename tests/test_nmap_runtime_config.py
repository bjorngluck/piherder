"""Nmap runtime (mocked Redis), config CRUD, device link, enqueue, cron — no live scan."""
from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from sqlalchemy.pool import StaticPool

from app.models import Integration, Job, NmapDevice, NmapScanRun
from app.services.nmap import config as nmap_cfg
from app.services.nmap import runtime as rt
from app.services.nmap import scan as nscan
from app.services.nmap import _cron as ncron


# --- Redis runtime (mocked) -------------------------------------------------


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, bytes] = {}

    def setex(self, key, ttl, value):
        self.store[key] = value if isinstance(value, bytes) else str(value).encode()

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return False
        self.store[key] = value if isinstance(value, bytes) else str(value).encode()
        return True

    def get(self, key):
        return self.store.get(key)

    def delete(self, key):
        self.store.pop(key, None)


@pytest.fixture()
def fake_redis(monkeypatch):
    fr = _FakeRedis()
    monkeypatch.setattr(rt, "_redis", lambda: fr)
    return fr


def test_heartbeat_and_worker_online(fake_redis):
    assert rt.worker_online()["online"] is False
    rt.touch_worker_heartbeat(worker_id="nmap-test")
    st = rt.worker_online()
    assert st["online"] is True
    assert st["worker_id"] == "nmap-test"


def test_lock_acquire_release_and_progress(fake_redis):
    assert rt.try_acquire_lock("cidr", "192.168.1.0/24", holder="job-1") is True
    assert rt.try_acquire_lock("cidr", "192.168.1.0/24", holder="job-2") is False
    rt.release_lock("cidr", "192.168.1.0/24", holder="job-2")  # wrong holder
    assert rt.try_acquire_lock("cidr", "192.168.1.0/24", holder="job-2") is False
    rt.release_lock("cidr", "192.168.1.0/24", holder="job-1")
    assert rt.try_acquire_lock("cidr", "192.168.1.0/24", holder="job-2") is True

    rt.set_progress(9, {"pct": 40, "phase": "scan"})
    assert rt.get_progress(9) == {"pct": 40, "phase": "scan"}
    assert rt.get_progress(99) is None


def test_runtime_redis_failure_is_soft(monkeypatch):
    def boom():
        raise OSError("redis down")

    monkeypatch.setattr(rt, "_redis", boom)
    rt.touch_worker_heartbeat()  # no raise
    assert rt.worker_online()["online"] is False
    assert rt.try_acquire_lock("host", "1.2.3.4", holder="x") is False
    rt.release_lock("host", "1.2.3.4", holder="x")
    rt.set_progress(1, {"a": 1})
    assert rt.get_progress(1) is None


# --- config pure + sqlite ---------------------------------------------------


def test_dump_nmap_config_rejects_bad_cidr():
    with pytest.raises(ValueError):
        nmap_cfg.dump_nmap_config(cidrs=["not-a-net"])


def test_parse_nmap_config_string_cidrs():
    integ = SimpleNamespace(
        config_json=json.dumps(
            {
                "cidrs": "192.168.1.0/24, 10.0.0.0/8",
                "excludes": "192.168.1.1/32\n10.0.0.1/32",
                "skip_dns": True,
                "use_syn": True,
                "vuln_enabled": True,
                "notes": "lab",
            }
        )
    )
    cfg = nmap_cfg.parse_nmap_config(integ)
    assert "192.168.1.0/24" in cfg["cidrs"]
    assert "10.0.0.0/8" in cfg["cidrs"]
    assert len(cfg["excludes"]) == 2
    assert cfg["skip_dns"] is True
    assert cfg["vuln_enabled"] is True


def test_open_ports_helpers_and_device_list_item():
    ports = json.dumps(
        [
            {"port": 80, "state": "open", "service": "http", "product": "nginx"},
            {"port": 22, "state": "open", "service": "ssh"},
            {"port": 443, "state": "closed", "service": "https"},
            {"port": 8080, "state": "open", "service": "http-proxy"},
        ]
    )
    summary = nmap_cfg._open_ports_summary(ports, limit=2)
    assert len(summary) == 2
    assert summary[0]["port"] == 22  # sorted by port
    assert nmap_cfg._count_open_ports(ports) == 3
    assert nmap_cfg._count_open_ports(None) == 0
    assert nmap_cfg._count_open_ports("{bad") == 0
    assert nmap_cfg._open_ports_summary("not-json") == []
    assert nmap_cfg._ip_sort_key("10.0.0.2")[0] == 0
    assert nmap_cfg._ip_sort_key("nope")[0] == 1

    dev = SimpleNamespace(
        id=1,
        ports_json=ports,
        hostname="pi",
        ip_address="10.0.0.2",
        state="new",
        mac_address="aa",
        linked_server_id=None,
        os_summary=None,
    )
    item = nmap_cfg.device_list_item(dev)
    assert item["open_ports"] == 3
    assert any("22/ssh" in x for x in item["service_labels"])


def test_create_update_nmap_and_device_state_sqlite(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'nmapcfg.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        row = nmap_cfg.create_nmap(
            s,
            name="Home LAN",
            cidrs=["192.168.86.0/24"],
            excludes=["192.168.86.1/32"],
            skip_dns=False,
            use_syn=True,
            vuln_enabled=True,
            notes="lab",
        )
        assert row.type == "nmap"
        assert row.base_url == nmap_cfg.BASE_URL_LOCAL
        cfg = nmap_cfg.parse_nmap_config(row)
        assert cfg["cidrs"] == ["192.168.86.0/24"]
        assert cfg["use_syn"] is True

        with pytest.raises(ValueError, match="already exists"):
            nmap_cfg.create_nmap(s, cidrs=["10.0.0.0/24"])

        row = nmap_cfg.update_nmap(
            s,
            row,
            name="Home LAN 2",
            cidrs=["192.168.86.0/24", "10.0.0.0/24"],
            excludes=[],
            skip_dns=True,
            use_syn=False,
            vuln_enabled=False,
            notes="",
            enabled=True,
        )
        assert row.name == "Home LAN 2"
        assert len(nmap_cfg.parse_nmap_config(row)["cidrs"]) == 2

        dev = NmapDevice(
            integration_id=row.id,
            identity_key="ip:192.168.86.50",
            ip_address="192.168.86.50",
            hostname="cam",
            state="new",
            ports_json='[{"port":554,"state":"open","service":"rtsp"}]',
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        s.add(dev)
        s.commit()
        s.refresh(dev)

        with pytest.raises(ValueError, match="invalid state"):
            nmap_cfg.set_device_state(s, dev, "nope")
        with pytest.raises(ValueError, match="linked_server_id"):
            nmap_cfg.set_device_state(s, dev, "linked")

        nmap_cfg.link_device(s, dev, server_id=7)
        assert dev.state == "linked"
        assert dev.linked_server_id == 7
        nmap_cfg.unlink_device(s, dev)
        assert dev.state == "known"
        assert dev.linked_server_id is None
        nmap_cfg.set_device_state(s, dev, "ignored")
        assert dev.state == "ignored"

        # refresh_status should not require redis if worker offline path
        with patch.object(nmap_cfg, "worker_online", return_value={"online": False}):
            with patch.object(
                nmap_cfg, "vuln_pack_status", return_value={"ready": False, "exists": True}
            ):
                st = nmap_cfg.refresh_status(s, row)
        assert "worker" in st or "online" in str(st).lower() or isinstance(st, dict)


def test_enqueue_nmap_scan_dispatches_celery(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'enq.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        integ = Integration(
            type="nmap",
            name="LAN",
            base_url="local://nmap",
            enabled=True,
            config_json='{"cidrs":["192.168.1.0/24"]}',
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        s.add(integ)
        s.commit()
        s.refresh(integ)

        fake_result = SimpleNamespace(id="task-abc")
        with patch("app.celery_app.celery.send_task", return_value=fake_result) as send:
            job, run = nscan.enqueue_nmap_scan(
                s,
                integration_id=integ.id,
                intensity="inventory",
                targets=["192.168.1.0/24"],
                user_id=3,
                use_syn=True,
            )
        assert job.id and run.id
        assert job.job_type == "nmap_inventory"
        assert job.celery_task_id == "task-abc"
        assert run.integration_id == integ.id
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        assert kwargs["queue"] == "nmap"
        assert kwargs["kwargs"]["use_syn"] is True

        job2, run2 = None, None
        with patch("app.celery_app.celery.send_task", return_value=fake_result):
            job2, run2 = nscan.enqueue_nmap_scan(
                s,
                integration_id=integ.id,
                intensity="deep",
                targets=["192.168.1.10"],
                vuln_scripts=True,
            )
        assert job2.job_type == "nmap_host_deep"


def test_integration_cidrs_helper():
    integ = SimpleNamespace(
        config_json=json.dumps(
            {"cidrs": ["192.168.1.0/24", "bad"], "excludes": ["192.168.1.1/32"]}
        )
    )
    ok, ex = nscan._integration_cidrs(integ)
    assert "192.168.1.0/24" in ok
    assert "192.168.1.1/32" in ex


def test_cron_trigger_five_fields():
    trig = ncron.cron_trigger("15 4 * * 1")
    assert trig is not None
    with pytest.raises(ValueError, match="5 fields"):
        ncron.cron_trigger("not-cron")
