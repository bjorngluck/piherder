"""v1.6 Q-80 third pack — job execute, nmap stream, DNS plan, vanished retry."""
from __future__ import annotations

import io
import json
import subprocess
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models import AuditLog, Job, Server


def _memory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine), engine


def _server(session, **kw):
    s = Server(
        name=kw.get("name", "pi"),
        hostname=kw.get("hostname", "pi.local"),
        ssh_username="pi",
        docker_base_dir="/home/pi/docker",
        os_type="debian",
        backup_enabled=True,
        container_patch_enabled=True,
        dns_name=kw.get("dns_name", "pi.lan"),
        backup_paths=kw.get(
            "backup_paths",
            json.dumps(
                [{"source": "/home/pi/docker/grafana", "dest_name": "grafana", "enabled": True}]
            ),
        ),
        ssh_private_key_encrypted="enc",
    )
    session.add(s)
    session.commit()
    session.refresh(s)
    return s


def _job_audit(session, server, job_type="docker_stack_stop"):
    job = Job(server_id=server.id, job_type=job_type, status="pending", details="{}")
    session.add(job)
    session.commit()
    session.refresh(job)
    audit = AuditLog(
        server_id=server.id,
        action=job_type,
        status="running",
        details=f"Job #{job.id} started",
    )
    session.add(audit)
    session.commit()
    session.refresh(audit)
    return job, audit


def test_execute_docker_stack_lifecycle_and_remove(monkeypatch):
    from app.services import jobs as js

    session, engine = _memory()
    srv = _server(session)
    monkeypatch.setattr(js, "engine", engine)

    @contextmanager
    def _fresh():
        s = Session(engine, expire_on_commit=False)
        try:
            yield s
        finally:
            s.close()

    monkeypatch.setattr(js, "_get_fresh_session", _fresh)
    monkeypatch.setattr(js, "_flush_job_progress", lambda *a, **k: None)
    monkeypatch.setattr(js, "_append_output_log_lines", lambda *a, **k: None)
    monkeypatch.setattr(js, "_send_summary_webhook", lambda *a, **k: None)
    monkeypatch.setattr(
        "app.services.docker_management.compose_action",
        lambda *a, **k: {"success": True, "output": "ok"},
    )
    monkeypatch.setattr(
        "app.services.docker_inventory.invalidate_after_mutation",
        lambda *a, **k: None,
    )

    job, audit = _job_audit(session, srv, "docker_stack_stop")
    js._execute_docker_stack_lifecycle(
        job.id, srv.id, audit.id, "/home/pi/docker/grafana", "stop"
    )
    session.expire_all()
    assert session.get(Job, job.id).status == "success"

    jobf, auditf = _job_audit(session, srv, "docker_stack_stop")
    js._execute_docker_stack_lifecycle(
        jobf.id, 99999, auditf.id, "/home/pi/docker/grafana", "stop"
    )
    session.expire_all()
    assert session.get(Job, jobf.id).status == "failed"

    jobe, audite = _job_audit(session, srv, "docker_stack_restart")
    monkeypatch.setattr(
        "app.services.docker_management.compose_action",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("compose")),
    )
    js._execute_docker_stack_lifecycle(
        jobe.id, srv.id, audite.id, "/home/pi/docker/grafana", "restart"
    )
    session.expire_all()
    assert session.get(Job, jobe.id).status == "failed"

    monkeypatch.setattr(
        "app.services.service_migrate.leftover.wipe_compose_project",
        lambda *a, **k: {"project_removed": True},
    )
    jobr, auditr = _job_audit(session, srv, "docker_stack_remove")
    js._execute_docker_stack_remove(jobr.id, srv.id, auditr.id, "/home/pi/docker/old")
    session.expire_all()
    assert session.get(Job, jobr.id).status == "success"

    jobrf, auditrf = _job_audit(session, srv, "docker_stack_remove")
    monkeypatch.setattr(
        "app.services.service_migrate.leftover.wipe_compose_project",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("busy")),
    )
    js._execute_docker_stack_remove(jobrf.id, srv.id, auditrf.id, "/home/pi/docker/old")
    session.expire_all()
    assert session.get(Job, jobrf.id).status == "failed"
    assert js._project_basename("/home/pi/docker/grafana/") == "grafana"


