"""v1.4 coverage — docker stack jobs, versions, patching, diagnostics, host deps."""
from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models import DockerVersion, Job, Server


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
        name=kw.get("name", "a"),
        hostname=kw.get("hostname", "a.local"),
        ssh_username=kw.get("ssh_username", "pi"),
        docker_base_dir=kw.get("docker_base_dir", "/home/pi/docker"),
        container_patch_enabled=True,
        backup_enabled=kw.get("backup_enabled", True),
        os_patch_enabled=kw.get("os_patch_enabled", True),
        os_type=kw.get("os_type", "debian"),
    )
    session.add(s)
    session.commit()
    session.refresh(s)
    return s


class _Pool:
    def submit(self, fn, *a, **k):
        fn(*a, **k)
        return SimpleNamespace()


class MemSFTP:
    def __init__(self, files=None):
        self.files = dict(files or {})

    def open(self, path, mode="r"):
        if "r" in mode and "w" not in mode:
            if path not in self.files:
                raise IOError("missing")
            data = self.files[path]
            if isinstance(data, str):
                data = data.encode()
            return io.BytesIO(data)

        files = self.files

        class W:
            def __init__(self):
                self.buf = io.BytesIO()

            def write(self, data):
                if isinstance(data, str):
                    data = data.encode()
                self.buf.write(data)
                files[path] = self.buf.getvalue()

            def read(self, n=-1):
                return self.buf.read(n)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return W()

    def close(self):
        pass

    def mkdir(self, path):
        pass

    def remove(self, path):
        self.files.pop(path, None)

    def rename(self, src, dst):
        self.files[dst] = self.files.pop(src)

    def stat(self, path):
        if path not in self.files:
            raise IOError("missing")
        return True


class Ssh:
    def __init__(self, sftp=None):
        self._sftp = sftp or MemSFTP()
        self.closed = False

    def open_sftp(self):
        return self._sftp

    def close(self):
        self.closed = True

    def exec_command(self, cmd, timeout=None, get_pty=False):
        stdout = MagicMock()
        stdout.channel.recv_exit_status.return_value = 0
        stdout.channel.exit_status_ready.return_value = True
        stdout.read.return_value = b""
        return MagicMock(), stdout, MagicMock()


def _smart_run(_client, cmd, timeout=15):
    c = str(cmd)
    if "piherder_ok" in c:
        return 0, "piherder_ok\nLinux\n", ""
    if "command -v rsync" in c or "which rsync" in c or "test -x /usr/bin/rsync" in c:
        return 0, "/usr/bin/rsync\n", ""
    if "rsync --version" in c:
        return 0, "rsync  version 3.2\n", ""
    if "command -v docker" in c or "docker version" in c or "docker info" in c:
        return 0, "/usr/bin/docker\n24.0.7\n", ""
    if "command -v apt" in c or "command -v apt-get" in c:
        return 0, "/usr/bin/apt-get\n", ""
    if "command -v ha" in c or "which ha" in c:
        return 0, "/usr/bin/ha\n", ""
    if "uname -r" in c:
        return 0, "6.1.0-rpi\n", ""
    if "uname -s" in c or "uname -m" in c or "uname -o" in c:
        return 0, "Linux\n", ""
    if "os-release" in c:
        return 0, "Debian GNU/Linux 12\n", ""
    if "reboot-required" in c:
        return 0, "yes\n", ""
    if "df -h --output" in c:
        return 0, "/dev/sda1  30G  10G  18G  36% /\n", ""
    if "df -h" in c:
        return (
            0,
            "Filesystem Size Used Avail Use% Mounted on\n"
            "/dev/sda1 30G 10G 18G 36% /\n"
            "tmpfs 1G 0 1G 0% /run\n",
            "",
        )
    if "ls -1" in c:
        return 0, "grafana\nn8n\n", ""
    if "compose.*" in c or "docker-compose.*" in c:
        return 0, "/home/pi/docker/grafana/docker-compose.yml\n", ""
    if "config --images" in c:
        return 0, "grafana/grafana:11\n", ""
    if "config --format json" in c:
        return 0, json.dumps({"services": {"grafana": {"image": "grafana/grafana:11"}}}), ""
    if "docker inspect" in c:
        return 0, "sha256:abc\n", ""
    if "compose pull" in c:
        return 0, "Pulled\n", ""
    if "compose up" in c:
        return 0, "Started\n", ""
    if "cat " in c and "compose" in c:
        return 0, "services:\n  grafana:\n    image: grafana/grafana:11\n", ""
    if "mkdir -p" in c:
        return 0, "", ""
    if "git clone" in c:
        return 0, "", ""
    return 0, "", ""


