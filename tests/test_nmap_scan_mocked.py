"""run_nmap_scan / enqueue paths with SQLite + mocks (no live nmap binary)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models import Integration, Job, NmapScanRun
from app.services.nmap import scan as nscan

FIXTURE = Path(__file__).parent / "fixtures" / "nmap_sample.xml"


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s
    engine.dispose()


def _integ(session: Session, **cfg_extra) -> Integration:
    cfg = {
        "cidrs": ["192.168.1.0/24"],
        "excludes": [],
        "use_syn": False,
        "vuln_enabled": True,
        "skip_dns": True,
    }
    cfg.update(cfg_extra)
    integ = Integration(
        type="nmap",
        name="LAN",
        base_url="",
        enabled=True,
        config_json=json.dumps(cfg),
    )
    session.add(integ)
    session.commit()
    session.refresh(integ)
    return integ


def _run(session: Session, integ_id: int, **kw) -> NmapScanRun:
    run = NmapScanRun(
        integration_id=integ_id,
        intensity=kw.get("intensity", "discovery"),
        targets_json=kw.get("targets_json"),
        status="pending",
        job_id=kw.get("job_id"),
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _guard_ok(monkeypatch):
    monkeypatch.setattr(
        "app.services.nmap.worker_guard.ensure_nmap_worker_runtime",
        lambda: "/usr/bin/nmap",
    )
    monkeypatch.setattr(nscan, "touch_worker_heartbeat", lambda *a, **k: None)
    monkeypatch.setattr(nscan, "set_progress", lambda *a, **k: None)
    monkeypatch.setattr(nscan, "try_acquire_lock", lambda *a, **k: True)
    monkeypatch.setattr(nscan, "release_lock", lambda *a, **k: None)


def test_run_nmap_scan_missing_run(db, monkeypatch):
    _guard_ok(monkeypatch)
    out = nscan.run_nmap_scan(db, run_id=99999)
    assert out["status"] == "error"


def test_run_nmap_scan_disabled_integration(db, monkeypatch):
    _guard_ok(monkeypatch)
    integ = _integ(db)
    integ.enabled = False
    db.add(integ)
    db.commit()
    run = _run(db, integ.id)
    out = nscan.run_nmap_scan(db, run_id=run.id)
    assert out["status"] == "failed"
    db.refresh(run)
    assert run.status == "failed"
    assert "disabled" in (run.error or "").lower() or "missing" in (run.error or "").lower()


def test_run_nmap_scan_no_cidrs(db, monkeypatch):
    _guard_ok(monkeypatch)
    integ = _integ(db, cidrs=[])
    run = _run(db, integ.id)
    out = nscan.run_nmap_scan(db, run_id=run.id)
    assert out["status"] == "failed"
    assert "cidr" in (out.get("error") or "").lower()


def test_run_nmap_scan_rejected_targets(db, monkeypatch):
    _guard_ok(monkeypatch)
    integ = _integ(db)
    run = _run(db, integ.id, targets_json=json.dumps(["8.8.8.8"]))
    out = nscan.run_nmap_scan(db, run_id=run.id)
    assert out["status"] == "failed"
    assert "allowed" in (out.get("error") or "").lower() or "rejected" in (
        out.get("error") or ""
    ).lower()


def test_run_nmap_scan_lock_busy(db, monkeypatch):
    _guard_ok(monkeypatch)
    monkeypatch.setattr(nscan, "try_acquire_lock", lambda *a, **k: False)
    integ = _integ(db)
    run = _run(db, integ.id)
    out = nscan.run_nmap_scan(db, run_id=run.id)
    assert out["status"] == "failed"
    assert "lock" in (out.get("error") or "").lower()


def test_run_nmap_scan_success_with_fixture_xml(db, tmp_path, monkeypatch):
    _guard_ok(monkeypatch)
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    integ = _integ(db)
    job = Job(server_id=None, job_type="nmap_discovery", status="pending", details="{}")
    db.add(job)
    db.commit()
    db.refresh(job)
    run = _run(db, integ.id, job_id=job.id, intensity="inventory")

    def fake_stream(session, job_id, argv, *, timeout_sec):
        # Write sample XML where nmap would
        out_xml = None
        for i, a in enumerate(argv):
            if a in ("-oX", "-oA") and i + 1 < len(argv):
                out_xml = Path(argv[i + 1])
                break
            if a.startswith("-oX"):
                out_xml = Path(a[3:] if len(a) > 3 else argv[i + 1])
        # build_nmap_argv uses -oX path
        for i, a in enumerate(argv):
            if a == "-oX" and i + 1 < len(argv):
                out_xml = Path(argv[i + 1])
        assert out_xml is not None
        out_xml.parent.mkdir(parents=True, exist_ok=True)
        out_xml.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout="Nmap done", stderr=""
        )

    monkeypatch.setattr(nscan, "_run_nmap_streaming", fake_stream)
    out = nscan.run_nmap_scan(db, run_id=run.id, use_syn=False)
    assert out["status"] == "success"
    db.refresh(run)
    assert run.status == "success"
    assert run.hosts_up >= 1
    assert run.artifact_path


def test_run_nmap_scan_no_xml_fails(db, tmp_path, monkeypatch):
    _guard_ok(monkeypatch)
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    integ = _integ(db)
    run = _run(db, integ.id)

    def fake_stream(session, job_id, argv, *, timeout_sec):
        return subprocess.CompletedProcess(
            args=argv, returncode=1, stdout="failed", stderr="no xml"
        )

    monkeypatch.setattr(nscan, "_run_nmap_streaming", fake_stream)
    out = nscan.run_nmap_scan(db, run_id=run.id)
    assert out["status"] == "failed"
    assert "no XML" in (out.get("error") or "")


def test_run_nmap_scan_nse_failed(db, tmp_path, monkeypatch):
    _guard_ok(monkeypatch)
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    integ = _integ(db)
    run = _run(db, integ.id, intensity="deep")

    def fake_stream(session, job_id, argv, *, timeout_sec):
        out_xml = None
        for i, a in enumerate(argv):
            if a == "-oX" and i + 1 < len(argv):
                out_xml = Path(argv[i + 1])
        assert out_xml
        out_xml.parent.mkdir(parents=True, exist_ok=True)
        out_xml.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
        return subprocess.CompletedProcess(
            args=argv,
            returncode=1,
            stdout="Failed to initialize the script engine\nQUITTING!\n",
            stderr="",
        )

    monkeypatch.setattr(nscan, "_run_nmap_streaming", fake_stream)
    out = nscan.run_nmap_scan(
        db, run_id=run.id, script_preset="cpe", vuln_scripts=True
    )
    assert out["status"] == "failed"
    assert "NSE" in (out.get("error") or "") or "script" in (
        out.get("error") or ""
    ).lower()


def test_run_nmap_scan_timeout(db, tmp_path, monkeypatch):
    _guard_ok(monkeypatch)
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    integ = _integ(db)
    run = _run(db, integ.id)

    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd=["nmap"], timeout=1)

    monkeypatch.setattr(nscan, "_run_nmap_streaming", boom)
    out = nscan.run_nmap_scan(db, run_id=run.id)
    assert out["status"] == "failed"
    assert "timed out" in (out.get("error") or "").lower()


def test_enqueue_nmap_scan_creates_job_and_dispatches(db, monkeypatch):
    integ = _integ(db)

    class FakeCelery:
        def send_task(self, *args, **kwargs):
            return SimpleNamespace(id="task-abc")

    monkeypatch.setattr("app.celery_app.celery", FakeCelery())
    job, run = nscan.enqueue_nmap_scan(
        db,
        integration_id=integ.id,
        intensity="discovery",
        user_id=1,
    )
    assert job.id is not None
    assert run.integration_id == integ.id
    assert run.intensity == "discovery"
    assert job.job_type == "nmap_discovery"
    assert job.celery_task_id == "task-abc"
