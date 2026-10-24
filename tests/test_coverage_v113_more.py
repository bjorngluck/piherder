"""More line coverage above the 80% gate.

Move dest-up recovery, a backup retry, a straight-out pull, and diagnostics.
"""
from __future__ import annotations

import io
import json
from types import SimpleNamespace

import pytest
from sqlmodel import Session

from app.database import get_engine
from app.models import BackupDestination, Job, Server
from app.services import backup as backup_mod
from app.services import backup_replicate as copies
from app.services import diagnostics as diag
from app.services import jobs as jobs_mod
from app.services import jobs_migrate as jm
from app.services.service_migrate.dest_up_recover import DestUpRecoverError


def _session() -> Session:
    return Session(get_engine())


def _pair(session: Session) -> tuple[Server, Server]:
    src = Server(name="Source", hostname="host-a.example.com", ssh_username="backup")
    dst = Server(name="Dest", hostname="host-b.example.com", ssh_username="backup")
    session.add(src)
    session.add(dst)
    session.commit()
    session.refresh(src)
    session.refresh(dst)
    return src, dst


def _parent(session: Session, src: Server, dst: Server, **extra) -> Job:
    payload = {
        "failed_step": "worker_restart",
        "migrate_step": "dest_up",
        "dest_up_recover": {
            "source_id": int(src.id),
            "dest_id": int(dst.id),
            "project": "app",
            "dest_project": "app",
        },
    }
    payload.update(extra)
    job = Job(
        server_id=src.id,
        job_type="service_migrate",
        status="failed",
        details=json.dumps(payload),
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def _hold_locks(monkeypatch):
    monkeypatch.setattr(
        jm,
        "try_acquire_dual_server_lock",
        lambda *_args, **_kwargs: ("tok-a", "tok-b"),
    )
    monkeypatch.setattr(jm, "release_dual_server_lock", lambda *_args, **_kwargs: None)


def test_dest_recover_refuses_a_closed_surface_and_a_healthy_move(monkeypatch):
    monkeypatch.setattr(
        "app.services.service_migrate.host_lock.migrate_surface_allowed",
        lambda: False,
    )
    with pytest.raises(ValueError, match="off"):
        jm.enqueue_service_migrate_dest_recover(1)
    monkeypatch.setattr(
        "app.services.service_migrate.host_lock.migrate_surface_allowed",
        lambda: True,
    )
    with _session() as session:
        src, dst = _pair(session)
        parent = _parent(session, src, dst, failed_step="copy")
        with pytest.raises(ValueError, match="died while starting"):
            jm.enqueue_service_migrate_dest_recover(int(parent.id))
        gone = _parent(session, src, dst)
        details = json.loads(gone.details)
        details["dest_up_recover"]["dest_id"] = 99999
        gone.details = json.dumps(details)
        session.add(gone)
        session.commit()
        with pytest.raises(ValueError, match="gone"):
            jm.enqueue_service_migrate_dest_recover(int(gone.id))


def test_dest_recover_runs_inline_and_rejects_a_second_and_a_busy_host(monkeypatch):
    _hold_locks(monkeypatch)
    seen = {}

    def recover(**kwargs):
        seen["project"] = kwargs["project"]
        kwargs["log"]("dest stopped")
        return {"ok": True}

    monkeypatch.setattr(
        "app.services.service_migrate.dest_up_recover.run_dest_up_recover",
        recover,
    )
    with _session() as session:
        src, dst = _pair(session)
        parent = _parent(session, src, dst)
        job = jm.enqueue_service_migrate_dest_recover(int(parent.id), user_id=None)
        assert job is not None
        assert job.status == "success"
        assert seen["project"] == "app"
        session.expire_all()
        fresh = session.get(Job, parent.id)
        assert json.loads(fresh.details)["dest_up_recovered"] is True

        again = _parent(session, src, dst)
        session.add(
            Job(
                server_id=src.id,
                job_type="service_migrate_dest_recover",
                status="success",
                details=json.dumps({"parent_job_id": int(again.id)}),
            )
        )
        session.commit()
        with pytest.raises(ValueError, match="already recovered"):
            jm.enqueue_service_migrate_dest_recover(int(again.id))

        other = _parent(session, src, dst)
        busy = Job(
            server_id=src.id,
            job_type="docker_stack_start",
            status="running",
            details="{}",
        )
        session.add(busy)
        session.commit()
        with pytest.raises(jobs_mod.JobAlreadyActive):
            jm.enqueue_service_migrate_dest_recover(int(other.id))
        busy.status = "failed"
        session.add(busy)
        session.commit()


def test_dest_recover_lock_and_stop_failure(monkeypatch):
    monkeypatch.setattr(jm, "try_acquire_dual_server_lock", lambda *_a, **_k: None)
    with _session() as session:
        src, dst = _pair(session)
        parent = _parent(session, src, dst)
        job = jm.enqueue_service_migrate_dest_recover(int(parent.id))
        assert job.status == "failed"
        assert "already using" in (job.details or "")

    _hold_locks(monkeypatch)

    def stop_failed(**_kwargs):
        raise DestUpRecoverError("dest compose stop failed")

    monkeypatch.setattr(
        "app.services.service_migrate.dest_up_recover.run_dest_up_recover",
        stop_failed,
    )
    with _session() as session:
        src, dst = _pair(session)
        parent = _parent(session, src, dst)
        job = jm.enqueue_service_migrate_dest_recover(int(parent.id))
        assert job.status == "failed"
        assert "stop failed" in (job.details or "")


def test_dest_recover_celery_queue_and_a_missing_worker(monkeypatch):
    monkeypatch.setattr(jm, "_migrate_run_inline", lambda: False)
    monkeypatch.setattr(jobs_mod, "HAS_CELERY", False)
    with _session() as session:
        src, dst = _pair(session)
        parent = _parent(session, src, dst)
        with pytest.raises(RuntimeError, match="Celery worker"):
            jm.enqueue_service_migrate_dest_recover(int(parent.id))

    class Task:
        def delay(self, *_args):
            return SimpleNamespace(id="celery-1")

    monkeypatch.setattr(jobs_mod, "HAS_CELERY", True)
    monkeypatch.setattr(jobs_mod, "service_migrate_dest_recover_task", Task())
    with _session() as session:
        src, dst = _pair(session)
        parent = _parent(session, src, dst)
        job = jm.enqueue_service_migrate_dest_recover(int(parent.id))
        assert job.celery_task_id == "celery-1"
        assert job.status == "pending"

    class Down:
        def delay(self, *_args):
            raise ConnectionError("broker down")

    monkeypatch.setattr(jobs_mod, "service_migrate_dest_recover_task", Down())
    with _session() as session:
        src, dst = _pair(session)
        parent = _parent(session, src, dst)
        with pytest.raises(RuntimeError, match="broker down"):
            jm.enqueue_service_migrate_dest_recover(int(parent.id))


def test_dest_recover_execute_edges(monkeypatch):
    monkeypatch.setattr(
        "app.services.service_migrate.dest_up_recover.run_dest_up_recover",
        lambda **_kwargs: {"ok": True},
    )
    with _session() as session:
        src, dst = _pair(session)
        broken = Job(
            server_id=src.id,
            job_type="service_migrate_dest_recover",
            status="pending",
            details="{",
        )
        session.add(broken)
        session.commit()
        session.refresh(broken)
        jm._execute_dest_up_recover(int(broken.id), int(src.id), int(dst.id), 0)
        missing = Job(
            server_id=src.id,
            job_type="service_migrate_dest_recover",
            status="pending",
            details="{}",
        )
        session.add(missing)
        session.commit()
        session.refresh(missing)
        jm._execute_dest_up_recover(int(missing.id), 99998, 99999, 0)
        session.expire_all()
        assert session.get(Job, missing.id).status == "failed"


def test_backup_retries_with_sudo_after_permission_denied(tmp_path, monkeypatch):
    root = tmp_path / "backups"
    monkeypatch.setattr(backup_mod, "get_backup_root_for_server", lambda _server: root)
    monkeypatch.setattr(backup_mod, "get_private_key_plain", lambda _server: "key")
    monkeypatch.setattr(
        backup_mod,
        "get_ssh_client",
        lambda _server: SimpleNamespace(close=lambda: None),
    )
    monkeypatch.setattr(backup_mod, "_remote_rsync_path", lambda *_a, **_k: "/usr/bin/rsync")
    monkeypatch.setattr(backup_mod, "_folder_exists_via_ssh", lambda *_a, **_k: True)
    monkeypatch.setattr(backup_mod, "_send_webhook", lambda _msg: None)
    monkeypatch.setattr(backup_mod.time, "sleep", lambda _seconds: None)
    clock = {"now": 1_000.0}

    def tick():
        clock["now"] += 15
        return clock["now"]

    monkeypatch.setattr(backup_mod.time, "time", tick)
    procs = []

    class Proc:
        def __init__(self, rc: int, err: str):
            self.returncode = rc
            self.stderr = io.StringIO(err)
            self._polls = 0
            self.rc = rc

        def poll(self):
            self._polls += 1
            if self.rc != 0 and self._polls == 1:
                return None
            return self.rc

    def popen(cmd, **_kwargs):
        text = " ".join(cmd)
        if "sudo -n" in text:
            proc = Proc(0, "")
        else:
            proc = Proc(1, "permission denied")
        procs.append(text)
        return proc

    monkeypatch.setattr(backup_mod.subprocess, "Popen", popen)
    server = Server(
        id=41,
        name="Host A",
        hostname="host-a.example.com",
        ssh_username="backup",
    )
    out = backup_mod.run_backup(
        server,
        sources_override=[
            {"source": "/srv/off", "enabled": False},
            {"source": "/srv/app", "enabled": True, "dest_name": "app"},
        ],
    )
    assert out["results"][0]["rc"] == 0
    assert any("sudo -n" in cmd for cmd in procs)
    assert (root / "app").is_dir()


def test_backup_reports_a_root_permission_and_an_ssh_failure(tmp_path, monkeypatch):
    blocked = tmp_path / "blocked-root"

    def deny(_server):
        return blocked

    monkeypatch.setattr(backup_mod, "get_backup_root_for_server", deny)
    real_mkdir = type(blocked).mkdir

    def mkdir(self, *args, **kwargs):
        if self == blocked:
            raise PermissionError("read-only")
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(type(blocked), "mkdir", mkdir)
    server = Server(id=42, name="Host A", hostname="host-a.example.com", ssh_username="backup")
    out = backup_mod.run_backup(
        server,
        sources_override=[{"source": "/srv/app", "enabled": True}],
    )
    assert "Permission denied" in out["error"]

    monkeypatch.setattr(backup_mod, "get_backup_root_for_server", lambda _s: tmp_path / "ok")
    monkeypatch.setattr(type(blocked), "mkdir", real_mkdir)

    def no_ssh(_server):
        raise RuntimeError("no route")

    monkeypatch.setattr(backup_mod, "get_ssh_client", no_ssh)
    monkeypatch.setattr(backup_mod, "get_private_key_plain", lambda _s: "key")
    monkeypatch.setattr(backup_mod, "_send_webhook", lambda _msg: None)
    failed = backup_mod.run_backup(
        server,
        sources_override=[{"source": "/srv/app", "enabled": True}],
    )
    assert "no route" in failed["error"]


def test_pull_rel_dry_run_failure_and_redaction(tmp_path, monkeypatch):
    dest = BackupDestination(
        provider="drive",
        enabled=True,
        credentials_encrypted="tok",
        selection_json=json.dumps({"checked": ["host-a"], "skipped": []}),
        config_json='{"remote_dir":"PiHerder"}',
    )
    assert copies.pull_rel(dest, "../secret")["ok"] is False
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: True)
    assert "Demo" in copies.pull_rel(dest, "host-a")["error"]
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    other = BackupDestination(provider="s3", selection_json="{}")
    assert "not built" in copies.pull_rel(other, "host-a")["error"]
    assert "not selected" in copies.pull_rel(dest, "other/app")["error"]

    def refuse(_rel):
        raise ValueError("outside")

    monkeypatch.setattr(copies, "resolve_under_root", refuse)
    assert copies.pull_rel(dest, "host-a")["ok"] is False

    seen = {}

    def open_remote(_destination):
        return str(tmp_path / "rclone.conf"), "sekret", "refresh-1", False

    def run(cmd, secret):
        seen["cmd"] = cmd
        seen["secret"] = secret
        return seen.get("rc", 0), seen.get("err", "")

    (tmp_path / "rclone.conf").write_text("pass = obscured-pass\n", encoding="utf-8")
    monkeypatch.setattr(copies, "_open_destination_rclone", open_remote)
    monkeypatch.setattr(copies, "_run_rclone", run)
    folder = tmp_path / "pulled"
    ok = copies.pull_rel(dest, "host-a", dry_run=False, local_dir=folder)
    assert ok["ok"] is True
    assert folder.is_dir()
    seen["rc"] = 1
    seen["err"] = "sekret refresh-1 token rejected"
    bad = copies.pull_rel(dest, "host-a", dry_run=True, local_dir=folder)
    assert bad["ok"] is False
    assert "refresh-1" not in bad["error"]
    assert "[redacted]" in bad["error"]

    def explode(cmd, secret):
        raise RuntimeError("sekret refresh-1 exploded")

    monkeypatch.setattr(copies, "_run_rclone", explode)
    blown = copies.pull_rel(dest, "host-a", local_dir=folder)
    assert blown["ok"] is False
    assert "sekret" not in blown["error"]
    assert "refresh-1" not in blown["error"]