# ---------------------------------------------------------------------------
# jobs: stack check / deploy / lifecycle / remove + cancel + orphan cleanup
# ---------------------------------------------------------------------------


def test_jobs_stack_check_deploy_lifecycle_remove(monkeypatch):
    from app.services import jobs as jobs_mod

    session, engine = _memory()
    srv = _server(session)
    monkeypatch.setattr(jobs_mod, "engine", engine)
    monkeypatch.setattr(jobs_mod, "_update_check_pool", _Pool())
    monkeypatch.setattr(jobs_mod, "_patch_apply_pool", _Pool())

    monkeypatch.setattr(
        "app.services.docker_management.check_compose_updates",
        lambda s, p: {
            "has_updates": True,
            "updated_images": ["grafana/grafana:11"],
            "success": True,
            "pull_output": "Pulled grafana\n",
        },
    )
    monkeypatch.setattr(
        "app.services.docker_management.redeploy_project",
        lambda s, p, pull=True, compose_files=None: {
            "success": True,
            "output": "up ok\n",
            "pull_status": 0,
            "up_status": 0,
        },
    )
    monkeypatch.setattr(
        "app.services.docker_management.compose_action",
        lambda s, p, act, service=None, remove_volumes=False: {
            "success": True,
            "output": f"{act} ok",
            "error": None,
        },
    )
    monkeypatch.setattr(
        "app.services.docker_inventory.invalidate_after_mutation",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "app.services.service_migrate.leftover.wipe_compose_project",
        lambda *a, **k: {"project_removed": True},
    )

    with pytest.raises(ValueError):
        jobs_mod.enqueue_docker_stack_check(srv.id, "")
    with pytest.raises(ValueError):
        jobs_mod.enqueue_docker_stack_check(99999, "/home/pi/docker/grafana")

    chk = jobs_mod.enqueue_docker_stack_check(srv.id, "/home/pi/docker/grafana")
    assert chk is not None
    session.expire_all()
    assert session.get(Job, chk.id).status == "success"
    session.refresh(srv)
    assert "grafana" in (srv.container_updates_summary or "")

    with pytest.raises(jobs_mod.JobAlreadyActive):
        # insert a running check then enqueue
        busy = Job(
            server_id=srv.id,
            job_type="docker_stack_check",
            status="running",
            details=json.dumps({"project_path": "/home/pi/docker/grafana"}),
        )
        session.add(busy)
        session.commit()
        try:
            jobs_mod.enqueue_docker_stack_check(srv.id, "/home/pi/docker/grafana")
        finally:
            session.delete(busy)
            session.commit()

    with pytest.raises(ValueError):
        jobs_mod.enqueue_docker_stack_deploy(srv.id, "")
    dep = jobs_mod.enqueue_docker_stack_deploy(
        srv.id, "/home/pi/docker/grafana", pull=True, compose_files=["docker-compose.yml"]
    )
    assert dep is not None
    session.expire_all()
    assert session.get(Job, dep.id).status == "success"

    with pytest.raises(ValueError):
        jobs_mod.enqueue_docker_stack_lifecycle(srv.id, "/x", "explode")
    with pytest.raises(ValueError):
        jobs_mod.enqueue_docker_stack_lifecycle(srv.id, "", "stop")
    life = jobs_mod.enqueue_docker_stack_lifecycle(
        srv.id, "/home/pi/docker/grafana", "down", remove_volumes=True
    )
    assert life is not None
    session.expire_all()
    assert session.get(Job, life.id).status == "success"

    with pytest.raises(ValueError):
        jobs_mod.enqueue_docker_stack_remove(srv.id, "")
    with pytest.raises(ValueError):
        jobs_mod.enqueue_docker_stack_remove(srv.id, "/etc/passwd")
    rm = jobs_mod.enqueue_docker_stack_remove(srv.id, "/home/pi/docker/grafana")
    assert rm is not None
    session.expire_all()
    assert session.get(Job, rm.id).status == "success"

    # execute missing server
    jobs_mod._execute_docker_stack_check(1, 99999, 1, "/x")
    jobs_mod._execute_docker_stack_deploy(1, 99999, 1, "/x")
    jobs_mod._execute_docker_stack_lifecycle(1, 99999, 1, "/x", "stop")
    jobs_mod._execute_docker_stack_remove(1, 99999, 1, "/x")

    # check/deploy failure branches
    monkeypatch.setattr(
        "app.services.docker_management.check_compose_updates",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ssh")),
    )
    fail_chk = jobs_mod.enqueue_docker_stack_check(srv.id, "/home/pi/docker/n8n")
    session.expire_all()
    assert session.get(Job, fail_chk.id).status == "failed"

    monkeypatch.setattr(
        "app.services.docker_management.redeploy_project",
        lambda *a, **k: {"success": False, "error": "up failed", "output": "boom"},
    )
    fail_dep = jobs_mod.enqueue_docker_stack_deploy(srv.id, "/home/pi/docker/n8n")
    session.expire_all()
    assert session.get(Job, fail_dep.id).status == "failed"

    jobs_mod._apply_single_project_check_result(session, 99999, "/x", {})
    jobs_mod._apply_single_project_deploy_result(session, 99999, "/x", True)
    jobs_mod._apply_single_project_deploy_result(session, srv.id, "/home/pi/docker/grafana", False)

    jobs_mod._append_output_log_lines(fail_dep.id, "deploying", "line1\nline2\n")
    jobs_mod._append_output_log_lines(fail_dep.id, "deploying", "")
    jobs_mod._flush_container_progress_to_job(fail_dep.id, "patching", "hi")
    jobs_mod._send_summary_webhook("h", "backup", "failed", "x")
    jobs_mod._send_summary_webhook("h", "os_patch", "success", "ok")


