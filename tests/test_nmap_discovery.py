"""LAN discovery (nmap) pure helpers — no live scan, no Redis required."""
from __future__ import annotations

import json
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
    assert "-PR" in d  # ARP ping when on L2
    assert "-oX" in d
    assert "/tmp/o.xml" in d
    assert "192.168.1.0/24" in d
    # skip_dns True adds -n (no reverse DNS hostnames)
    d_n = av.build_nmap_argv(
        "discovery", ["192.168.1.0/24"], output_xml="/tmp/o.xml", skip_dns=True
    )
    assert "-n" in d_n
    d_dns = av.build_nmap_argv(
        "discovery", ["192.168.1.0/24"], output_xml="/tmp/o.xml", skip_dns=False
    )
    assert "-n" not in d_dns

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


def test_vuln_update_writes_ready_marker(tmp_path, monkeypatch):
    """Pack update with mocked downloads creates READY (no live network)."""
    from app.services.nmap import vuln_update as vu
    from app.services.nmap import paths as npaths

    monkeypatch.setenv("PIHERDER_NMAP_VULN_ROOT", str(tmp_path))

    def fake_vulners(root, log):
        d = root / "nmap-vulners"
        d.mkdir(parents=True, exist_ok=True)
        (d / "vulners.nse").write_text("-- mock\n", encoding="utf-8")
        log("[t] nmap-vulners mock")
        return {"nmap_vulners": str(d), "nse_count": 1}

    def fake_vulscan(root, log):
        d = root / "vulscan"
        d.mkdir(parents=True, exist_ok=True)
        (d / "vulscan.nse").write_text("-- mock\n", encoding="utf-8")
        for name in npaths.EXPECTED_VULSCAN_TABLES:
            (d / name).write_text("1;mock\n", encoding="utf-8")
        log("[t] vulscan mock")
        return {
            "vulscan_dir": str(d),
            "vulscan_script": True,
            "files_ok": list(npaths.EXPECTED_VULSCAN_TABLES),
            "files_failed": [],
        }

    def fake_edb(root, log):
        d = root / "exploitdb"
        d.mkdir(parents=True, exist_ok=True)
        (d / "files_exploits.csv").write_text(
            "id,file,description\n1,e/1.txt,Test Exploit\n", encoding="utf-8"
        )
        (d / "READY").write_text('{"entries":1}\n', encoding="utf-8")
        (root / "vulscan").mkdir(exist_ok=True)
        (root / "vulscan" / "exploitdb.csv").write_text("1;Test Exploit\n", encoding="utf-8")
        log("[t] exploitdb mock")
        return {"exploitdb_entries": 1, "exploitdb_bytes": 40}

    monkeypatch.setattr(vu, "download_nmap_vulners", fake_vulners)
    monkeypatch.setattr(vu, "download_vulscan", fake_vulscan)
    monkeypatch.setattr(vu, "download_exploitdb", fake_edb)
    monkeypatch.setattr(vu, "try_acquire_lock", lambda *a, **k: True)
    monkeypatch.setattr(vu, "release_lock", lambda *a, **k: None)
    monkeypatch.setattr(vu, "touch_worker_heartbeat", lambda *a, **k: None)

    class FakeSession:
        def get(self, *a, **k):
            return None

    result = vu.run_vuln_db_update(
        FakeSession(), job_id=None, include_vulscan=True, include_exploitdb=True
    )
    assert result["status"] == "success"
    assert (tmp_path / "READY").is_file()
    st = npaths.vuln_pack_status(tmp_path)
    assert st["ready"] is True
    assert st["exploitdb"]["present"] is True


def test_convert_exploitdb_csv_to_vulscan():
    from app.services.nmap.vuln_update import convert_exploitdb_csv_to_vulscan

    raw = (
        b"id,file,description,date_published\n"
        b'42,exploits/linux/42.txt,"Foo; Bar XSS",2020-01-01\n'
        b"99,exploits/php/99.txt,Simple Title,2021-01-01\n"
    )
    lines, n = convert_exploitdb_csv_to_vulscan(raw)
    assert n == 2
    assert lines[0].startswith("42;")
    assert ";" not in lines[0].split(";", 1)[1]  # semicolons stripped from title
    assert lines[1] == "99;Simple Title"


