"""Unit tests for HAOS detect / ha CLI parsers (no live SSH)."""
from __future__ import annotations

from types import SimpleNamespace

from app.services import haos


OS_RELEASE_HAOS = """
ID=hassos
NAME="Home Assistant OS"
PRETTY_NAME="Home Assistant OS 14.2"
VERSION_ID=14.2
"""

OS_RELEASE_DEBIAN = """
ID=debian
PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"
"""

HA_CORE_INFO = """
arch: aarch64
channel: stable
version: 2025.1.2
version_latest: 2025.2.0
update_available: true
machine: raspberrypi5-64
"""

HA_CORE_JSON = """
{"version": "2025.1.2", "version_latest": "2025.1.2", "update_available": false, "machine": "qemux86-64"}
"""

HA_OS_INFO = """
version: 14.2
version_latest: 14.2
update_available: false
"""

HA_SUP_INFO = """
version: 2025.01.0
version_latest: 2025.02.0
update_available: true
"""


def test_os_release_looks_like_haos():
    assert haos.os_release_looks_like_haos(haos.parse_os_release(OS_RELEASE_HAOS))
    assert not haos.os_release_looks_like_haos(haos.parse_os_release(OS_RELEASE_DEBIAN))


def test_parse_ha_info_kv_and_update_flag():
    data = haos.parse_ha_info_blob(HA_CORE_INFO)
    assert data["version"] == "2025.1.2"
    assert data["version_latest"] == "2025.2.0"
    assert data["update_available"] is True
    fact = haos.component_fact_from_info("core", data)
    assert fact["update_available"] is True
    assert "core 2025.1.2 → 2025.2.0" in haos.summarize_component_sample(fact)


def test_parse_ha_info_json_no_update():
    data = haos.parse_ha_info_blob(HA_CORE_JSON)
    fact = haos.component_fact_from_info("core", data)
    assert fact["version"] == "2025.1.2"
    assert fact["update_available"] is False


def test_parse_ha_cli_raw_json_envelope():
    """Live HA CLI --raw-json wraps fields in {result, data}."""
    raw = (
        '{"result":"ok","data":{"version":"2026.7.3","version_latest":"2026.7.3",'
        '"update_available":false,"machine":"raspberrypi5-64","disk_total":234,'
        '"disk_used":26.7,"disk_free":197.8}}'
    )
    data = haos.parse_ha_info_blob(raw)
    assert data.get("version") == "2026.7.3"
    assert data.get("machine") == "raspberrypi5-64"
    assert "result" not in data or data.get("version")
    fact = haos.component_fact_from_info("core", data)
    assert fact["version"] == "2026.7.3"
    host = haos.parse_host_disk_facts(data)
    assert host["disk_total_gb"] == 234
    assert host["disk_free_gb"] == 197.8


def test_parse_df_h_busybox():
    from app.services.diagnostics import parse_df_h_output

    out = """Filesystem                Size      Used Available Use% Mounted on
overlay                 234.0G     26.7G    197.8G  12% /
/dev/nvme0n1p8          234.0G     26.7G    197.8G  12% /backup
tmpfs                     3.9G         0      3.9G   0% /dev/shm
"""
    drives = parse_df_h_output(out)
    assert len(drives) == 3
    assert drives[0]["target"] == "/"
    assert drives[0]["avail"] == "197.8G"
    assert drives[1]["target"] == "/backup"


def test_version_mismatch_implies_update():
    data = {"version": "1.0", "version_latest": "1.1"}
    assert haos._truthy_update(data) is True


def test_is_haos_server():
    assert haos.is_haos_server(SimpleNamespace(os_type="haos"))
    assert haos.is_haos_server(SimpleNamespace(os_type="HAOS"))
    assert not haos.is_haos_server(SimpleNamespace(os_type="debian"))


def test_parse_host_disk_facts():
    raw = """
hostname: homeassistant
operating_system: Home Assistant OS 14.2
kernel: 6.6.31
chassis: embedded
disk_total: 28.5
disk_used: 12.1
disk_free: 16.4
disk_life_time: 3
"""
    data = haos.parse_ha_info_blob(raw)
    facts = haos.parse_host_disk_facts(data)
    assert facts["disk_total_gb"] == 28.5
    assert facts["disk_free_gb"] == 16.4
    assert facts["disk_used_h"] is not None
    assert facts["disk_pcent"] is not None
    assert facts["chassis"] == "embedded"
    assert "Home Assistant OS" in (facts["operating_system"] or "")