def test_jobs_update_checks_cancel_orphan_demo(monkeypatch):
    from app.services import jobs as jobs_mod
    from fastapi import BackgroundTasks

    session, engine = _memory()
    srv = _server(session)
    monkeypatch.setattr(jobs_mod, "engine", engine)
    monkeypatch.setattr(jobs_mod, "_update_check_pool", _Pool())
    monkeypatch.setattr(
        jobs_mod.os_patching,
        "check_os_updates",
        lambda s: {"updates_count": 1, "reboot_pending": False, "packages_sample": ["linux"]},
    )
    monkeypatch.setattr(
        jobs_mod.container_patching,
        "check_all_projects_updates",
        lambda s: {"projects_with_updates": ["grafana"], "project_details": {}},
    )

    assert jobs_mod.enqueue_os_update_check(99999) is None
    osj = jobs_mod.enqueue_os_update_check(srv.id)
    assert osj is not None
    session.expire_all()
    assert session.get(Job, osj.id).status == "success"
    again = jobs_mod.enqueue_os_update_check(srv.id)
    # previous finished — new one allowed
    assert again is not None

    running = Job(server_id=srv.id, job_type="os_update_check", status="running", details="{}")
    session.add(running)
    session.commit()
    skipped = jobs_mod.enqueue_os_update_check(srv.id)
    assert skipped.id == running.id
    session.delete(running)
    session.commit()

    cj = jobs_mod.enqueue_container_update_check(srv.id)
    assert cj is not None
    assert jobs_mod.enqueue_container_update_check(99999) is None

    now = jobs_mod.run_os_update_check_now(session, srv)
    assert now is not None
    now2 = jobs_mod.run_container_update_check_now(session, srv)
    assert now2 is not None

    jobs_mod._execute_os_update_check(1, 99999, 1)
    jobs_mod._execute_container_update_check(1, 99999, 1)
    monkeypatch.setattr(
        jobs_mod.os_patching,
        "check_os_updates",
        lambda s: (_ for _ in ()).throw(RuntimeError("apt")),
    )
    jobs_mod.enqueue_os_update_check(srv.id)

    # cancel
    pend = Job(server_id=srv.id, job_type="os_patch", status="pending", details="{}")
    session.add(pend)
    session.commit()
    session.refresh(pend)
    monkeypatch.setattr(jobs_mod.os_patching, "_append_os_log", lambda *a, **k: None)
    cancelled = jobs_mod.cancel_job(session, pend, user_id=1, message="stop")
    assert cancelled.status == "cancelled"
    with pytest.raises(jobs_mod.JobNotCancellable):
        jobs_mod.cancel_job(session, cancelled)
    bak = Job(
        server_id=srv.id,
        job_type="backup",
        status="running",
        details="{}",
        celery_task_id="abc",
    )
    session.add(bak)
    session.commit()
    session.refresh(bak)
    monkeypatch.setattr("app.services.backup.stop_backup", lambda *a, **k: None)
    monkeypatch.setattr(jobs_mod, "_revoke_celery_task", lambda *a, **k: None)
    jobs_mod.cancel_job(session, bak)

    # orphan web jobs (leave celery backup)
    web = Job(server_id=srv.id, job_type="service_migrate", status="running", details="{}")
    cel = Job(server_id=srv.id, job_type="backup", status="running", details="{}", celery_task_id="x")
    session.add(web)
    session.add(cel)
    session.commit()
    n = jobs_mod.cleanup_orphan_web_jobs(session)
    assert n >= 1
    session.refresh(cel)
    assert cel.status == "running"

    # demo create_job_and_run
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: True)
    bg = BackgroundTasks()
    demo = jobs_mod.create_job_and_run(bg, session, srv, "os_patch", user_id=None)
    assert demo.status == "success"
    demo_b = jobs_mod.create_job_and_run(
        bg, session, srv, "backup", user_id=None, source_filter="/home/pi/docker"
    )
    assert demo_b.status == "success"

    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    active = Job(server_id=srv.id, job_type="os_patch", status="running", details="{}")
    session.add(active)
    session.commit()
    with pytest.raises(jobs_mod.JobAlreadyActive):
        jobs_mod.create_job_and_run(bg, session, srv, "os_patch")


