"""v1.6 HA-p2 — GET /api/v1/summary heartbeat (DB only)."""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.services.api_summary import fleet_summary
from app.services.api_tokens import api_meta_dict
from app.version_info import APP_VERSION


def test_fleet_summary_empty():
    out = fleet_summary([], [])
    assert out["ok"] is True
    assert out["version"] == APP_VERSION
    assert out["hosts"] == 0
    assert out["os_updates"] == 0
    assert out["container_updates"] == 0
    assert out["reboot_pending"] == 0
    assert out["jobs_running"] == 0
    assert out["move_running"] is False
    assert out["last_backup_oldest_at"] is None


def test_fleet_summary_counts_hosts_not_packages():
    t_old = datetime(2026, 9, 1, 2, 0, 0)
    t_new = datetime(2026, 9, 10, 2, 0, 0)
    servers = [
        SimpleNamespace(
            os_updates_count=12,
            container_updates_count=0,
            reboot_pending=True,
            last_backup_at=t_new,
        ),
        SimpleNamespace(
            os_updates_count=0,
            container_updates_count=3,
            reboot_pending=False,
            last_backup_at=t_old,
        ),
        SimpleNamespace(
            os_updates_count=None,
            container_updates_count=None,
            reboot_pending=False,
            last_backup_at=None,
        ),
    ]
    jobs = [
        SimpleNamespace(status="running", job_type="backup"),
        SimpleNamespace(status="pending", job_type="service_migrate"),
        SimpleNamespace(status="success", job_type="service_migrate"),
    ]
    out = fleet_summary(servers, jobs, version="1.5.0")
    assert out["hosts"] == 3
    assert out["os_updates"] == 1
    assert out["container_updates"] == 1
    assert out["reboot_pending"] == 1
    assert out["jobs_running"] == 2
    assert out["move_running"] is True
    assert out["last_backup_oldest_at"] == t_old.isoformat()
    assert out["version"] == "1.5.0"


def test_move_running_false_when_no_active_migrate():
    jobs = [SimpleNamespace(status="running", job_type="backup")]
    assert fleet_summary([], jobs)["move_running"] is False


def test_api_meta_lists_summary():
    paths = [e["path"] for e in api_meta_dict()["endpoints"]]
    assert "/api/v1/summary" in paths
    assert "/api/v1/inventory" in paths
    assert "/api/v1/services" in paths


def test_os_display_label_ubuntu_haos_not_raw_debian():
    from app.services.api_summary import os_display_label

    assert os_display_label("haos") == "HAOS"
    assert os_display_label("ubuntu") == "Ubuntu"
    assert os_display_label("debian") == "Debian"
    ident = '{"identity":{"os_release_name":"Ubuntu 24.04.3 LTS"}}'
    assert os_display_label("debian", ident) == "Ubuntu"
    ha = '{"ha":{"host":{"operating_system":"Home Assistant OS 14.2"}}}'
    assert os_display_label("debian", ha) == "HAOS"


def test_fleet_summary_alerts_open():
    out = fleet_summary([], [], alerts_open=3)
    assert out["alerts_open"] == 3
