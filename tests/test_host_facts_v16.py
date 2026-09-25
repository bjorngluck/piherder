"""v1.6 host facts snapshot — parse OS/hardware without SSH."""
from __future__ import annotations

from app.services.host_facts import (
    disk_bytes_from_summary,
    family_from_ids,
    parse_human_bytes,
    parse_meminfo_kb,
    parse_nproc,
    parse_os_release_blob,
    pick_hardware,
)
from app.services.api_summary import fleet_summary, os_display_label
from app.services.api_summary import os_display_label


def test_parse_os_release_ubuntu():
    blob = 'ID=ubuntu\nPRETTY=Ubuntu 24.04.3 LTS\n'
    os_id, pretty = parse_os_release_blob(blob)
    assert os_id == "ubuntu"
    assert pretty == "Ubuntu 24.04.3 LTS"


def test_family_haos_and_ubuntu():
    assert family_from_ids("debian", "Home Assistant OS 14.2", "haos") == "haos"
    assert family_from_ids("ubuntu", "Ubuntu 24.04.3 LTS", None) == "ubuntu"


def test_pick_hardware_pi_then_dmi():
    assert pick_hardware(device_tree="Raspberry Pi 5 Model B Rev 1.0") == "Raspberry Pi 5 Model B Rev 1.0"
    assert pick_hardware(dmi_vendor="Dell Inc.", dmi_product="OptiPlex") == "Dell Inc. OptiPlex"
    assert pick_hardware(ha_chassis="green") == "green"


def test_os_display_prefers_pretty():
    assert os_display_label("debian", os_pretty="Ubuntu 24.04.3 LTS", os_id="ubuntu") == "Ubuntu 24.04.3 LTS"
    assert os_display_label("debian") == "Debian"


def test_parse_human_bytes_and_meminfo():
    assert parse_human_bytes("15G") == 15 * 1024**3
    assert parse_human_bytes("512M") == 512 * 1024**2
    total, used = parse_meminfo_kb("MemTotal: 8000000 kB\nMemAvailable: 2000000 kB\n")
    assert total == 8000000 * 1024
    assert used == 6000000 * 1024
    assert parse_nproc("4\n") == 4
    dt, du = disk_bytes_from_summary({"root": {"size": "32G", "used": "10G"}})
    assert dt == 32 * 1024**3
    assert du == 10 * 1024**3


def test_fleet_summary_sums_resources():
    from types import SimpleNamespace

    a = SimpleNamespace(
        os_updates_count=0,
        container_updates_count=0,
        reboot_pending=False,
        last_backup_at=None,
        cpu_cores=4,
        memory_total_bytes=8,
        memory_used_bytes=3,
        disk_total_bytes=100,
        disk_used_bytes=40,
        container_count=5,
    )
    b = SimpleNamespace(
        os_updates_count=0,
        container_updates_count=0,
        reboot_pending=False,
        last_backup_at=None,
        cpu_cores=2,
        memory_total_bytes=2,
        memory_used_bytes=1,
        disk_total_bytes=50,
        disk_used_bytes=10,
        container_count=7,
    )
    out = fleet_summary([a, b], [])
    assert out["cpu_cores"] == 6
    assert out["memory_total_bytes"] == 10
    assert out["memory_used_bytes"] == 4
    assert out["disk_total_bytes"] == 150
    assert out["disk_used_bytes"] == 50
    assert out["containers"] == 12