# ---------------------------------------------------------------------------
# docker_versions SFTP + drafts
# ---------------------------------------------------------------------------


def test_docker_versions_sftp_and_drafts(monkeypatch):
    from app.services import docker_versions as dv

    session, engine = _memory()
    srv = _server(session)
    monkeypatch.setattr(dv, "engine", engine)

    sftp = MemSFTP(
        {
            "/home/pi/docker/grafana/docker-compose.yml": "services: {}\n",
            "/home/pi/docker/grafana/.env": "X=1\n",
        }
    )
    ssh = Ssh(sftp)
    monkeypatch.setattr(dv, "get_ssh_client", lambda *a, **k: ssh)
    monkeypatch.setattr(dv, "run_command", _smart_run)

    names = dv._discover_project_filenames(srv, "/home/pi/docker/grafana")
    assert "docker-compose.yml" in names or names
    files = dv.get_project_live_files(
        srv, "/home/pi/docker/grafana", filenames=["docker-compose.yml", "missing.yml"]
    )
    assert "docker-compose.yml" in files
    ok, err = dv.write_project_files(
        srv,
        "/home/pi/docker/grafana",
        {"docker-compose.yml": "services:\n  web: {}\n", "__piherder__": "{}"},
    )
    assert ok is True
    bad, msg = dv.write_project_files(srv, "/x", {})
    assert bad is False
    bad2, _ = dv.write_project_files(srv, "/x", {"../etc": "x"})
    assert bad2 is False

    d1 = dv.save_draft_version(srv.id, "grafana", {"docker-compose.yml": "a"}, session)
    assert d1.version == 1 and d1.is_draft
    d1b = dv.save_draft_version(
        srv.id, "grafana", {"docker-compose.yml": "b"}, session, update_existing_draft_id=d1.id
    )
    assert d1b.id == d1.id
    d2 = dv.save_draft_version(srv.id, "grafana", {"docker-compose.yml": "c"}, session)
    assert d2.version == 2
    vers = dv.get_versions(srv.id, "grafana", session=session)
    assert len(vers) >= 2
    dv.prune_old_versions(srv.id, "grafana", session, keep=1)
    assert len(dv.get_versions(srv.id, "grafana", session=session)) == 1

    monkeypatch.setattr(
        "app.services.docker_management.redeploy_project", lambda *a, **k: {"success": True}
    )
    latest = dv.get_versions(srv.id, "grafana", session=session)[0]
    deployed = dv.deploy_version(srv.id, latest.id, srv, "/home/pi/docker/grafana", session)
    assert deployed is True
    assert dv.deploy_version(srv.id, 99999, srv, "/x", session) is False

    monkeypatch.setattr("app.services.ssh.docker_base_expanded", lambda s: "/home/pi/docker")
    created = dv.create_new_docker_project(
        srv, "newapp", {"docker-compose.yml": "services: {}\n"}, git_url="https://example/repo.git"
    )
    assert created is True