def test_merge_job_details_appends_log_lines():
    from app.services.nmap.job_progress import merge_job_details
    from app.models import Job

    calls = []

    class FakeJob:
        def __init__(self):
            self.status = "pending"
            self.started_at = None
            self.finished_at = None
            self.details = None

    job = FakeJob()

    class FakeSession:
        def get(self, model, jid):
            return job

        def add(self, obj):
            calls.append(obj)

        def commit(self):
            pass

    merge_job_details(
        FakeSession(),
        1,
        status="running",
        current="scanning",
        log_line="line1",
    )
    merge_job_details(
        FakeSession(),
        1,
        status="running",
        log_line="line2",
    )
    data = json.loads(job.details)
    assert data["log_lines"] == ["line1", "line2"]
    assert job.status == "running"
    assert job.started_at is not None


def test_resolve_use_syn_downgrades_without_privileges():
    from app.services.nmap import privileges as priv

    with patch.object(priv.os, "geteuid", return_value=1000):
        use, note = priv.resolve_use_syn(True)
        assert use is False
        assert note and "root" in note.lower()
        use2, note2 = priv.resolve_use_syn(False)
        assert use2 is False
        assert note2 is None
    with patch.object(priv.os, "geteuid", return_value=0):
        use3, note3 = priv.resolve_use_syn(True)
        assert use3 is True
        assert note3 is None
    assert priv.is_root_required_error(
        "You requested a scan type which requires root privileges.\nQUITTING!\n"
    )
    assert not priv.is_root_required_error("all hosts up")


def test_device_identity_key_prefers_mac():
    assert up.device_identity_key(mac="aa:bb:cc:dd:ee:ff", ip="1.2.3.4") == "mac:AA:BB:CC:DD:EE:FF"
    assert up.device_identity_key(mac=None, ip="1.2.3.4") == "ip:1.2.3.4"


def test_vuln_pack_status_empty_and_ready(tmp_path):
    st = npaths.vuln_pack_status(tmp_path)
    assert st["ready"] is False
    assert st["exists"] is True
    assert st["completeness"] == "empty"
    # Marker alone is not "ready" — need vulners.nse or tables
    marker = tmp_path / "READY"
    marker.write_text("ok", encoding="utf-8")
    st_marker = npaths.vuln_pack_status(tmp_path)
    assert st_marker["marker"] is True
    assert st_marker["ready"] is False
    nv = tmp_path / "nmap-vulners"
    nv.mkdir()
    (nv / "vulners.nse").write_text("-- mock\n", encoding="utf-8")
    st2 = npaths.vuln_pack_status(tmp_path)
    assert st2["ready"] is True
    assert st2["nmap_vulners"]["present"] is True
    assert st2["completeness"] == "online_vulners"


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


def test_parse_nmap_config_and_cidrs_textarea():
    from app.services.nmap import config as nmap_cfg

    assert nmap_cfg.parse_cidrs_textarea("192.168.1.0/24\n# comment\n10.0.0.0/8") == [
        "192.168.1.0/24",
        "10.0.0.0/8",
    ]
    raw = nmap_cfg.dump_nmap_config(
        cidrs=["192.168.1.0/24"],
        excludes=["192.168.1.1/32"],
        skip_dns=True,
        vuln_enabled=True,
    )
    import json

    data = json.loads(raw)
    assert data["cidrs"] == ["192.168.1.0/24"]
    assert data["vuln_enabled"] is True


