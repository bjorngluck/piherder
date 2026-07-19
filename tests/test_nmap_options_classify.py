"""Curated scan options, script presets, classification, soft-embed helpers."""
from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models import Integration, NmapDevice, NmapScriptResult, Server
from app.services.nmap import argv as av
from app.services.nmap import config as nmap_cfg
from app.services.nmap import options as opts
from app.services.nmap import script_classify as sc
from app.services.nmap.scan import _script_args_for_preset


def test_normalize_port_list_allowlist():
    assert opts.normalize_port_list("22, 80,443") == "22,80,443"
    assert opts.normalize_port_list("1-1024") == "1-1024"
    assert opts.normalize_port_list("") is None
    assert opts.normalize_port_list("22;rm -rf") is None
    assert opts.normalize_port_list("0") is None
    assert opts.normalize_port_list("70000") is None


def test_script_preset_and_form_options():
    assert opts.normalize_script_preset("CPE") == "cpe"
    assert opts.normalize_script_preset(None, vuln_scripts_fallback=True) == "full"
    assert opts.normalize_script_preset(None, vuln_scripts_fallback=False) == "none"
    assert opts.preset_wants_scripts("offline") is True
    assert opts.preset_wants_scripts("none") is False

    o = opts.form_scan_options(script_preset="offline", timing=3, top_ports=50)
    assert o["script_preset"] == "offline"
    assert o["vuln_scripts"] is True
    assert o["timing"] == 3
    assert o["top_ports"] == 50

    o2 = opts.form_scan_options(vuln_scripts=True)  # legacy
    assert o2["script_preset"] == "full"
    assert o2["timing"] == opts.DEFAULT_TIMING

    o3 = opts.form_scan_options(script_preset="none", timing=None)
    assert o3["timing"] is None


def test_build_argv_curated_timing_ports_udp():
    a = av.build_nmap_argv(
        "inventory",
        ["10.0.0.1"],
        output_xml="/tmp/o.xml",
        timing=3,
        top_ports=50,
        include_udp=True,
    )
    assert "-T3" in a
    assert "--top-ports" in a
    assert "50" in a
    assert "-sU" in a

    b = av.build_nmap_argv(
        "deep",
        ["10.0.0.1"],
        output_xml="/tmp/o.xml",
        timing=5,
        port_list="22,443",
        vuln_scripts=False,
    )
    assert "-T5" in b
    assert "-p" in b
    assert "22,443" in b
    assert "-p-" not in b

    d = av.build_nmap_argv(
        "discovery", ["192.168.1.0/24"], output_xml="/tmp/o.xml", timing=4
    )
    assert "-T4" not in d  # not applied to -sn


def test_script_args_presets(tmp_path, monkeypatch):
    monkeypatch.setenv("PIHERDER_NMAP_VULN_ROOT", str(tmp_path))
    assert _script_args_for_preset("none") == []
    assert _script_args_for_preset("cpe") == ["--script", "vulners"]

    # offline without pack falls back to vulners
    assert _script_args_for_preset("offline") == ["--script", "vulners"]

    (tmp_path / "vulscan").mkdir()
    (tmp_path / "vulscan" / "vulscan.nse").write_text("--\n", encoding="utf-8")
    off = _script_args_for_preset("offline")
    assert off[0] == "--script"
    assert str(tmp_path / "vulscan" / "vulscan.nse") in off[1]

    full = _script_args_for_preset("full")
    assert full[0] == "--script"
    assert full[1].startswith("vuln")