def test_diagnostics_records_hardware_haos_and_a_connect_error(monkeypatch):
    diag.clear_diagnostics_cache()
    monkeypatch.setattr(diag, "ping", lambda _host, timeout=2: True)

    def run(_client, cmd, timeout=8):
        if "uname -r" in cmd:
            return 0, "6.6.0\n", ""
        if "os-release" in cmd:
            return 0, "ID=debian\nPRETTY=Debian\n", ""
        if "uname -m" in cmd:
            return 0, "aarch64\n", ""
        if "device-tree" in cmd:
            return 0, "Pi model\n", ""
        if "product_name" in cmd:
            return 0, "Pi 5\n", ""
        if "sys_vendor" in cmd:
            return 0, "Example Vendor\n", ""
        if "reboot-required" in cmd:
            return 0, "yes\n", ""
        if "nproc" in cmd:
            return 0, "4\n", ""
        if "loadavg" in cmd:
            return 0, "0.20\n", ""
        if "MemTotal" in cmd:
            return 0, run.mem, ""
        return 1, "", ""

    run.mem = "8192 1024\n"
    monkeypatch.setattr(diag, "run_command", run)
    monkeypatch.setattr(diag, "get_ssh_client", lambda _server: SimpleNamespace(close=lambda: None))
    panel = {
        "host": {
            "kernel": "6.6.0-ha",
            "operating_system": "HAOS",
            "disk_total_h": "32G",
            "disk_used_h": "10G",
            "disk_free_h": "22G",
            "disk_free_gb": 22,
            "disk_pcent": 30,
            "chassis": "embedded",
            "disk_life_time": "ok",
            "usage_drives": [],
            "machine": "pi5",
        },
        "components": {"core": {"machine": "board"}},
    }
    monkeypatch.setattr("app.services.haos.is_haos_server", lambda _server: True)
    monkeypatch.setattr("app.services.haos.gather_system_panel", lambda _client: panel)
    monkeypatch.setattr(diag, "summarize_usable_space", lambda _drives: {})
    server = Server(id=77, name="Host A", hostname="host-a.example.com")
    info = diag.run_diagnostics(server, force=True)
    assert info["kernel"] == "6.6.0-ha"
    assert info["profile"] == "haos"
    assert info["reboot_pending"] is True
    assert info["device_tree"] == "Pi model"
    assert info["memory_total_bytes"] == 8192 * 1024
    assert info["summary"]["root"]["filesystem"] == "ha-host"
    assert info["drives_source"] == "ha host info"

    panel["host"]["usage_drives"] = [
        {"filesystem": "overlay", "size": "32G", "used": "1G", "avail": "31G", "pcent": "4%", "target": "/"}
    ]
    diag.clear_diagnostics_cache(77)
    again = diag.run_diagnostics(server, force=True)
    assert again["drives_source"] == "ha host disks usage"

    def gather_down(_client):
        raise RuntimeError("ha cli down")

    monkeypatch.setattr("app.services.haos.gather_system_panel", gather_down)
    diag.clear_diagnostics_cache(77)
    broken = diag.run_diagnostics(server, force=True)
    assert "ha cli down" in broken["ha"]["error"]

    def no_ssh(_server):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(diag, "get_ssh_client", no_ssh)
    diag.clear_diagnostics_cache(77)
    down = diag.run_diagnostics(server, force=True)
    assert "connection refused" in down["error"]
