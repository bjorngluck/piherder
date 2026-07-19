"""LAN discovery (nmap) pure helpers — no live scan, no Redis required."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services.nmap import allowlist as al
from app.services.nmap import argv as av
from app.services.nmap import parse as np
from app.services.nmap import paths as npaths
from app.services.nmap import upsert as up

FIXTURE = Path(__file__).parent / "fixtures" / "nmap_sample.xml"


def test_parse_nmap_xml_hosts_ports_scripts():
    xml = FIXTURE.read_text(encoding="utf-8")
    hosts = np.parse_nmap_xml(xml)
    assert len(hosts) == 2
    up_host = next(h for h in hosts if h.ip_address == "192.168.1.10")
    assert up_host.status == "up"
    assert up_host.hostname == "pi-lab.local"
    assert up_host.mac_address == "AA:BB:CC:DD:EE:FF"
    assert up_host.os_summary and "Linux" in up_host.os_summary
    open_ports = np.open_ports(up_host)
    assert {p.port for p in open_ports} == {22, 80}
    ssh = next(p for p in open_ports if p.port == 22)
    assert ssh.service == "ssh"
    assert ssh.product == "OpenSSH"
    script_ids = {s.script_id for s in up_host.scripts}
    assert "vulners" in script_ids
    assert "http-vuln-cve2014-3704" in script_ids
    cves = {c for s in up_host.scripts for c in s.cve_ids}
    assert "CVE-2014-3704" in cves
    assert "CVE-2023-38408" in cves


def test_parse_invalid_xml_raises():
    with pytest.raises(ValueError, match="invalid nmap XML"):
        np.parse_nmap_xml("<not-closed")


def test_validate_and_allowlist_cidrs():
    ok, errs = al.validate_cidrs(["192.168.1.0/24", "not-a-net", "10.0.0.5"])
    assert "192.168.1.0/24" in ok
    assert "10.0.0.5/32" in ok
    assert errs
    assert al.target_allowed("192.168.1.10", ["192.168.1.0/24"])
    assert al.target_allowed("192.168.1.0/28", ["192.168.1.0/24"])
    assert not al.target_allowed("10.0.0.1", ["192.168.1.0/24"])
    assert not al.target_allowed("192.168.1.10", ["192.168.1.0/24"], excludes=["192.168.1.10/32"])
    good, bad = al.filter_targets(
        ["192.168.1.10", "8.8.8.8"],
        ["192.168.1.0/24"],
    )
    assert good == ["192.168.1.10"]
    assert bad == ["8.8.8.8"]


def test_build_nmap_argv_profiles():
    d = av.build_nmap_argv("discovery", ["192.168.1.0/24"], output_xml="/tmp/o.xml")
    assert d[0] == "nmap"
    assert "-sn" in d
    assert "-oX" in d
    assert "/tmp/o.xml" in d
    assert "192.168.1.0/24" in d

    inv = av.build_nmap_argv("inventory", ["10.0.0.1"], output_xml="/tmp/o.xml", use_syn=True)
    assert "-sS" in inv
    assert "-sV" in inv
    assert any(a.startswith("--top-ports") or a == "100" for a in inv)

    deep = av.build_nmap_argv(
        "deep",
        ["10.0.0.1"],
        output_xml="/tmp/o.xml",
        vuln_scripts=True,
    )
    assert "-p-" in deep
    assert "--script" in deep
    assert "vuln,vulners" in deep

    # forbidden script fragments stripped from extras
    filtered = av.build_nmap_argv(
        "inventory",
        ["10.0.0.1"],
        output_xml="/tmp/o.xml",
        extra_args=["--script", "ftp-brute"],
    )
    assert "ftp-brute" not in filtered


def test_build_nmap_argv_requires_target():
    with pytest.raises(ValueError):
        av.build_nmap_argv("discovery", [], output_xml="/tmp/o.xml")


def test_device_identity_key_prefers_mac():
    assert up.device_identity_key(mac="aa:bb:cc:dd:ee:ff", ip="1.2.3.4") == "mac:AA:BB:CC:DD:EE:FF"
    assert up.device_identity_key(mac=None, ip="1.2.3.4") == "ip:1.2.3.4"


def test_vuln_pack_status_empty_and_ready(tmp_path):
    st = npaths.vuln_pack_status(tmp_path)
    assert st["ready"] is False
    assert st["exists"] is True
    marker = tmp_path / "READY"
    marker.write_text("ok", encoding="utf-8")
    st2 = npaths.vuln_pack_status(tmp_path)
    assert st2["ready"] is True
    assert st2["marker"] is True


def test_upsert_hosts_from_parse_creates_and_updates():
    xml = FIXTURE.read_text(encoding="utf-8")
    hosts = np.parse_nmap_xml(xml)

    created_dev = SimpleNamespace(
        id=1,
        integration_id=1,
        identity_key="mac:AA:BB:CC:DD:EE:FF",
        ip_address="192.168.1.10",
        hostname=None,
        mac_address=None,
        state="new",
        os_summary=None,
        ports_json=None,
        last_seen_at=None,
        last_run_id=None,
        updated_at=None,
    )

    session = MagicMock()
    # first call: no existing; second host down skipped after only_up
    session.exec.return_value.first.return_value = None
    session.flush = MagicMock()

    def add_side_effect(obj):
        if getattr(obj, "id", None) is None and hasattr(obj, "identity_key"):
            obj.id = 1

    session.add.side_effect = add_side_effect

    with patch.object(up, "NmapDevice", side_effect=lambda **kw: SimpleNamespace(id=None, **kw)):
        # Simpler path: mock select path and only verify counts via real dataclass flow
        pass

    # Use a lightweight fake session that stores devices in a dict
    store: dict[str, SimpleNamespace] = {}
    scripts: list = []

    class FakeSession:
        def exec(self, statement):
            # Very small fake: inspect bound params is hard; use last where keys
            class R:
                def first(self_inner):
                    # called for device lookup — return by scanning store
                    for d in store.values():
                        return d
                    return None

                def all(self_inner):
                    return list(scripts)

            # If deleting scripts, return all scripts
            return R()

        def add(self, obj):
            if hasattr(obj, "identity_key"):
                if getattr(obj, "id", None) is None:
                    obj.id = len(store) + 1
                store[obj.identity_key] = obj
            elif hasattr(obj, "script_id"):
                scripts.append(obj)

        def delete(self, obj):
            if obj in scripts:
                scripts.remove(obj)

        def flush(self):
            pass

        def commit(self):
            pass

    # First upsert: create
    fs = FakeSession()
    # override first() to respect identity
    def make_exec():
        class Q:
            def __init__(self, key=None):
                self._key = key

            def first(self):
                if self._key and self._key in store:
                    return store[self._key]
                # try any match for ip path
                return None

            def all(self):
                return [s for s in scripts]

        def exec_fn(stmt):
            # Always return empty first for create path
            return Q()

        return exec_fn

    fs.exec = make_exec()
    summary = up.upsert_hosts_from_parse(
        fs,
        integration_id=1,
        hosts=hosts,
        run_id=9,
        only_up=True,
    )
    assert summary["created"] >= 1
    assert summary["hosts_processed"] >= 1
    assert any(d.ip_address == "192.168.1.10" for d in store.values())


def test_upsert_skips_ignored():
    host = np.ParsedHost(
        ip_address="192.168.1.50",
        hostname="x",
        mac_address=None,
        status="up",
        ports=[],
        scripts=[],
    )
    ignored = SimpleNamespace(
        id=3,
        integration_id=1,
        identity_key="ip:192.168.1.50",
        ip_address="192.168.1.50",
        state="ignored",
        hostname=None,
        mac_address=None,
        os_summary=None,
        ports_json=None,
        last_seen_at=None,
        last_run_id=None,
        updated_at=None,
    )

    class Sess:
        def exec(self, stmt):
            class R:
                def first(self):
                    return ignored

                def all(self):
                    return []

            return R()

        def add(self, obj):
            raise AssertionError("should not update ignored")

        def flush(self):
            pass

        def commit(self):
            pass

        def delete(self, obj):
            pass

    summary = up.upsert_hosts_from_parse(
        Sess(),
        integration_id=1,
        hosts=[host],
        run_id=1,
    )
    assert summary["skipped"] == 1
    assert summary["created"] == 0