def test_build_ha_summary_counts_components():
    components = {
        "core": haos.parse_ha_info_for_component("core", HA_CORE_INFO),
        "os": haos.parse_ha_info_for_component("os", HA_OS_INFO),
        "supervisor": haos.parse_ha_info_for_component("supervisor", HA_SUP_INFO),
    }
    summary = haos.build_ha_summary(components, marked_by="detected")
    assert summary["backend"] == "ha_cli"
    assert summary["actionable_count"] == 2  # core + supervisor
    assert summary["total_upgradable"] == 2
    assert any("core" in s for s in summary["packages_sample"])
    assert any("supervisor" in s for s in summary["packages_sample"])


def test_check_haos_updates_mocked(monkeypatch):
    class FakeClient:
        pass

    def fake_run(client, cmd, timeout=45):
        c = cmd or ""
        if "os-release" in c:
            return 0, OS_RELEASE_HAOS, ""
        if "command -v ha" in c or "which ha" in c:
            return 0, "/usr/bin/ha", ""
        if "core info" in c:
            return 0, HA_CORE_INFO, ""
        if "os info" in c:
            return 0, HA_OS_INFO, ""
        if "supervisor info" in c:
            return 0, HA_SUP_INFO, ""
        return 0, "", ""

    monkeypatch.setattr(haos, "_run", fake_run)
    server = SimpleNamespace(hostname="ha.local", os_type="debian")
    res = haos.check_haos_updates(server, client=FakeClient())
    assert res["supported"] is True
    assert res["backend"] == "ha_cli"
    assert res["updates_count"] == 2
    assert res["auto_mark_haos"] is True
    assert res["detected_os_type"] == "haos"
    assert res["ha"]["components"]["core"]["update_available"] is True


def test_check_os_updates_routes_to_haos(monkeypatch):
    from app.services import os_patching

    class FakeClient:
        def close(self):
            pass

    def fake_get_ssh(server):
        return FakeClient()

    def fake_check(server, client=None):
        return {
            "server": "x",
            "supported": True,
            "backend": "ha_cli",
            "updates_count": 1,
            "actionable_count": 1,
            "phased_count": 0,
            "total_upgradable": 1,
            "reboot_pending": False,
            "packages_sample": ["core 1 → 2"],
            "phased_sample": [],
            "error": None,
            "auto_mark_haos": True,
            "detected_os_type": "haos",
            "ha": {"components": {}},
        }

    monkeypatch.setattr(os_patching, "get_ssh_client", fake_get_ssh)
    monkeypatch.setattr(haos, "is_haos_server", lambda s: True)
    monkeypatch.setattr(haos, "check_haos_updates", fake_check)

    server = SimpleNamespace(hostname="ha", os_type="haos")
    res = os_patching.check_os_updates(server)
    assert res["backend"] == "ha_cli"
    assert res["updates_count"] == 1


def test_run_haos_update_apply_order(monkeypatch):
    class FakeClient:
        def close(self):
            pass

    cmds: list[str] = []

    def fake_get_ssh(server):
        return FakeClient()

    def fake_probe(client):
        return {"is_haos": True, "ha_cli": True, "signals": ["ha-cli"], "os_release_name": "HAOS"}

    def fake_collect(client):
        return (
            {
                "supervisor": {
                    "name": "supervisor",
                    "version": "1",
                    "version_latest": "2",
                    "update_available": True,
                },
                "core": {
                    "name": "core",
                    "version": "a",
                    "version_latest": "b",
                    "update_available": True,
                },
                "os": {
                    "name": "os",
                    "version": "10",
                    "version_latest": "10",
                    "update_available": False,
                },
            },
            None,
        )

    def fake_run(client, cmd, timeout=45):
        cmds.append(cmd)
        return 0, "ok", ""

    monkeypatch.setattr("app.services.ssh.get_ssh_client", fake_get_ssh)
    monkeypatch.setattr(haos, "probe_haos_identity", fake_probe)
    monkeypatch.setattr(haos, "collect_ha_component_facts", fake_collect)
    monkeypatch.setattr(haos, "_run", fake_run)

    server = SimpleNamespace(hostname="ha", os_type="haos")
    res = haos.run_haos_update(server, selected_steps=["update", "upgrade"])
    assert res["backend"] == "ha_cli"
    assert res.get("error") is None
    # supervisor then core; os skipped (no update)
    update_cmds = [c for c in cmds if "update" in c and "info" not in c]
    assert update_cmds[0] == "ha supervisor update"
    assert update_cmds[1] == "ha core update"
    assert all("ha os update" not in c for c in update_cmds)
    steps = [r["step"] for r in res["results"]]
    assert "update" in steps
    assert "supervisor" in steps
    assert "core" in steps
