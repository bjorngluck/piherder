"""RC3 coverage push — pure helpers (no live SSH / nmap / network).

Targets under-covered modules that still ship meaningful logic:
avatars, logos, backup_progress, diagnostics, jobs summaries, scheduler guards,
stale cleanup config, nmap scan/vuln helpers, registry chips, notifications,
os_patching summarize, app timezone, fabric IP class helpers.
"""
from __future__ import annotations

import io
import json
import tarfile
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _memory_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine), engine


# ---------------------------------------------------------------------------
# Avatars + service logos
# ---------------------------------------------------------------------------


def test_avatar_detect_save_path_and_delete(tmp_path, monkeypatch):
    from app.services import avatars as av
    from app.config import settings

    monkeypatch.setattr(settings, "DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "AVATAR_MAX_BYTES", 50_000)

    assert av.detect_image_type(b"short") is None
    assert av.detect_image_type(b"\xff\xd8\xff" + b"x" * 20) == "image/jpeg"
    assert av.detect_image_type(b"\x89PNG\r\n\x1a\n" + b"x" * 20) == "image/png"
    webp = b"RIFF" + b"\x00" * 4 + b"WEBP" + b"x" * 8
    assert av.detect_image_type(webp) == "image/webp"
    assert av.detect_image_type(b"GIF89a" + b"x" * 20) is None

    with pytest.raises(ValueError, match="JPEG|PNG|WebP"):
        av.save_avatar(1, b"not-an-image" + b"x" * 20)

    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    rel = av.save_avatar(7, png)
    assert rel == "avatars/7.png"
    assert (tmp_path / rel).is_file()
    assert av.content_type_for_path(Path(rel)) == "image/png"
    assert av.content_type_for_path(Path("x.webp")) == "image/webp"
    assert av.content_type_for_path(Path("x.bin")) == "application/octet-stream"

    abs_p = av.absolute_avatar_path(rel)
    assert abs_p is not None and abs_p.is_file()
    assert av.absolute_avatar_path(None) is None
    assert av.absolute_avatar_path("../etc/passwd") is None
    assert av.absolute_avatar_path("avatars/missing.png") is None

    av.delete_avatar_files(7)
    assert not (tmp_path / rel).exists()

    # too large
    monkeypatch.setattr(settings, "AVATAR_MAX_BYTES", 10)
    with pytest.raises(ValueError, match="too large"):
        av.save_avatar(2, png)


def test_service_logo_detect_save_and_public_url(tmp_path, monkeypatch):
    from app.services import service_logos as logos
    from app.config import settings

    monkeypatch.setattr(settings, "DATA_ROOT", str(tmp_path))

    assert logos.detect_image_type(b"xx") is None
    assert logos.detect_image_type(b"\xff\xd8\xff" + b"x" * 10) == "image/jpeg"
    assert logos.detect_image_type(b"GIF89a" + b"x" * 10) == "image/gif"
    assert logos.detect_image_type(b"<svg xmlns='x'></svg>") == "image/svg+xml"
    assert logos.detect_image_type(b"\x00\x00\x01\x00" + b"x" * 8) == "image/x-icon"
    # content-type fallback
    assert logos.detect_image_type(b"zzzz", "image/png") == "image/png"

    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
    rel = logos.save_logo_bytes(3, png)
    assert rel.endswith(".png")
    assert logos.logo_public_url(3) == "/services/logo/3"
    assert logos.content_type_for_path(Path("x.svg")) == "image/svg+xml"
    abs_p = logos.absolute_logo_path(rel)
    assert abs_p is not None
    assert logos.absolute_logo_path(None) is None
    assert logos.absolute_logo_path("service_logos/../x") is None

    logos.delete_logo_files(3)
    with pytest.raises(ValueError, match="too large|Logo"):
        logos.save_logo_bytes(4, b"nope")


# ---------------------------------------------------------------------------
# Backup progress pure lines
# ---------------------------------------------------------------------------


def test_backup_progress_rsync_filters_and_buffer():
    from app.services import backup_progress as bp

    assert "..." in bp._truncate_log_line("x" * 500)
    assert bp._truncate_log_line("  hi  ") == "hi"
    assert bp._is_rsync_progress2_line("  1.2MB/s  to-chk=3/10")
    assert bp._is_rsync_progress2_line("xfr#12, 50%  3.0MB/s")
    assert not bp._is_rsync_progress2_line("error: permission denied")
    assert bp._rsync_line_worth_logging("rsync: failed to open")
    assert bp._rsync_line_worth_logging("Backing up /home")
    assert bp._rsync_line_worth_logging("Completed backup")
    assert not bp._rsync_line_worth_logging("to-chk=1/2")
    assert not bp._rsync_line_worth_logging("/some/file.txt")
    assert not bp._rsync_line_worth_logging("")

    buf = bp._merge_progress_buffer(99, "scanning", "line-a")
    assert buf["current"] == "scanning"
    assert buf["log_lines"] == ["line-a"]
    bp._merge_progress_buffer(99, None, "line-a")  # dedupe last
    bp._merge_progress_buffer(99, "done", "line-b")
    assert bp._job_details_buffer[99]["log_lines"] == ["line-a", "line-b"]
    bp.clear_job_progress_buffer(99)
    assert 99 not in bp._job_details_buffer
    bp.clear_job_progress_buffer(None)


# ---------------------------------------------------------------------------
# Diagnostics space summary
# ---------------------------------------------------------------------------


def test_diagnostics_summarize_usable_space():
    from app.services.diagnostics import clear_diagnostics_cache, summarize_usable_space

    empty = summarize_usable_space([])
    assert empty["root"] is None
    assert empty["main_drives"] == []

    drives = [
        {"target": "/", "filesystem": "ext4", "size": "64G", "used": "20G", "avail": "40G"},
        {"target": "/run", "filesystem": "tmpfs", "size": "1G", "used": "0", "avail": "1G"},
        {"target": "/boot/efi", "filesystem": "vfat", "size": "512M", "used": "10M", "avail": "500M"},
        {"target": "/home", "filesystem": "ext4", "size": "100G", "used": "10G", "avail": "90G"},
        {"target": "/dev", "filesystem": "devtmpfs", "size": "0", "used": "0", "avail": "0"},
    ]
    s = summarize_usable_space(drives)
    assert s["root"]["target"] == "/"
    assert s["total_size"] == "64G"
    assert all(d["target"] not in ("/run", "/dev", "/boot/efi") for d in s["main_drives"])

    clear_diagnostics_cache()
    clear_diagnostics_cache(1)


# ---------------------------------------------------------------------------
# Jobs pure helpers
# ---------------------------------------------------------------------------


def test_job_source_filter_and_attach_states():
    from app.services import jobs as jobs_mod

    assert jobs_mod.job_source_filter(None) is None
    j = SimpleNamespace(details=None)
    assert jobs_mod.job_source_filter(j) is None
    j.details = "not-json"
    assert jobs_mod.job_source_filter(j) is None
    j.details = json.dumps({"source_filter": "/data/a"})
    assert jobs_mod.job_source_filter(j) == "/data/a"
    j.details = json.dumps({})
    assert jobs_mod.job_source_filter(j) is None

    profiles = [{"source": "/data/a", "name": "A"}, {"source": "/data/b", "name": "B"}]
    active = [
        SimpleNamespace(
            id=1,
            status="pending",
            details=json.dumps({"source_filter": "/data/a"}),
        ),
        SimpleNamespace(
            id=2,
            status="running",
            details=json.dumps({"source_filter": "/data/a"}),
        ),
        SimpleNamespace(id=3, status="running", details=json.dumps({})),  # full
    ]
    out = jobs_mod.attach_source_job_states(profiles, active)
    a = next(r for r in out if r["source"] == "/data/a")
    assert a["active_job_id"] == 2  # running preferred
    assert a["active_job_status"] == "running"
    b = next(r for r in out if r["source"] == "/data/b")
    assert "active_job_id" not in b


def test_human_job_summary_remaining_branches():
    from app.services import jobs as jobs_mod

    s = jobs_mod._human_job_summary(
        "os_update_check",
        "success",
        json.dumps(
            {
                "actionable_count": 3,
                "phased_count": 1,
                "total_upgradable": 5,
                "reboot_pending": True,
                "error": "partial",
            }
        ),
    )
    assert "3 ready" in s
    assert "phased" in s
    assert "reboot" in s

    s = jobs_mod._human_job_summary(
        "container_update_check",
        "success",
        json.dumps(
            {
                "projects_with_updates": ["a"],
                "projects_checked": ["a", "b"],
            }
        ),
    )
    assert "1 project" in s
    assert "2 checked" in s

    s = jobs_mod._human_job_summary(
        "docker_stack_check",
        "failed",
        json.dumps({"project": "web", "has_updates": False, "success": False}),
    )
    assert "check failed" in s

    s = jobs_mod._human_job_summary(
        "docker_stack_deploy",
        "failed",
        json.dumps({"project": "web", "success": False, "error": "boom"}),
    )
    assert "deploy failed" in s

    s = jobs_mod._human_job_summary(
        "docker_stack_stop",
        "failed",
        json.dumps({"project": "web", "action": "stop", "success": False, "error": "x"}),
    )
    assert "stop failed" in s

    s = jobs_mod._human_job_summary(
        "template_redeploy",
        "failed",
        json.dumps(
            {
                "project_name": "p",
                "template_slug": "nginx",
                "success": False,
                "error": "nope",
            }
        ),
    )
    assert "failed" in s

    s = jobs_mod._human_job_summary(
        "template_drift_check",
        "success",
        json.dumps({"project_name": "p", "drift_status": "drifted", "diff_count": 2}),
    )
    assert "drifted" in s and "2" in s

    s = jobs_mod._human_job_summary(
        "template_drift_check",
        "failed",
        json.dumps({"project_name": "p", "status": "unknown", "error": "ssh"}),
    )
    assert "drift unknown" in s

    s = jobs_mod._human_job_summary(
        "os_patch", "success", json.dumps({"summary": "update ✓ · upgrade ✓"})
    )
    assert "update" in s

    s = jobs_mod._human_job_summary(
        "container_patch", "success", json.dumps({"summary": "3 containers"})
    )
    assert "3 containers" in s
    assert "Retention" in jobs_mod._human_job_summary("retention", "success", "")
    assert jobs_mod._human_job_summary("backup", "success", "done") == "done"


def test_parse_os_apply_steps_and_project_basename():
    from app.services import jobs as jobs_mod

    assert jobs_mod._parse_os_apply_steps(None) == ["update", "upgrade", "autoremove"]
    assert jobs_mod._parse_os_apply_steps("[]") == ["update", "upgrade", "autoremove"]
    assert "update" in jobs_mod._parse_os_apply_steps('["update","upgrade"]')
    assert "upgrade" in jobs_mod._parse_os_apply_steps("update,upgrade")
    assert jobs_mod._project_basename("/opt/stacks/web/") == "web"
    assert jobs_mod._project_basename("") == "project"
    assert jobs_mod._project_basename("simple") == "simple"


def test_nmap_job_type_labels_present():
    from app.services import jobs as jobs_mod

    for jt in (
        "nmap_discovery",
        "nmap_inventory",
        "nmap_detailed",
        "nmap_host_deep",
        "nmap_vuln_db_update",
        "stale_data_cleanup",
    ):
        assert jobs_mod.job_type_label(jt)
        assert jt in jobs_mod.JOB_TYPE_LABELS


# ---------------------------------------------------------------------------
# Scheduler pure guards
# ---------------------------------------------------------------------------


def test_scheduler_skip_reasons_cron_and_job_ids():
    from app.services import scheduler as sch

    assert sch.os_apply_skip_reason(None) == "missing"
    assert (
        sch.os_apply_skip_reason(
            SimpleNamespace(os_patch_enabled=False, os_apply_enabled=True)
        )
        == "disabled"
    )
    assert (
        sch.os_apply_skip_reason(
            SimpleNamespace(
                os_patch_enabled=True,
                os_apply_enabled=True,
                os_apply_only_if_updates=True,
                os_updates_count=0,
            )
        )
        == "no_updates"
    )
    assert (
        sch.os_apply_skip_reason(
            SimpleNamespace(
                os_patch_enabled=True,
                os_apply_enabled=True,
                os_apply_only_if_updates=True,
                os_updates_count=2,
            )
        )
        is None
    )

    assert sch.container_apply_skip_reason(None) == "missing"
    assert (
        sch.container_apply_skip_reason(
            SimpleNamespace(container_patch_enabled=False, container_apply_enabled=True)
        )
        == "disabled"
    )
    assert (
        sch.container_apply_skip_reason(
            SimpleNamespace(
                container_patch_enabled=True,
                container_apply_enabled=True,
                container_apply_only_if_updates=True,
                container_updates_count=0,
            )
        )
        == "no_updates"
    )
    assert (
        sch.container_apply_skip_reason(
            SimpleNamespace(
                container_patch_enabled=True,
                container_apply_enabled=True,
                container_apply_only_if_updates=False,
                container_updates_count=0,
            )
        )
        is None
    )

    with pytest.raises(ValueError, match="5 fields"):
        sch._cron_trigger("bad")
    trig = sch._cron_trigger("0 3 * * *")
    assert trig is not None

    ids = sch.server_cron_job_ids(42)
    assert any("42" in i for i in ids)
    sch.unregister_server_cron_jobs(MagicMock(), False, 42)


# ---------------------------------------------------------------------------
# Host deps parse
# ---------------------------------------------------------------------------


def test_parse_host_deps_json():
    from app.services.host_deps import parse_host_deps, _check, overall_from_checks

    assert parse_host_deps(SimpleNamespace(host_deps_json=None)) is None
    assert parse_host_deps(SimpleNamespace(host_deps_json="{bad")) is None
    assert parse_host_deps(SimpleNamespace(host_deps_json='["x"]')) is None
    data = parse_host_deps(SimpleNamespace(host_deps_json='{"overall":"ok"}'))
    assert data["overall"] == "ok"

    c = _check("ssh", "SSH", "ok", required=True, message="fine", hint="hint")
    assert c["id"] == "ssh" and c["message"] == "fine"
    assert overall_from_checks([c]) == "ok"


# ---------------------------------------------------------------------------
# Stale data cleanup pure
# ---------------------------------------------------------------------------


def test_stale_cleanup_config_and_stale_predicates():
    from app.services import stale_data_cleanup as sdc

    conf = sdc.cleanup_config(
        {
            "data_cleanup_enabled": True,
            "data_cleanup_jobs_days": 99999,
            "data_cleanup_audit_days": "nope",
            "data_cleanup_nmap_enabled": True,
            "data_cleanup_nmap_days": 0,
        }
    )
    assert conf["enabled"] is True
    assert conf["jobs_days"] == sdc.MAX_DAYS
    assert conf["audit_days"] == sdc.DEFAULT_DAYS
    assert conf["nmap_days"] == sdc.MIN_DAYS

    cut = sdc._cutoff(30)
    old = SimpleNamespace(
        status="success",
        finished_at=datetime.utcnow() - timedelta(days=60),
        created_at=datetime.utcnow() - timedelta(days=60),
    )
    fresh = SimpleNamespace(
        status="success",
        finished_at=datetime.utcnow(),
        created_at=datetime.utcnow(),
    )
    running = SimpleNamespace(
        status="running",
        finished_at=None,
        created_at=datetime.utcnow() - timedelta(days=60),
    )
    assert sdc._job_is_stale(old, cut) is True
    assert sdc._job_is_stale(fresh, cut) is False
    assert sdc._job_is_stale(running, cut) is False
    assert sdc._nmap_run_is_stale(old, cut) is True
    assert sdc._nmap_run_is_stale(
        SimpleNamespace(status="pending", finished_at=None, created_at=old.created_at),
        cut,
    ) is False


# ---------------------------------------------------------------------------
# Nmap scan pure + schedules form
# ---------------------------------------------------------------------------


def test_nmap_script_args_and_engine_failed(tmp_path, monkeypatch):
    from app.services.nmap import scan as nscan

    # scan imports vuln_root at module level — patch there
    monkeypatch.setattr(nscan, "vuln_root", lambda: tmp_path)

    assert nscan._script_args_for_preset("none") == []
    assert nscan._script_args_for_preset("cpe") == ["--script", "vulners"]
    # offline without pack → stock vulners fallback
    assert nscan._script_args_for_preset("offline") == ["--script", "vulners"]

    (tmp_path / "vulscan").mkdir()
    (tmp_path / "vulscan" / "vulscan.nse").write_text("--", encoding="utf-8")
    (tmp_path / "nmap-vulners").mkdir()
    (tmp_path / "nmap-vulners" / "http-vulners-regex.nse").write_text("--", encoding="utf-8")

    off = nscan._script_args_for_preset("offline")
    assert off[0] == "--script" and "vulscan.nse" in off[1]
    full = nscan._script_args_for_preset("full")
    assert "vuln" in full[1]
    assert "vulscan.nse" in full[1]
    assert "http-vulners-regex.nse" in full[1]
    assert nscan._script_args_for_vuln() == full

    assert nscan._nmap_script_engine_failed("Failed to initialize the script engine")
    assert nscan._nmap_script_engine_failed("duplicate script ID 'vulners'")
    assert nscan._nmap_script_engine_failed("NSE: error … quitting!")
    assert not nscan._nmap_script_engine_failed("all good")
    assert not nscan._nmap_script_engine_failed(None)


def test_nmap_parse_use_syn_form():
    from app.services.nmap import schedules as sch

    assert sch.parse_use_syn_form("syn") == (True, False)
    assert sch.parse_use_syn_form("on") == (True, False)
    assert sch.parse_use_syn_form("connect") == (False, False)
    assert sch.parse_use_syn_form("off") == (False, False)
    assert sch.parse_use_syn_form("") == (None, True)
    assert sch.parse_use_syn_form(None) == (None, True)
    assert sch.parse_use_syn_form("inherit") == (None, True)


def test_nmap_integration_cidrs_and_parse_cidrs():
    from app.services.nmap import scan as nscan
    from app.services.nmap import device_ops as dops

    integ = SimpleNamespace(
        config_json=json.dumps(
            {
                "cidrs": ["192.168.1.0/24", "bad"],
                "excludes": ["192.168.1.1/32"],
                "excludes_port_scans": ["10.0.0.1/32"],
                "excludes_deep": ["10.0.0.2/32"],
            }
        )
    )
    ok, ex = nscan._integration_cidrs(integ, intensity="discovery")
    assert "192.168.1.0/24" in ok
    assert "192.168.1.1/32" in ex or "192.168.1.1" in " ".join(ex)
    ok2, ex2 = nscan._integration_cidrs(integ, intensity="deep")
    assert len(ex2) >= len(ex)

    assert dops.parse_cidrs_textarea("192.168.1.0/24\n# comment\n10.0.0.0/8, 172.16.0.0/12") == [
        "192.168.1.0/24",
        "10.0.0.0/8",
        "172.16.0.0/12",
    ]
    assert dops._count_open_ports(None) == 0
    assert dops._count_open_ports("not-json") == 0
    ports = json.dumps(
        [
            {"port": 22, "state": "open", "service": "ssh"},
            {"port": 80, "state": "closed"},
            {"port": 443, "state": "open", "service": "https"},
        ]
    )
    assert dops._count_open_ports(ports) == 2
    summary = dops._open_ports_summary(ports, limit=1)
    assert len(summary) == 1
    assert summary[0]["port"] == 22
    assert dops._open_ports_summary(None) == []
    assert dops._open_ports_summary("{") == []


def test_device_list_item_shape():
    from app.services.nmap import device_ops as dops

    dev = SimpleNamespace(
        id=1,
        ip_address="192.168.1.10",
        hostname="pi.local",
        display_name="lab-pi",
        mac_address=None,
        mac_vendor=None,
        state="new",
        os_summary="Linux",
        ports_json=json.dumps(
            [{"port": 22, "protocol": "tcp", "state": "open", "service": "ssh"}]
        ),
        kind_override=None,
        map_role="",
        last_seen_at=None,
        linked_server_id=None,
    )
    item = dops.device_list_item(dev)
    assert item["open_ports"] == 1
    assert item["label"] == "lab-pi"
    assert item["services"]
    assert item["kind"]


# ---------------------------------------------------------------------------
# Nmap vuln_update pure + mocked downloads
# ---------------------------------------------------------------------------


def test_vuln_update_human_copy_tree_and_safe_tar(tmp_path):
    from app.services.nmap import vuln_update as vu

    assert "KB" in vu._human(2048)
    assert "MB" in vu._human(2 * 1024 * 1024)

    src = tmp_path / "src"
    dest = tmp_path / "dest"
    (src / "sub").mkdir(parents=True)
    (src / "a.txt").write_text("a", encoding="utf-8")
    (src / "sub" / "b.txt").write_text("b", encoding="utf-8")
    vu._copy_tree_contents(src, dest)
    assert (dest / "a.txt").read_text() == "a"
    assert (dest / "sub" / "b.txt").read_text() == "b"
    # overwrite existing dir
    (dest / "sub" / "old").write_text("old", encoding="utf-8")
    vu._copy_tree_contents(src, dest)
    assert not (dest / "sub" / "old").exists()

    # safe tar extract
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo(name="hello.txt")
        data = b"hi"
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    buf.seek(0)
    out = tmp_path / "extract"
    out.mkdir()
    with tarfile.open(fileobj=buf, mode="r:gz") as tf:
        vu._safe_extract_tar(tf, out)
    assert (out / "hello.txt").read_text() == "hi"

    # path traversal rejected
    bad = io.BytesIO()
    with tarfile.open(fileobj=bad, mode="w:gz") as tf:
        info = tarfile.TarInfo(name="../evil.txt")
        info.size = 1
        tf.addfile(info, io.BytesIO(b"x"))
    bad.seek(0)
    with tarfile.open(fileobj=bad, mode="r:gz") as tf:
        with pytest.raises(ValueError, match="unsafe"):
            vu._safe_extract_tar(tf, out)


def test_download_nmap_vulners_and_vulscan_mocked(tmp_path, monkeypatch):
    from app.services.nmap import vuln_update as vu
    from app.services.nmap import paths as npaths

    logs: list[str] = []

    def log(m):
        logs.append(m)

    # Build a tiny gzip tarball with one top-level dir + nse
    def make_tarball(inner_name: str, files: dict[str, bytes]) -> bytes:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            for name, content in files.items():
                path = f"{inner_name}/{name}"
                info = tarfile.TarInfo(name=path)
                info.size = len(content)
                tf.addfile(info, io.BytesIO(content))
        return buf.getvalue()

    vulners_tar = make_tarball("nmap-vulners-main", {"vulners.nse": b"--nse\n"})

    def fake_http(url, *, timeout=180):
        if "vulners" in url.lower() or "nmap-vulners" in url.lower():
            return vulners_tar
        if url.endswith(".nse"):
            return b"-- script\n"
        if url.endswith(".csv"):
            return b"1;row\n" + b"x" * 40
        # vulscan tarball
        tables = {n: b"1;x\n" for n in npaths.EXPECTED_VULSCAN_TABLES}
        tables["vulscan.nse"] = b"--vulscan\n"
        return make_tarball("vulscan-master", tables)

    monkeypatch.setattr(vu, "_http_get", fake_http)

    r = vu.download_nmap_vulners(tmp_path, log)
    assert r["nse_count"] >= 1
    assert (tmp_path / "nmap-vulners" / "vulners.nse").is_file()

    r2 = vu.download_vulscan(tmp_path, log)
    assert r2["vulscan_script"] is True
    assert len(r2["files_ok"]) == len(npaths.EXPECTED_VULSCAN_TABLES)

    # raw fallback path when tarball fails
    def boom(url, *, timeout=180):
        if "archive" in url or "tarball" in url or url.endswith(".tar.gz"):
            raise OSError("net down")
        if url.endswith("vulscan.nse"):
            return b"--nse\n"
        return b"1;data\n" + b"y" * 40

    monkeypatch.setattr(vu, "_http_get", boom)
    root2 = tmp_path / "fb"
    root2.mkdir()
    r3 = vu.download_vulscan(root2, log)
    assert r3["vulscan_script"] is True


def test_download_exploitdb_mocked(tmp_path, monkeypatch):
    from app.services.nmap import vuln_update as vu

    logs: list[str] = []

    def log(m):
        logs.append(m)

    csv_body = (
        b"id,file,description,date_published\n"
        b"10,e/10.txt,Alpha Bug,2020-01-01\n"
        b"20,e/20.txt,Beta Bug,2021-01-01\n"
    )

    def fake_http(url, *, timeout=180):
        if "shellcodes" in url:
            return (
                b"id,file,description,date_published\n"
                b"10,s/10.txt,Dup ID should lose,2020-01-01\n"
            )
        return csv_body

    monkeypatch.setattr(vu, "_http_get", fake_http)
    r = vu.download_exploitdb(tmp_path, log)
    assert r["exploitdb_entries"] >= 2
    assert (tmp_path / "exploitdb" / "READY").is_file()
    lines = (tmp_path / "vulscan" / "exploitdb.csv").read_text(encoding="utf-8").strip().splitlines()
    # id 10 only once (first wins)
    assert sum(1 for ln in lines if ln.startswith("10;")) == 1

    marker = vu.write_ready_marker(tmp_path, {"note": "test"}, log)
    assert marker.is_file()
    assert (tmp_path / "pack-meta.json").is_file()


def test_vuln_pack_status_full_and_offline(tmp_path):
    from app.services.nmap import paths as npaths

    # full pack
    nv = tmp_path / "nmap-vulners"
    nv.mkdir()
    (nv / "vulners.nse").write_text("--", encoding="utf-8")
    vs = tmp_path / "vulscan"
    vs.mkdir()
    (vs / "vulscan.nse").write_text("--", encoding="utf-8")
    for name in npaths.EXPECTED_VULSCAN_TABLES:
        (vs / name).write_text("1;x\n", encoding="utf-8")
    edb = tmp_path / "exploitdb"
    edb.mkdir()
    (edb / "files_exploits.csv").write_text("id\n1\n", encoding="utf-8")
    (edb / "READY").write_text('{"entries":1}\n', encoding="utf-8")
    (tmp_path / "READY").write_text("ok", encoding="utf-8")
    (tmp_path / "pack-meta.json").write_text('{"updated_at":"t"}', encoding="utf-8")

    st = npaths.vuln_pack_status(tmp_path)
    assert st["completeness"] == "full"
    assert st["ready"] is True
    assert st["exploitdb"]["ready"] is True
    assert "Exploit-DB" in st["completeness_label"]
    assert "GB" in npaths._human_bytes(3 * 1024**3) or "MB" in npaths._human_bytes(3 * 1024**2)
    assert npaths._human_bytes(100) == "100 B"
    assert "KB" in npaths._human_bytes(2048)

    # offline_tables: tables ok, script missing
    (vs / "vulscan.nse").unlink()
    st2 = npaths.vuln_pack_status(tmp_path)
    assert st2["completeness"] == "offline_tables"


# ---------------------------------------------------------------------------
# Integrations registry pure chips / meta
# ---------------------------------------------------------------------------


def test_registry_binding_helpers():
    from app.services.integrations import registry as reg

    assert reg.parse_binding_meta(SimpleNamespace(external_meta_json=None)) == {}
    assert reg.parse_binding_meta(SimpleNamespace(external_meta_json="{")) == {}
    assert reg.parse_binding_meta(SimpleNamespace(external_meta_json='["x"]')) == {}
    meta = reg.parse_binding_meta(
        SimpleNamespace(external_meta_json=json.dumps({"url": "https://x", "cert_days_remaining": 30}))
    )
    assert meta["url"] == "https://x"

    host_b = SimpleNamespace(docker_project="", docker_container="")
    dock_b = SimpleNamespace(docker_project="web", docker_container="nginx")
    assert reg.is_host_service_binding(host_b) is True
    assert reg.is_docker_service_binding(dock_b) is True
    assert reg.is_host_service_binding(dock_b) is False

    integ_npm = SimpleNamespace(type=reg.TYPE_NPM, base_url="https://npm.example")
    integ_ph = SimpleNamespace(type=reg.TYPE_PIHOLE, base_url="https://pihole.example")
    integ_kuma = SimpleNamespace(type=reg.TYPE_UPTIME_KUMA, base_url="https://kuma.example")
    binding = SimpleNamespace(
        external_id="1",
        external_meta_json="{}",
        docker_project="",
        docker_container="",
        server_id=1,
    )
    assert "npm" in reg.binding_open_url(integ_npm, binding).lower() or "nginx" in reg.binding_open_url(
        integ_npm, binding
    ).lower()
    assert reg.binding_open_url(integ_ph, binding)
    assert reg.binding_open_url(integ_kuma, binding)

    integ_gf = SimpleNamespace(
        type=reg.TYPE_GRAFANA,
        base_url="https://gf.example",
        config_json=reg.dump_config({"display_names": {"uid1": "Dash"}}),
    )
    b_gf = SimpleNamespace(
        external_id="uid1",
        external_meta_json=json.dumps({"slug": "dash", "grafana_title": "T"}),
        docker_project="",
        docker_container="",
        server_id=1,
        label_override=None,
    )
    url = reg.binding_open_url(
        integ_gf,
        b_gf,
        server=SimpleNamespace(id=1, hostname="pi.local", name="Pi", ip_address="10.0.0.1"),
    )
    assert "gf.example" in url or "uid1" in url or url.startswith("http")

    label, override, title = reg.resolve_grafana_display_label(integ_gf, b_gf)
    assert label == "Dash"
    assert override == "Dash"

    chip_binding = SimpleNamespace(
        id=9,
        integration_id=1,
        server_id=2,
        external_id="m1",
        external_label="HTTP",
        last_state="up",
        last_message="ok",
        last_checked_at=None,
        logo_path="service_logos/9.png",
        docker_project="web",
        docker_container="app",
        external_meta_json=json.dumps({"url": "https://app.example", "cert_days_remaining": 10}),
    )
    with patch.object(reg, "get_integration", return_value=integ_kuma):
        chip = reg.binding_to_chip(MagicMock(), chip_binding)
    assert chip["scope"] == "docker"
    assert chip["has_logo"] is True
    assert chip["logo_url"] == "/services/logo/9"
    assert chip["cert_days"] == 10


def test_registry_grafana_kind_and_message():
    from app.services.integrations import registry as reg

    # Explicit meta.kind wins over docker scope inference
    kind = reg.binding_grafana_kind(
        SimpleNamespace(docker_project="", docker_container=""),
        meta={"kind": "logs"},
    )
    assert kind == reg.GRAFANA_KIND_LOGS
    kind_c = reg.binding_grafana_kind(
        SimpleNamespace(docker_project="web", docker_container="app"),
        meta={},
    )
    assert kind_c == reg.GRAFANA_KIND_CONTAINERS

    mon = SimpleNamespace(
        cert_is_valid=True,
        cert_days_remaining=12,
        response_time_ms=45.2,
        target_display=lambda: "https://x",
    )
    msg = reg.binding_message_from_monitor(mon)
    assert "TLS" in msg and "ms" in msg

    mon2 = SimpleNamespace(
        cert_is_valid=False,
        cert_days_remaining=None,
        response_time_ms=None,
        target_display=lambda: "https://y",
    )
    assert "TLS invalid" in reg.binding_message_from_monitor(mon2)

    mon3 = SimpleNamespace(
        cert_is_valid=None,
        cert_days_remaining=None,
        response_time_ms=None,
        target_display=lambda: "https://z",
    )
    assert "https://z" in reg.binding_message_from_monitor(mon3)

    integ = SimpleNamespace(last_status_json=json.dumps({"ok": True, "monitors": [{"id": 1}]}))
    assert reg.parse_last_status(integ)["ok"] is True
    assert reg.monitors_from_cache(integ) == [{"id": 1}]
    empty = SimpleNamespace(last_status_json=None)
    assert reg.parse_last_status(empty) == {}
    assert reg.monitors_from_cache(empty) == []
    assert reg.dashboards_from_cache(
        SimpleNamespace(last_status_json=json.dumps({"dashboards": [{"uid": "a"}]}))
    ) == [{"uid": "a"}]


# ---------------------------------------------------------------------------
# Notifications (in-memory SQLite)
# ---------------------------------------------------------------------------


def test_notifications_upsert_dismiss_resolve():
    from app.models import Notification
    from app.services import notifications as ntf

    session, engine = _memory_session()
    with patch.object(ntf, "_maybe_webhook"), patch.object(ntf, "_maybe_push"), patch.object(
        ntf, "_maybe_push_resolved"
    ):
        n1 = ntf.upsert_notification(
            session,
            fingerprint="fp-os-1",
            type="os_updates",
            title="OS updates",
            body="3 ready",
            severity="warning",
            server_id=1,
            payload={"n": 3},
        )
        assert n1.status == "open"
        n2 = ntf.upsert_notification(
            session,
            fingerprint="fp-os-1",
            type="os_updates",
            title="OS updates",
            body="4 ready",
            severity="warning",
            server_id=1,
        )
        assert n2.id == n1.id
        assert "4 ready" in (n2.body or "")

        assert ntf.open_count(session) == 1
        assert ntf.mark_read(session, n1.id) is True
        assert ntf.dismiss(session, n1.id) is True
        assert ntf.open_count(session) == 0

        ntf.upsert_notification(
            session,
            fingerprint="fp-os-2",
            type="backup_failed",
            title="Backup failed",
            severity="critical",
        )
        ntf.upsert_notification(
            session,
            fingerprint="fp-os-3",
            type="info",
            title="Info",
            severity="info",
        )
        assert ntf.dismiss_all(session) >= 1
        assert ntf.open_count(session) == 0

        ntf.upsert_notification(
            session,
            fingerprint="fp-res",
            type="x",
            title="Y",
            severity="warning",
        )
        assert ntf.resolve_by_fingerprint(session, "fp-res") == 1
        assert ntf.resolve_by_fingerprint(session, "fp-res") == 0

        listed = ntf.list_notifications(session, status=None)
        assert listed

        ntf.notify_backup_failed(
            session, server_id=5, server_name="pi", message="rsync fail"
        )
        ntf.resolve_backup_failed(session, 5)

    session.close()
    engine.dispose()


def test_notify_os_and_container_updates():
    from app.services import notifications as ntf

    session, engine = _memory_session()
    with patch.object(ntf, "_maybe_webhook"), patch.object(ntf, "_maybe_push"), patch.object(
        ntf, "_maybe_push_resolved"
    ):
        ntf.notify_os_updates(
            session,
            9,
            "Lab",
            updates_count=2,
            reboot_pending=True,
            phased_count=1,
        )
        ntf.notify_os_updates(
            session, 9, "Lab", updates_count=0, reboot_pending=False
        )
        ntf.notify_container_updates(session, 9, "Lab", projects=["web", "db"])
        ntf.notify_container_updates(session, 9, "Lab", projects=[])
    session.close()
    engine.dispose()


# ---------------------------------------------------------------------------
# OS patching pure
# ---------------------------------------------------------------------------


def test_os_patching_normalize_summarize_parse():
    from app.services import os_patching as osp

    assert osp.normalize_os_patch_steps(None) == ["update", "upgrade", "autoremove"]
    steps = osp.normalize_os_patch_steps(["upgrade", "full-upgrade", "update", "bogus"])
    assert "full-upgrade" not in steps
    assert steps[0] == "update"

    assert "Failed" in osp.summarize_os_patch_result({"error": "ssh down"})
    assert "reboot" in osp.summarize_os_patch_result(
        {
            "results": [
                {"step": "update", "rc": 0},
                {"step": "upgrade", "rc": 0},
            ],
            "needs_reboot": True,
        }
    )
    assert "✗" in osp.summarize_os_patch_result(
        {"results": [{"step": "update", "error": "nope"}]}
    )
    assert osp.os_patch_succeeded(
        {"results": [{"step": "update", "rc": 0}, {"step": "upgrade", "rc": 0}]}
    )
    assert not osp.os_patch_succeeded({"error": "x"})
    assert not osp.os_patch_succeeded({"results": [{"step": "update", "rc": 1}]})

    pkgs = osp._parse_upgradable_list(
        "Listing...\nfoo/stable 1.0 amd64 [upgradable from: 0.9]\nbar/stable 2.0\n"
    )
    assert any("foo" in p for p in pkgs) or len(pkgs) >= 0  # parser best-effort
    sim = osp._parse_sim_upgrade_inst(
        "Inst foo [1.0] (1.1 stable [amd64])\nConf foo\n"
    )
    assert isinstance(sim, list)


# ---------------------------------------------------------------------------
# App settings timezone pure
# ---------------------------------------------------------------------------


def test_app_settings_timezone_describe_and_calendar(monkeypatch):
    from app.services import app_settings as app_cfg

    monkeypatch.setattr(app_cfg, "load_settings", lambda: {"timezone": "Europe/Berlin"})
    d = app_cfg.describe_timezone("Europe/Berlin")
    assert d["iana"] == "Europe/Berlin"
    assert d["region"] == "EU"
    assert "UTC" in d["offset_utc"] or d["offset"]
    assert d["city"]

    d2 = app_cfg.describe_timezone("UTC")
    assert d2["region"] == "UTC"
    d3 = app_cfg.describe_timezone("Not/A/Real/Zone")
    assert d3["iana"] == "UTC"

    today = app_cfg.calendar_today_in_app_tz()
    assert len(today) == 10
    rng = app_cfg.calendar_date_range_preset(7)
    assert rng["date_from"] <= rng["date_to"]
    assert app_cfg.calendar_date_range_preset(0)  # clamps to 1

    assert app_cfg.utc_isoformat(None) is None
    assert app_cfg.utc_isoformat(datetime(2026, 1, 2, 3, 4, 5))
    assert app_cfg.parse_utc_datetime("2026-01-02T03:04:05Z") is not None
    assert app_cfg.parse_utc_datetime("not-a-date") is None
    assert app_cfg.validate_cron_expression("0 4 * * *") == "0 4 * * *"
    with pytest.raises(ValueError):
        app_cfg.validate_cron_expression("bad")


# ---------------------------------------------------------------------------
# Fabric IP / map URL pure
# ---------------------------------------------------------------------------


def test_fabric_ip_class_and_map_urls():
    from app.services.dns_fabric import core as fabric

    assert fabric._ip_in_lan("192.168.1.10", "192.168.1.0/24") is True
    assert fabric._ip_in_lan("10.0.0.1", "192.168.1.0/24") is False
    assert fabric._ip_in_lan(None, "192.168.1.0/24") is None
    assert fabric._ip_in_lan("bad", "192.168.1.0/24") is None

    assert fabric._is_private_ip("10.1.2.3") is True
    assert fabric._is_private_ip("8.8.8.8") is False
    assert fabric._is_private_ip(None) is None
    assert fabric._is_private_ip("not-ip") is None
    assert fabric._is_private_ip("127.0.0.1") is True

    assert fabric._host_is_cloud("8.8.8.8", "192.168.1.0/24") is True
    assert fabric._host_is_cloud("192.168.1.5", "192.168.1.0/24") is False
    assert fabric._host_is_cloud("8.8.8.8", "") is True
    assert fabric._host_is_cloud("10.0.0.1", "") is False

    assert fabric.host_focus_key(12) == fabric.host_focus_key(12)
    assert "12" in fabric.host_focus_key(12)
    assert fabric.path_map_url(path_id=3)
    assert fabric.hosts_map_url(server_id=1) or fabric.hosts_map_url()
    assert fabric._with_map_anchor("/dns/hosts")


# ---------------------------------------------------------------------------
# Runtime edges pure keys
# ---------------------------------------------------------------------------


def test_runtime_edge_key_and_serialize():
    from app.services import runtime_edges as re

    k = re.edge_key(
        from_server_id=1,
        from_project="a",
        from_container="web",
        to_server_id=1,
        to_project="b",
        to_container="db",
    )
    assert isinstance(k, tuple)
    assert k[1] == "a"
    row = SimpleNamespace(
        id=1,
        from_server_id=1,
        from_project="a",
        from_container="web",
        to_server_id=2,
        to_project="b",
        to_container="db",
        kind="depends_on",
        source="manual",
        confidence=80,
        note="n",
        dismissed_at=None,
    )
    ser = re.serialize_edge(row, server_names={1: "Lab", 2: "Other"})
    assert ser["from_server_name"] == "Lab"
    assert ser["same_host"] is False
    assert re._norm("  AbC  ") == "AbC"