def test_classify_finding_clear_error_info():
    assert (
        sc.classify_script_result(
            "http-sql-injection", "ERROR: Script execution failed (use -d to debug)"
        )["kind"]
        == "error"
    )
    assert (
        sc.classify_script_result(
            "http-csrf", "Couldn't find any CSRF vulnerabilities."
        )["kind"]
        == "clear"
    )
    assert (
        sc.classify_script_result(
            "http-slowloris-check",
            "VULNERABLE:\n  Slowloris DOS\n    State: LIKELY VULNERABLE",
        )["kind"]
        == "finding"
    )
    c = sc.classify_script_result(
        "vulners",
        "cpe:/a:openbsd:openssh:10.2p1:\nCVE-2026-60002 9.4",
        cve_ids_json='["CVE-2026-60002"]',
    )
    assert c["kind"] == "finding"
    assert "CVE-2026-60002" in c["cve_ids"]
    assert (
        sc.classify_script_result("fingerprint-strings", "GetRequest:\n  HTTP/1.0 403")[
            "kind"
        ]
        == "info"
    )

    rows = [
        SimpleNamespace(
            id=1,
            script_id="http-csrf",
            output="Couldn't find any",
            cve_ids_json=None,
        ),
        SimpleNamespace(
            id=2,
            script_id="http-passwd",
            output="ERROR: Script execution failed",
            cve_ids_json=None,
        ),
        SimpleNamespace(
            id=3,
            script_id="vulners",
            output="CVE-2020-1",
            cve_ids_json='["CVE-2020-1"]',
        ),
    ]
    classified = sc.classify_scripts(rows)
    assert classified[0]["kind"] == "finding"
    counts = sc.script_summary_counts(classified)
    assert counts["finding"] == 1
    assert counts["error"] == 1
    assert counts["clear"] == 1
    assert counts["total"] == 3


def test_schedule_options_roundtrip_presets():
    from app.services.nmap import schedules as nmap_sched

    raw = nmap_sched.dump_schedule_options(
        script_preset="cpe",
        timing=3,
        top_ports=80,
        include_udp=True,
        use_syn=True,
        port_list="22,80",
    )
    data = json.loads(raw)
    assert data["script_preset"] == "cpe"
    assert data["vuln_scripts"] is True
    assert data["timing"] == 3
    assert data["include_udp"] is True
    assert data["port_list"] == "22,80"

    row = SimpleNamespace(options_json=raw)
    parsed = nmap_sched.parse_schedule_options(row)
    assert parsed["script_preset"] == "cpe"
    assert parsed["use_syn"] is True


def test_discovery_embed_and_chips_sqlite(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'embed.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        server = Server(
            name="pi-lab",
            hostname="192.168.1.10",
            ssh_username="pi",
            sort_order=0,
        )
        session.add(server)
        session.commit()
        session.refresh(server)

        integ = Integration(
            type="nmap",
            name="LAN",
            base_url="local://nmap",
            enabled=True,
            config_json=json.dumps({"cidrs": ["192.168.1.0/24"]}),
        )
        session.add(integ)
        session.commit()
        session.refresh(integ)

        dev = NmapDevice(
            integration_id=integ.id,
            identity_key="ip:192.168.1.10",
            ip_address="192.168.1.10",
            hostname="pi-lab.local",
            state="linked",
            linked_server_id=server.id,
            ports_json=json.dumps(
                [
                    {"port": 22, "state": "open", "service": "ssh"},
                    {"port": 80, "state": "open", "service": "http"},
                ]
            ),
            first_seen_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        session.add(dev)
        session.commit()
        session.refresh(dev)

        session.add(
            NmapScriptResult(
                device_id=dev.id,
                script_id="http-csrf",
                output="Couldn't find any CSRF vulnerabilities.",
            )
        )
        session.add(
            NmapScriptResult(
                device_id=dev.id,
                script_id="http-sql-injection",
                output="ERROR: Script execution failed",
            )
        )
        session.commit()

        embed = nmap_cfg.discovery_embed_for_server(session, server.id)
        assert embed is not None
        assert embed["device"].id == dev.id
        assert embed["open_ports"] == 2
        assert embed["script_counts"]["clear"] >= 1
        assert embed["script_counts"]["error"] >= 1
        assert f"device={dev.id}" in embed["href"]

        chips = nmap_cfg.discovery_chips_by_server(session, [server.id])
        assert server.id in chips
        assert chips[server.id]["ip"] == "192.168.1.10"
        assert chips[server.id]["open_ports"] == 2

        assert nmap_cfg.discovery_embed_for_server(session, 99999) is None