def test_script_args_for_vuln_no_duplicate_vulners(tmp_path, monkeypatch):
    """Pack vulners.nse must not be added when stock vuln category already has it."""
    from app.services.nmap import scan as nscan

    monkeypatch.setenv("PIHERDER_NMAP_VULN_ROOT", str(tmp_path))
    nv = tmp_path / "nmap-vulners"
    nv.mkdir()
    (nv / "vulners.nse").write_text("-- pack copy\n", encoding="utf-8")
    (nv / "http-vulners-regex.nse").write_text("-- regex\n", encoding="utf-8")
    vs = tmp_path / "vulscan"
    vs.mkdir()
    (vs / "vulscan.nse").write_text("-- vulscan\n", encoding="utf-8")

    args = nscan._script_args_for_vuln()
    assert args[0] == "--script"
    script_list = args[1]
    assert "vuln" in script_list.split(",")
    # stock name only — never absolute pack path to vulners.nse
    assert "vulners.nse" not in script_list or "nmap-vulners/vulners" not in script_list
    assert str(vs / "vulscan.nse") in script_list
    assert "http-vulners-regex.nse" in script_list
    assert nscan._nmap_script_engine_failed(
        "NSE: failed to initialize the script engine:\nduplicate script ID: 'vulners'\nQUITTING!"
    )