def test_nmap_streaming_and_timeout(monkeypatch):
    from app.services.nmap import scan as nscan

    session, _ = _memory()

    class Proc:
        def __init__(self, lines, rc=0, hang=False):
            self.stdout = iter(lines)
            self.returncode = rc
            self._hang = hang

        def wait(self, timeout=None):
            if self._hang:
                raise subprocess.TimeoutExpired(cmd="nmap", timeout=timeout or 1)
            return self.returncode

        def kill(self):
            self._hang = False

    monkeypatch.setattr(nscan.subprocess, "Popen", lambda *a, **k: Proc(["Starting Nmap\n", "Done\n"], 0))
    monkeypatch.setattr(nscan, "_log", lambda *a, **k: None)
    monkeypatch.setattr(nscan, "merge_job_details", lambda *a, **k: None)
    monkeypatch.setattr(nscan, "touch_worker_heartbeat", lambda: None)
    out = nscan._run_nmap_streaming(session, 1, ["nmap", "-sn", "10.0.0.0/24"], timeout_sec=5)
    assert out.returncode == 0
    assert "Starting Nmap" in out.stdout

    monkeypatch.setattr(
        nscan.subprocess, "Popen", lambda *a, **k: Proc(["x\n"], hang=True)
    )
    with pytest.raises(subprocess.TimeoutExpired):
        nscan._run_nmap_streaming(session, 1, ["nmap", "x"], timeout_sec=0.01)


def test_dns_plan_and_candidates():
    from app.services.dns_fabric.core import (
        DnsFabricError,
        list_service_dns_candidates,
        resolve_service_dns_plan,
    )

    session, _ = _memory()
    with pytest.raises(DnsFabricError):
        resolve_service_dns_plan(session, backend_server_id=1)
    srv = _server(session)
    plan = resolve_service_dns_plan(
        session, backend_server_id=srv.id, docker_project="grafana", fqdn="g.lan"
    )
    assert plan.get("fqdn") or plan.get("backend_server_id") == srv.id or "fqdn" in plan
    cands = list_service_dns_candidates(session, base_domain="lan")
    assert isinstance(cands, list)


def test_port_annotation_upsert_and_enrich():
    from app.services.dns_fabric.host_ports import (
        enrich_with_server_annotations,
        upsert_port_annotation,
    )

    session, _ = _memory()
    srv = _server(session)
    with pytest.raises(ValueError):
        upsert_port_annotation(session, host_port=80)
    row = upsert_port_annotation(
        session,
        server_id=srv.id,
        host_port=443,
        proto="TCP",
        role_key="web",
        label="https",
        note="edge",
        hide=False,
        user_id=1,
    )
    assert row.role_key == "web"
    again = upsert_port_annotation(
        session, server_id=srv.id, host_port=443, proto="tcp", clear_role=True, label=""
    )
    assert again.id == row.id
    assert again.role_key is None
    with pytest.raises(ValueError):
        upsert_port_annotation(
            session, server_id=srv.id, host_port=80, role_key="not-a-role"
        )
    containers = [
        {
            "name": "proxy",
            "ports_parsed": [{"host": "443", "proto": "tcp", "published": True, "label": "443/tcp"}],
        }
    ]
    # restore role for enrich
    upsert_port_annotation(
        session, server_id=srv.id, host_port=443, proto="tcp", role_key="web"
    )
    out = enrich_with_server_annotations(session, containers, server_id=srv.id)
    assert out