# ---------------------------------------------------------------------------
# container + OS patching helpers / mocked SSH update
# ---------------------------------------------------------------------------


def test_container_and_os_patch_helpers(monkeypatch):
    from app.services import container_patching as cp
    from app.services import os_patching as op

    assert cp.container_patch_succeeded(None) is False
    assert cp.container_patch_succeeded({"failed": []}) is True
    assert "Failed" in cp.summarize_container_patch({"error": "x"})
    assert "updated" in cp.summarize_container_patch(
        {"updated": ["a"], "failed": ["b"], "projects_checked": ["a", "b"]}
    )
    assert cp.find_compose_file("/x") is None
    cp.clear_container_patch_progress("h")
    empty = cp.get_container_patch_progress("h")
    assert empty["done"] is False
    cp.init_container_patch_progress("h", "go")
    for i in range(50):
        cp.append_container_log("h", f"line {i}")
    cp.append_container_log("h", "")
    prog = cp.get_container_patch_progress("h")
    assert prog["total_lines"] > 0
    cp.mark_container_patch_done("h", True)
    assert cp.get_container_patch_progress("h")["done"] is True

    srv = SimpleNamespace(
        hostname="pi.local",
        name="pi",
        docker_base_dir="/home/pi/docker",
        ssh_username="pi",
        get_excluded_projects=lambda: ["skipme"],
    )
    ssh = Ssh()
    monkeypatch.setattr(cp, "get_ssh_client", lambda *a, **k: ssh)
    monkeypatch.setattr(cp, "run_command", _smart_run)
    monkeypatch.setattr("app.services.ssh.docker_base_expanded", lambda s: "/home/pi/docker")
    projects = cp.discover_projects(srv)
    assert "grafana" in projects
    monkeypatch.setattr(cp, "get_ssh_client", lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    err = cp.discover_projects(srv)
    assert err and str(err[0]).startswith("ERROR")

    monkeypatch.setattr(cp, "get_ssh_client", lambda *a, **k: ssh)
    res = cp.run_project_update(srv, project="grafana")
    assert "projects_checked" in res
    # discover error path
    monkeypatch.setattr(cp, "discover_projects", lambda s: ["ERROR: x"])
    bad = cp.run_project_update(srv, None)
    assert bad.get("error")

    # check_project_images via classify
    monkeypatch.setattr(
        "app.services.docker_management.classify_compose_images",
        lambda c, p: {"pullable_images": ["grafana/grafana:11"], "build_services": []},
    )
    ids = {"n": 0}

    def run2(client, cmd, timeout=15):
        if "ls " in str(cmd):
            return 0, "docker-compose.yml\n", ""
        if "image inspect" in str(cmd) or "inspect --format" in str(cmd):
            ids["n"] += 1
            return 0, f"id{ids['n']}\n", ""
        if "compose pull" in str(cmd):
            return 0, "Pulled\n", ""
        return _smart_run(client, cmd, timeout)

    monkeypatch.setattr(cp, "run_command", run2)
    monkeypatch.setattr("app.services.docker_management._image_id_remote", lambda c, img: f"id-{ids['n']}")
    chk = cp.check_project_images(ssh, "/home/pi/docker/grafana")
    assert chk["has_compose"] is True

    monkeypatch.setattr(cp, "run_command", lambda c, cmd, timeout=15: (0, "", ""))
    empty = cp.check_project_images(ssh, "/none")
    assert empty["has_compose"] is False

    monkeypatch.setattr(cp, "discover_projects", lambda s: ["grafana", "ERROR: x"])
    monkeypatch.setattr(cp, "get_ssh_client", lambda *a, **k: ssh)
    monkeypatch.setattr(cp, "check_project_images", lambda c, d: {"has_compose": True, "has_updates": True, "updated_images": ["img"]})
    fleet = cp.check_all_projects_updates(srv)
    assert "grafana" in fleet["projects_with_updates"]

    # OS helpers
    assert op.normalize_os_patch_steps(None) == ["update", "upgrade", "autoremove"]
    assert "full-upgrade" not in op.normalize_os_patch_steps(["update", "upgrade", "full-upgrade"])
    assert op.os_patch_succeeded(None) is False
    assert op.os_patch_succeeded({"results": [{"step": "update", "rc": 0}]}) is True
    assert op.os_patch_succeeded({"results": [{"step": "update", "rc": 1}]}) is False
    assert "reboot" in op.summarize_os_patch_result(
        {"results": [{"step": "update", "rc": 0}], "needs_reboot": True}
    )
    assert "Failed" in op.summarize_os_patch_result({"error": "apt"})
    empty_p = op.get_os_patch_progress("missing")
    assert empty_p["done"] is False
    op.init_os_patch_progress("pi", "go")
    op._append_os_log("pi", "line one\nline two")
    op._append_os_log("pi", "\rprogress 50%", replace_progress=True)
    op.mark_os_patch_done("pi", True)
    assert op.get_os_patch_log_tail("pi")
    attached = op.attach_audit_fields(
        {"summary": "ok"},
        "pi",
        post_check={"updates_count": 0, "phased_count": 1, "reboot_pending": True},
    )
    assert attached["post_check"]["phased_count"] == 1
    op.clear_os_patch_progress("pi")
    assert op._parse_upgradable_list("Listing...\nfwupd/stable 1.0 arm64 [upgradable from: 0.9]\n")
    assert op._parse_sim_upgrade_inst("Inst linux [1] (2 amd64)\nConf linux")


# ---------------------------------------------------------------------------
# diagnostics + host_deps
# ---------------------------------------------------------------------------


def test_diagnostics_and_host_deps(monkeypatch):
    from app.services import diagnostics as diag
    from app.services import host_deps as hd

    sample = (
        "Filesystem Size Used Avail Use% Mounted on\n"
        "/dev/sda1 30G 10G 18G 36% /\n"
        "tmpfs 1G 0 1G 0% /run\n"
        "/dev/sdb1 100G 40G 60G 40% /home\n"
    )
    drives = diag.parse_df_h_output(sample)
    assert any(d["target"] == "/" for d in drives)
    summary = diag.summarize_usable_space(drives)
    assert summary["root"]["target"] == "/"
    assert diag.summarize_usable_space([])["root"] is None

    srv = SimpleNamespace(
        id=7,
        hostname="pi.local",
        name="pi",
        os_type="debian",
        ssh_username="pi",
        backup_enabled=True,
        container_patch_enabled=True,
        os_patch_enabled=True,
    )
    ssh = Ssh()
    monkeypatch.setattr(diag, "get_ssh_client", lambda *a, **k: ssh)
    monkeypatch.setattr(diag, "run_command", _smart_run)
    monkeypatch.setattr(diag, "ping", lambda h, timeout=2: True)
    monkeypatch.setattr("app.services.haos.is_haos_server", lambda s: False)
    monkeypatch.setattr("app.services.haos.probe_haos_identity", lambda c: {"is_haos": False})
    diag.clear_diagnostics_cache()
    info = diag.run_diagnostics(srv, force=True)
    assert info["kernel"]
    assert info["reboot_pending"] is True
    cached = diag.run_diagnostics(srv, force=False)
    assert cached["kernel"] == info["kernel"]
    diag.clear_diagnostics_cache(7)
    diag.clear_diagnostics_cache()

    assert hd.parse_host_deps(SimpleNamespace(host_deps_json=None)) is None
    assert hd.parse_host_deps(SimpleNamespace(host_deps_json="{")) is None
    assert hd.parse_host_deps(SimpleNamespace(host_deps_json='{"ok":true}'))["ok"] is True
    assert hd.overall_from_checks([{"status": "ok", "required": True}]) == "ok"
    assert hd.overall_from_checks([{"status": "fail", "required": True}]) == "fail"
    assert hd.overall_from_checks([{"status": "fail", "required": False}]) == "warn"
    assert hd.overall_from_checks([{"status": "warn", "required": True}]) == "warn"

    monkeypatch.setattr(hd, "get_ssh_client", lambda *a, **k: ssh)
    monkeypatch.setattr(hd, "run_command", _smart_run)
    monkeypatch.setattr("app.services.haos.probe_haos_identity", lambda c: {"is_haos": False})
    result = hd.run_host_deps_check(srv)
    assert result["overall"] in ("ok", "warn", "fail")
    ids = {c["id"] for c in result["checks"]}
    assert "ssh" in ids and "docker" in ids and "rsync" in ids

    session, _ = _memory()
    row = _server(session, backup_enabled=True)
    persisted = hd.persist_host_deps(session, row, result)
    session.commit()
    assert persisted.host_deps_json
    hd.persist_host_deps(session, row, {"checked_at": "not-a-date", "checks": []})

    # SSH fail path
    monkeypatch.setattr(hd, "get_ssh_client", lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    fail = hd.run_host_deps_check(srv)
    assert fail["overall"] == "fail"

    # features off
    off = SimpleNamespace(
        hostname="x",
        ssh_username="root",
        os_type="debian",
        backup_enabled=False,
        container_patch_enabled=False,
        os_patch_enabled=False,
    )
    monkeypatch.setattr(hd, "get_ssh_client", lambda *a, **k: ssh)
    skipped = hd.run_host_deps_check(off)
    assert any(c["id"] == "docker" and c["status"] == "skip" for c in skipped["checks"])

    ha = SimpleNamespace(
        hostname="ha",
        ssh_username="root",
        os_type="haos",
        backup_enabled=True,
        container_patch_enabled=True,
        os_patch_enabled=True,
    )
    ha_res = hd.run_host_deps_check(ha)
    assert any(c["id"] in ("ha_cli", "docker") for c in ha_res["checks"])


# ---------------------------------------------------------------------------
# catalog save / delete / zip import
# ---------------------------------------------------------------------------


def test_catalog_save_delete_and_zip(tmp_path, monkeypatch):
    from app.services.service_templates import catalog as cat
    from app.services.service_templates.schema import TemplateDefinition, TemplateError
    from app.config import settings

    session, _ = _memory()
    definition = TemplateDefinition(
        schema_version=1,
        slug="cov-demo",
        name="Coverage Demo",
        description="unit",
        category="other",
        version="1.0.0",
        file_contents={"docker-compose.yml": "services: {}\n"},
        source="user",
    )
    row = cat.save_template_definition(session, definition, mark_user=True)
    assert row.slug == "cov-demo"
    row2 = cat.save_template_definition(session, definition, template_id=row.id, mark_user=True)
    assert row2.id == row.id
    cat.delete_template(session, slug="cov-demo")
    with pytest.raises(TemplateError):
        cat.delete_template(session, slug="nope")

    monkeypatch.setattr(settings, "DATA_ROOT", str(tmp_path))
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w") as zf:
        zf.writestr(
            "mypkg/template.yaml",
            "schema_version: 1\nslug: zip-demo\nname: Zip Demo\ncategory: other\nversion: '1.0.0'\n"
            "files:\n  - docker-compose.yml\n",
        )
        zf.writestr("mypkg/files/docker-compose.yml", "services: {}\n")
    imported = cat.import_template_from_zip_bytes(session, zbuf.getvalue())
    assert imported.slug == "zip-demo"
    with pytest.raises(TemplateError):
        cat.import_template_from_zip_bytes(session, b"")
    with pytest.raises(TemplateError):
        cat.import_template_from_zip_bytes(session, b"not-a-zip")