def test_stale_data_cleanup_preview_and_purge_jobs(tmp_path):
    """Old terminal jobs are purged; pending are kept."""
    from datetime import datetime, timedelta
    from sqlmodel import Session, SQLModel, create_engine, select
    from app.models import Job, AuditLog, NmapScanRun
    from app.services import stale_data_cleanup as sdc

    engine = create_engine(
        f"sqlite:///{tmp_path / 'cleanup.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    old = datetime.utcnow() - timedelta(days=40)
    recent = datetime.utcnow() - timedelta(days=2)
    xml_old = tmp_path / "old-run.xml"
    xml_old.write_text("<nmaprun/>", encoding="utf-8")
    with Session(engine) as s:
        s.add(
            Job(
                job_type="backup",
                status="success",
                created_at=old,
                finished_at=old,
            )
        )
        s.add(
            Job(
                job_type="backup",
                status="pending",
                created_at=old,
            )
        )
        s.add(
            Job(
                job_type="backup",
                status="failed",
                created_at=recent,
                finished_at=recent,
            )
        )
        s.add(AuditLog(action="x", status="success", started_at=old))
        s.add(AuditLog(action="y", status="success", started_at=recent))
        s.add(
            NmapScanRun(
                integration_id=1,
                intensity="discovery",
                status="success",
                created_at=old,
                finished_at=old,
                artifact_path=str(xml_old),
            )
        )
        s.add(
            NmapScanRun(
                integration_id=1,
                intensity="inventory",
                status="success",
                created_at=recent,
                finished_at=recent,
            )
        )
        s.commit()

        # Keys match AppSetting / cleanup_config() input shape
        conf = {
            "data_cleanup_enabled": True,
            "data_cleanup_cron": "30 4 * * *",
            "data_cleanup_jobs_enabled": True,
            "data_cleanup_jobs_days": 30,
            "data_cleanup_audit_enabled": True,
            "data_cleanup_audit_days": 30,
            "data_cleanup_nmap_enabled": False,
            "data_cleanup_nmap_days": 30,
        }
        prev = sdc.preview_cleanup(s, conf)
        assert prev["jobs"] == 1
        assert prev["audit"] == 1
        assert prev["nmap_runs"] == 0  # nmap toggle off

        dry = sdc.run_stale_data_cleanup(s, job_id=None, dry_run=True, cfg=conf)
        assert dry["dry_run"] is True
        assert dry["deleted_jobs"] == 0

        res = sdc.run_stale_data_cleanup(s, job_id=None, dry_run=False, cfg=conf)
        assert res["deleted_jobs"] == 1
        assert res["deleted_audit"] == 1
        jobs = list(s.exec(select(Job)).all())
        assert len(jobs) == 2  # pending + recent failed
        audits = list(s.exec(select(AuditLog)).all())
        # one recent audit remains (+ optional cleanup audit)
        assert any(a.action == "y" for a in audits)

        conf_nmap = {
            **conf,
            "data_cleanup_nmap_enabled": True,
            "data_cleanup_jobs_enabled": False,
            "data_cleanup_audit_enabled": False,
        }
        prev_n = sdc.preview_cleanup(s, conf_nmap)
        assert prev_n["nmap_runs"] == 1
        res_n = sdc.run_stale_data_cleanup(s, job_id=None, dry_run=False, cfg=conf_nmap)
        assert res_n["deleted_nmap_runs"] == 1
        assert res_n["deleted_nmap_files"] == 1
        assert not xml_old.is_file()
        runs = list(s.exec(select(NmapScanRun)).all())
        assert len(runs) == 1
        assert runs[0].intensity == "inventory"

    # cleanup_config clamps
    conf_clamped = sdc.cleanup_config(
        {
            "data_cleanup_enabled": True,
            "data_cleanup_jobs_days": 99999,
            "data_cleanup_audit_days": 0,
            "data_cleanup_nmap_enabled": True,
        }
    )
    assert conf_clamped["jobs_days"] == sdc.MAX_DAYS
    assert conf_clamped["audit_days"] == sdc.MIN_DAYS
    assert conf_clamped["nmap_enabled"] is True


def test_schedule_options_dump_parse_and_deep_gate():
    from app.services.nmap import schedules as sch
    from types import SimpleNamespace

    raw = sch.dump_schedule_options(vuln_scripts=True, use_syn=True)
    opts = sch.parse_schedule_options(SimpleNamespace(options_json=raw))
    assert opts["vuln_scripts"] is True
    assert opts["use_syn"] is True

    raw2 = sch.dump_schedule_options(vuln_scripts=False, use_syn=False)
    opts2 = sch.parse_schedule_options(SimpleNamespace(options_json=raw2))
    assert opts2["vuln_scripts"] is False
    assert opts2["use_syn"] is False

    # inherit SYN when omitted
    raw3 = sch.dump_schedule_options(vuln_scripts=True, use_syn=None)
    opts3 = sch.parse_schedule_options(SimpleNamespace(options_json=raw3))
    assert opts3["use_syn"] is None
    assert "deep" in sch.INTENSITIES_SCHEDULE

    # malformed / empty options_json
    assert sch.parse_schedule_options(SimpleNamespace(options_json="{not-json"))["vuln_scripts"] is False
    assert sch.parse_schedule_options(None)["use_syn"] is None
    assert sch.parse_schedule_options({"vuln_scripts": 1, "use_syn": 0})["use_syn"] is False

    # form helper for schedule edit
    assert sch.parse_use_syn_form("syn") == (True, False)
    assert sch.parse_use_syn_form("connect") == (False, False)
    assert sch.parse_use_syn_form("") == (None, True)
    assert sch.parse_use_syn_form(None) == (None, True)
    assert sch.schedule_aps_id(42) == "nmap_scan_42"


def test_create_update_delete_schedule_sqlite(tmp_path):
    """CRUD schedules on SQLite — covers edit path options_json."""
    from datetime import datetime
    from sqlmodel import Session, SQLModel, create_engine, select
    from app.models import Integration, NmapScanSchedule
    from app.services.nmap import schedules as sch

    engine = create_engine(
        f"sqlite:///{tmp_path / 'sched.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        integ = Integration(
            type="nmap",
            name="LAN",
            base_url="local",
            enabled=True,
            config_json='{"cidrs":["192.168.1.0/24"]}',
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        s.add(integ)
        s.commit()
        s.refresh(integ)

        row = sch.create_schedule(
            s,
            integration_id=integ.id,
            name="nightly-discovery",
            intensity="discovery",
            cron="0 2 * * *",
            enabled=False,
            vuln_scripts=True,  # ignored unless deep
            use_syn=True,
        )
        assert row.id
        assert row.intensity == "discovery"
        assert row.cron == "0 2 * * *"
        opts = sch.parse_schedule_options(row)
        assert opts["vuln_scripts"] is False  # gated off non-deep
        assert opts["use_syn"] is True

        with pytest.raises(ValueError, match="cron"):
            sch.create_schedule(
                s,
                integration_id=integ.id,
                name="bad",
                intensity="inventory",
                cron="not five",
            )

        with pytest.raises(ValueError, match="interval_hours|cron"):
            sch.create_schedule(
                s,
                integration_id=integ.id,
                name="empty",
                intensity="inventory",
            )

        deep = sch.create_schedule(
            s,
            integration_id=integ.id,
            name="deep-weekly",
            intensity="deep",
            interval_hours=168,
            enabled=True,
            vuln_scripts=True,
            use_syn=None,
        )
        dopts = sch.parse_schedule_options(deep)
        assert dopts["vuln_scripts"] is True
        assert dopts["use_syn"] is None

        updated = sch.update_schedule(
            s,
            deep,
            name="deep-weekly-renamed",
            intensity="deep",
            cron="0 4 * * 0",
            clear_interval=True,
            vuln_scripts=False,
            use_syn=False,
            enabled=False,
        )
        assert updated.name == "deep-weekly-renamed"
        assert updated.cron == "0 4 * * 0"
        assert updated.interval_hours is None
        assert updated.enabled is False
        uopts = sch.parse_schedule_options(updated)
        assert uopts["vuln_scripts"] is False
        assert uopts["use_syn"] is False

        # demote deep→inventory clears vuln
        demoted = sch.update_schedule(s, updated, intensity="inventory", vuln_scripts=True)
        assert sch.parse_schedule_options(demoted)["vuln_scripts"] is False

        sid = demoted.id
        sch.delete_schedule(s, demoted)
        assert s.get(NmapScanSchedule, sid) is None
        remaining = list(s.exec(select(NmapScanSchedule)).all())
        assert len(remaining) == 1
        assert remaining[0].name == "nightly-discovery"


def test_build_nmap_argv_detailed_udp_and_connect():
    detailed = av.build_nmap_argv(
        "detailed", ["192.168.1.0/24"], output_xml="/tmp/d.xml", use_syn=False
    )
    assert "-sT" in detailed
    assert "-p-" in detailed
    assert "-sV" in detailed

    with_udp = av.build_nmap_argv(
        "inventory",
        ["10.0.0.1"],
        output_xml="/tmp/u.xml",
        include_udp=True,
        use_syn=False,
    )
    assert "-sU" in with_udp
    # discovery never adds UDP
    disc_udp = av.build_nmap_argv(
        "discovery",
        ["10.0.0.0/24"],
        output_xml="/tmp/x.xml",
        include_udp=True,
    )
    assert "-sU" not in disc_udp

    unknown = av.build_nmap_argv("nope", ["1.2.3.4"], output_xml="/tmp/x.xml")
    assert "-sn" in unknown  # falls back to discovery


def test_network_view_groups_by_subnet():
    from app.services.nmap import config as nmap_cfg

    class FakeDev:
        def __init__(self, ip, state="new"):
            self.id = hash(ip) % 10000
            self.ip_address = ip
            self.hostname = None
            self.mac_address = None
            self.state = state
            self.linked_server_id = None
            self.os_summary = None
            self.ports_json = '[{"port":22,"protocol":"tcp","state":"open"}]'

    integ = SimpleNamespace(id=1, config_json='{"cidrs":["192.168.1.0/24"]}')
    session = MagicMock()
    with patch.object(
        nmap_cfg,
        "list_devices",
        return_value=[
            FakeDev("192.168.1.10"),
            FakeDev("192.168.1.20"),
            FakeDev("10.0.0.5"),
            FakeDev("192.168.1.99", state="ignored"),
        ],
    ):
        payload = nmap_cfg.network_view_payload(session, integ)
    assert payload["device_count"] == 3
    subnets = {g["subnet"] for g in payload["groups"]}
    assert "192.168.1.0/24" in subnets
    assert "10.0.0.0/24" in subnets


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