def test_backup_vanished_retry_then_ok(tmp_path, monkeypatch):
    from app.services import backup as bak

    session, _ = _memory()
    srv = _server(session)
    root = tmp_path / "dest"
    root.mkdir()
    monkeypatch.setattr(bak, "get_backup_root_for_server", lambda *_a, **_k: root)
    monkeypatch.setattr(bak, "get_private_key_plain", lambda *_a, **_k: "KEY")
    monkeypatch.setattr(bak, "_send_webhook", lambda *_a, **_k: None)
    monkeypatch.setattr(bak, "_clear_progress", lambda *_a, **_k: None)
    monkeypatch.setattr(bak, "_set_progress", lambda *a, **k: None)
    monkeypatch.setattr(bak, "_flush_job_progress_db", lambda *a, **k: None)
    monkeypatch.setattr(bak, "clear_job_progress_buffer", lambda *a, **k: None)
    monkeypatch.setattr(bak, "get_dir_size", lambda *_a, **_k: 1)
    monkeypatch.setattr(bak, "human_size", lambda n: "1 B")
    monkeypatch.setattr(bak, "_remote_rsync_path", lambda *a, **k: "sudo -n rsync")
    monkeypatch.setattr(bak, "_folder_exists_via_ssh", lambda *a, **k: True)
    monkeypatch.setattr(bak, "_build_rsync_ssh_cmd", lambda *_a, **_k: "ssh")
    monkeypatch.setattr(bak.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(bak, "_vanished_retry_count", lambda: 1)
    monkeypatch.setattr(bak, "_vanished_retry_delay_sec", lambda: 0)
    monkeypatch.setattr(bak, "_vanished_soft_ok_enabled", lambda: True)

    @contextmanager
    def _key(_priv):
        yield str(tmp_path / "k")

    monkeypatch.setattr(bak, "temp_key_file", _key)
    monkeypatch.setattr(bak, "get_ssh_client", lambda *_a, **_k: MagicMock())

    n = {"i": 0}

    class Proc:
        def __init__(self, rc, err=""):
            self.returncode = rc
            self.stderr = io.StringIO(err)

        def poll(self):
            return self.returncode

    def _popen(*a, **k):
        n["i"] += 1
        if n["i"] == 1:
            return Proc(24, "file has vanished")
        return Proc(0, "")

    monkeypatch.setattr(bak.subprocess, "Popen", _popen)
    out = bak.run_backup(srv, job_id=3)
    assert out.get("ok") is True or out.get("results")


def test_nmap_run_missing_and_disabled_integration(monkeypatch):
    from app.models import Integration, NmapScanRun
    from app.services.nmap import scan as nscan

    session, _ = _memory()
    monkeypatch.setattr(
        "app.services.nmap.worker_guard.ensure_nmap_worker_runtime", lambda: None
    )
    monkeypatch.setattr(nscan, "touch_worker_heartbeat", lambda: None)
    monkeypatch.setattr(nscan, "merge_job_details", lambda *a, **k: None)
    monkeypatch.setattr(nscan, "stamp_line", lambda s: s)

    missing = nscan.run_nmap_scan(session, run_id=999)
    assert missing["status"] == "error"

    integ = Integration(type="nmap", name="LAN", base_url="", enabled=False)
    session.add(integ)
    session.commit()
    session.refresh(integ)
    run = NmapScanRun(integration_id=integ.id, intensity="discovery", status="pending")
    session.add(run)
    session.commit()
    session.refresh(run)
    failed = nscan.run_nmap_scan(session, run_id=run.id, job_id=1)
    assert failed["status"] == "failed"

    integ.enabled = True
    integ.config_json = json.dumps({"cidrs": []})
    session.add(integ)
    session.commit()
    run2 = NmapScanRun(integration_id=integ.id, intensity="discovery", status="pending")
    session.add(run2)
    session.commit()
    session.refresh(run2)
    monkeypatch.setattr(nscan, "_integration_cidrs", lambda *a, **k: ([], []))
    no_cidr = nscan.run_nmap_scan(session, run_id=run2.id)
    assert no_cidr["status"] == "failed"


def test_stack_panel_for_server():
    from app.services.dns_fabric.stack_panel import build_stack_panel

    session, _ = _memory()
    srv = _server(session)
    srv.docker_inventory_json = json.dumps(
        {
            "v": 1,
            "meta": {"project_count": 1, "container_count": 1},
            "projects": [
                {
                    "name": "grafana",
                    "path": "/home/pi/docker/grafana",
                    "containers": [
                        {
                            "name": "grafana",
                            "image": "grafana/grafana:latest",
                            "state": "running",
                            "compose_service": "grafana",
                        }
                    ],
                }
            ],
        }
    )
    srv.docker_inventory_status = "ok"
    session.add(srv)
    session.commit()
    panel = build_stack_panel(session, server_id=srv.id, project="grafana")
    assert panel.get("ok") is True
    missing = build_stack_panel(session, server_id=99999)
    assert missing.get("ok") is False


def test_create_herder_backup_config_only(tmp_path, monkeypatch):
    from app.services import herder_backup as hb

    session, engine = _memory()
    _server(session)
    monkeypatch.setattr(hb, "engine", engine)
    monkeypatch.setattr(hb, "HERDER_BACKUP_DIR", tmp_path)
    monkeypatch.setattr(hb, "_ensure_dir", lambda: None)
    monkeypatch.setattr(hb, "prune_old_backups", lambda *a, **k: None)
    monkeypatch.setattr(hb, "load_settings", lambda: {"keep": 3})
    monkeypatch.setattr(hb, "_add_data_files_to_tar", lambda *a, **k: 0)
    monkeypatch.setattr(hb, "archive_dir_candidates", lambda: [tmp_path])
    monkeypatch.setattr(hb, "_path_is_writable", lambda p: True)
    monkeypatch.setattr(hb.settings, "DATA_ROOT", str(tmp_path / "data"))
    (tmp_path / "data").mkdir(exist_ok=True)
    path = hb.create_herder_backup(include_audit=False, config_only=True)
    assert path.exists()
    assert "config" in path.name
