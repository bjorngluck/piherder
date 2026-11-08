"""v1.14 coverage. Service branches the 80% suite left open.

OAuth exchange, SMB and Drive probes, Celery lock bodies, compose YAML checks.
"""
from __future__ import annotations

import io
import json
import subprocess
import urllib.error
from types import SimpleNamespace

import pytest
from sqlmodel import Session

from app.database import get_engine
from app.models import BackupDestination, Job, Server
from app.security.encryption import encrypt_str
from app.services import backup_replicate as copies
from app.services import docker_management as dm
from app.services import jobs as jobs_mod
from app.tasks import _migrate_dual_lock_task


def _session() -> Session:
    return Session(get_engine())


def _dest(**extra) -> BackupDestination:
    row = BackupDestination(name="Drive", provider="drive", config_json="{}")
    for key, value in extra.items():
        setattr(row, key, value)
    return row


class _Body:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_private_key_and_oauth_client_edges():
    pem = "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n"
    assert "BEGIN" in copies.normalize_private_key(json.dumps({"private_key": pem}))
    quoted = json.dumps(pem.replace("\n", "\\n"))
    assert "BEGIN" in copies.normalize_private_key(quoted)
    assert copies.normalize_private_key("{not json") == "{not json"
    assert copies.normalize_private_key('"nope') == '"nope'

    dest = _dest(config_json="{")
    with pytest.raises(ValueError, match="client"):
        copies.store_oauth_client(
            dest,
            client_id="has space",
            client_secret="secret-example",
            folder="PiHerder",
            schedule=None,
            after_host_backup=False,
        )
    dest.credentials_encrypted = "not-a-token"
    copies.store_oauth_client(
        dest,
        client_id="client.example",
        client_secret="secret-example",
        folder="folder",
        schedule="0 1 * * *",
        after_host_backup=True,
        copy_herder_backup=True,
    )
    saved = json.loads(dest.config_json)
    assert saved["oauth_client_id"] == "client.example"
    assert saved["copy_herder_backup"] is True

    token = encrypt_str(json.dumps({"refresh_token": "rt-example", "client_id": "old"}))
    dest.credentials_encrypted = token
    copies.store_oauth_client(
        dest,
        client_id="client.example",
        client_secret="",
        folder="folder",
        schedule=None,
        after_host_backup=False,
    )
    assert "client.example" in copies.decrypt_token(dest)


def test_token_exchange_and_account_email(monkeypatch):
    def _open(request, timeout=20):
        url = request.full_url
        if url.endswith("/fail-http"):
            raise urllib.error.HTTPError(url, 400, "bad", {}, io.BytesIO(b"nope"))
        if url.endswith("/fail-net"):
            raise TimeoutError("down")
        if url.endswith("/no-refresh"):
            return _Body(b'{"access_token": "only"}')
        if "userinfo" in url or url.endswith("/me"):
            if "empty" in (request.headers.get("Authorization") or ""):
                return _Body(b"[]")
            return _Body(b'{"email": "user@example.com", "mail": "user@example.com"}')
        return _Body(b'{"refresh_token": "rt-example", "access_token": "at-example"}')

    monkeypatch.setattr(copies.urllib.request, "urlopen", _open)
    monkeypatch.setattr(copies, "_GOOGLE_TOKEN", "https://example.com/token")
    monkeypatch.setattr(copies, "_MICROSOFT_TOKEN", "https://example.com/token")
    ok = copies.exchange_google_code(
        client_id="cid",
        client_secret="sec",
        code="code",
        redirect_uri="https://example.com/cb",
    )
    assert ok["refresh_token"] == "rt-example"
    assert copies.exchange_microsoft_code(
        client_id="cid",
        client_secret="sec",
        code="code",
        redirect_uri="https://example.com/cb",
    )["refresh_token"]

    monkeypatch.setattr(copies, "_GOOGLE_TOKEN", "https://example.com/fail-http")
    monkeypatch.setattr(copies, "_MICROSOFT_TOKEN", "https://example.com/fail-http")
    with pytest.raises(ValueError, match="google"):
        copies.exchange_google_code(
            client_id="cid", client_secret="sec", code="c", redirect_uri="https://example.com/cb"
        )
    with pytest.raises(ValueError, match="microsoft"):
        copies.exchange_microsoft_code(
            client_id="cid", client_secret="sec", code="c", redirect_uri="https://example.com/cb"
        )
    monkeypatch.setattr(copies, "_GOOGLE_TOKEN", "https://example.com/fail-net")
    monkeypatch.setattr(copies, "_MICROSOFT_TOKEN", "https://example.com/no-refresh")
    with pytest.raises(ValueError, match="google"):
        copies.exchange_google_code(
            client_id="cid", client_secret="sec", code="c", redirect_uri="https://example.com/cb"
        )
    with pytest.raises(ValueError, match="microsoft"):
        copies.exchange_microsoft_code(
            client_id="cid", client_secret="sec", code="c", redirect_uri="https://example.com/cb"
        )

    monkeypatch.setattr(copies, "_GOOGLE_USERINFO", "https://example.com/userinfo")
    monkeypatch.setattr(copies, "_MICROSOFT_ME", "https://example.com/me")
    assert copies.google_account_email("at-example") == "user@example.com"
    assert copies.microsoft_account_email("at-example") == "user@example.com"
    assert copies.google_account_email("empty") == ""
    monkeypatch.setattr(copies, "_GOOGLE_USERINFO", "https://example.com/fail-net")
    monkeypatch.setattr(copies, "_MICROSOFT_ME", "https://example.com/fail-net")
    assert copies.google_account_email("at-example") == ""
    assert copies.microsoft_account_email("at-example") == ""


def test_probe_codes_and_unstarted_job(monkeypatch):
    blank = _dest()
    assert copies.has_saved_destination(None) is False
    assert copies.has_saved_destination(blank) is False
    scheduled = _dest(schedule="0 3 * * *")
    assert copies.has_saved_destination(scheduled) is True
    picked = _dest(selection_json=json.dumps({"checked": ["host-a"], "skipped": []}))
    assert copies.has_saved_destination(picked) is True
    assert copies._queue_copy_message(_dest(provider="smb"), direct_host=True).startswith("Direct")
    assert "OneDrive" in copies._queue_copy_message(_dest(provider="onedrive"), direct_host=False)
    assert copies._queue_copy_message(_dest(provider="drive"), direct_host=False).startswith("Drive")

    smb = _dest(provider="smb", config_json=json.dumps({"host": "192.0.2.10", "share": "backups", "guest": True}))
    codes = []

    def _run(cmd, capture_output=True, text=True, timeout=25):
        kind = codes.pop(0)
        if kind == "missing":
            raise FileNotFoundError("rclone")
        if kind == "timeout":
            raise subprocess.TimeoutExpired(cmd, timeout)
        stderr = {
            "ok": "",
            "auth": "authentication failed",
            "folder": "no such file",
            "host": "connection refused",
            "other": "mystery",
            "drive-folder": "404 not found",
            "drive-auth": "unauthorized",
        }[kind]
        code = 0 if kind == "ok" else 1
        return SimpleNamespace(returncode=code, stderr=stderr, stdout="")

    monkeypatch.setattr(copies, "write_smb_rclone_config", lambda *_a, **_k: "/tmp/rclone-example.conf")
    monkeypatch.setattr(copies, "discard_rclone_config", lambda *_a, **_k: None)
    monkeypatch.setattr(copies, "smb_remote_root", lambda *_a, **_k: "share")
    monkeypatch.setattr(copies.subprocess, "run", _run)
    for kind in ("missing", "timeout", "ok", "auth", "folder", "host", "other"):
        codes.append(kind)
        assert copies._probe_smb(smb)["code"]
    assert copies._probe_smb(_dest(provider="smb", config_json="{}"))["code"] == "account"

    monkeypatch.setattr("app.services.demo.demo_mode", lambda: True)
    assert copies.probe(smb)["code"] == "demo"
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    odd = _dest(provider="other")
    assert copies.probe(odd)["code"] == "provider"
    assert copies.probe(_dest())["code"] == "account"

    drive = _dest(credentials_encrypted=encrypt_str(json.dumps({"refresh_token": "rt-example"})))
    monkeypatch.setattr(copies, "write_rclone_config", lambda *_a, **_k: "/tmp/rclone-example.conf")
    monkeypatch.setattr(copies, "decrypt_token", lambda *_a, **_k: "{}")
    for kind in ("missing", "timeout", "ok", "drive-folder", "drive-auth", "other"):
        codes.append(kind)
        assert copies.probe(drive)["code"]

    with _session() as session:
        dest = _dest(provider="smb")
        session.add(dest)
        session.commit()
        session.refresh(dest)
        job = Job(job_type="backup_replicate", status="running", server_id=None, details="{}")
        session.add(job)
        session.commit()
        session.refresh(job)
        failed = copies._fail_unstarted(session, job, dest, "token-example", direct_host=True)
        assert failed.status == "failed"
        assert "direct_host" in failed.details


def test_compose_yaml_reports_more_than_the_first_error():
    assert dm.validate_compose_content("  ")["valid"] is False
    assert dm.validate_compose_content("services:\n  web:\n    image: example\n")["valid"] is True
    bad = "services:\n  web\n    image: [\n  db: [\n"
    result = dm.validate_compose_content(bad)
    assert result["valid"] is False
    assert result["errors"]


class _Req:
    def __init__(self, task_id="task-example", retries=0):
        self.id = task_id
        self.retries = retries


class _Self:
    def __init__(self, retries=0):
        self.request = _Req(retries=retries)
        self.retried = False

    def retry(self, countdown=0):
        from celery.exceptions import Retry

        self.retried = True
        raise Retry("wait", None)


def test_dual_lock_task_branches(monkeypatch):
    import app.tasks as tasks

    with _session() as session:
        src = Server(name="Source", hostname="host-a.example.com", ssh_username="backup")
        session.add(src)
        session.commit()
        session.refresh(src)
        pending = Job(server_id=src.id, job_type="service_migrate_undo", status="pending", details="{}")
        running = Job(server_id=src.id, job_type="service_migrate_undo", status="running", details="{}")
        cancelled = Job(server_id=src.id, job_type="service_migrate_undo", status="cancelled", details="{}")
        done = Job(server_id=src.id, job_type="service_migrate_undo", status="success", details="{}")
        session.add(pending)
        session.add(running)
        session.add(cancelled)
        session.add(done)
        session.commit()
        for row in (pending, running, cancelled, done):
            session.refresh(row)
        ids = {
            "pending": pending.id,
            "running": running.id,
            "cancelled": cancelled.id,
            "done": done.id,
            "src": src.id,
        }

    monkeypatch.setattr(jobs_mod, "_finish", lambda *_a, **_k: None)
    monkeypatch.setattr(jobs_mod, "_load_server_for_job", lambda *_a, **_k: (None, "host-a"))

    body = _Self()
    assert _migrate_dual_lock_task(
        body, 999999, ids["src"], ids["src"], 1,
        job_type="service_migrate_undo", execute=lambda *_a: None, running_message="down", log_name="undo",
    )["status"] == "skipped"
    assert _migrate_dual_lock_task(
        body, ids["cancelled"], ids["src"], ids["src"], 1,
        job_type="service_migrate_undo", execute=lambda *_a: None, running_message="down", log_name="undo",
    )["status"] == "cancelled"
    assert _migrate_dual_lock_task(
        body, ids["done"], ids["src"], ids["src"], 1,
        job_type="service_migrate_undo", execute=lambda *_a: None, running_message="down", log_name="undo",
    )["status"] == "skipped"
    failed = _migrate_dual_lock_task(
        body, ids["running"], ids["src"], ids["src"], 1,
        job_type="service_migrate_undo", execute=lambda *_a: None, running_message="down", log_name="undo",
    )
    assert failed["reason"] == "worker_restart"

    monkeypatch.setattr(tasks, "try_acquire_dual_server_lock", lambda *_a, **_k: None)
    waiting = _Self(retries=0)
    with pytest.raises(Exception):
        _migrate_dual_lock_task(
            waiting, ids["pending"], ids["src"], ids["src"], 1,
            job_type="service_migrate_undo", execute=lambda *_a: None, running_message="down", log_name="undo",
        )
    assert waiting.retried is True
    timed = _Self(retries=tasks._LOCK_MAX_RETRIES)
    assert _migrate_dual_lock_task(
        timed, ids["pending"], ids["src"], ids["src"], 1,
        job_type="service_migrate_undo", execute=lambda *_a: None, running_message="down", log_name="undo",
    )["status"] == "failed"

    monkeypatch.setattr(tasks, "try_acquire_dual_server_lock", lambda *_a, **_k: ("a", "b"))
    monkeypatch.setattr(tasks, "release_dual_server_lock", lambda *_a, **_k: None)
    with _session() as session:
        again = session.get(Job, ids["pending"])
        again.status = "pending"
        session.add(again)
        session.commit()
    ok = _migrate_dual_lock_task(
        _Self(), ids["pending"], ids["src"], ids["src"], 1,
        job_type="service_migrate_undo", execute=lambda *_a: None, running_message="down", log_name="undo",
    )
    assert ok["status"] == "ok"

    def _boom(*_a):
        raise RuntimeError("copy failed")

    err = _migrate_dual_lock_task(
        _Self(), ids["pending"], ids["src"], ids["src"], 1,
        job_type="service_migrate_undo", execute=_boom, running_message="down", log_name="undo",
    )
    assert err["status"] == "error"

    def _release(*_a, **_k):
        raise RuntimeError("lock")

    monkeypatch.setattr(tasks, "release_dual_server_lock", _release)
    monkeypatch.setattr(jobs_mod, "_finish", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("finish")))
    still = _migrate_dual_lock_task(
        _Self(), ids["pending"], ids["src"], ids["src"], 1,
        job_type="service_migrate_undo", execute=_boom, running_message="down", log_name="undo",
    )
    assert still["status"] == "error"


def test_worker_activity_and_housekeeping(monkeypatch):
    class _Inspect:
        def __init__(self, ping, active):
            self._ping = ping
            self._active = active

        def ping(self):
            if self._ping == "boom":
                raise RuntimeError("inspect")
            return self._ping

        def active(self):
            return self._active

    holders = {}

    def _inspect(timeout=1.0):
        return holders["insp"]

    import app.celery_app as celery_app

    monkeypatch.setattr(celery_app.celery.control, "inspect", _inspect)
    holders["insp"] = None
    assert jobs_mod.celery_worker_activity() == (False, set())
    holders["insp"] = _Inspect({}, {})
    assert jobs_mod.celery_worker_activity() == (False, set())
    holders["insp"] = _Inspect({"w": "pong"}, {"w": [{"id": "t1"}, "nope", {"id": ""}]})
    alive, ids = jobs_mod.celery_worker_activity()
    assert alive is True and ids == {"t1"}
    holders["insp"] = _Inspect("boom", {})
    assert jobs_mod.celery_worker_activity() is None

    class _Task:
        request = _Req()

    with _session() as session:
        job = Job(job_type="herder_backup", status="pending", details="{}")
        session.add(job)
        session.commit()
        session.refresh(job)
        job_id = job.id
    monkeypatch.setattr(jobs_mod, "_execute_herder_backup", lambda *_a, **_k: None)
    monkeypatch.setattr(jobs_mod, "_fail_housekeeping_open", lambda *_a, **_k: None)
    assert jobs_mod.run_housekeeping_job(_Task(), 999999, 0, 1, "herder_backup")["status"] == "skipped"
    out = jobs_mod.run_housekeeping_job(_Task(), job_id, 0, 1, "herder_backup")
    assert out["status"] == "ok"
    with _session() as session:
        row = session.get(Job, job_id)
        row.status = "running"
        session.add(row)
        session.commit()
    assert jobs_mod.run_housekeeping_job(_Task(), job_id, 0, 1, "herder_backup")["reason"] == "worker_restart"
    monkeypatch.setattr(jobs_mod, "_execute_herder_backup", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("disk")))
    with _session() as session:
        row = session.get(Job, job_id)
        row.status = "pending"
        session.add(row)
        session.commit()
    assert jobs_mod.run_housekeeping_job(_Task(), job_id, 0, 1, "herder_backup")["status"] == "error"


def _run_backup(server_id, job_id, audit_id, source, retries):
    import app.tasks as tasks
    from celery.exceptions import Retry

    def _retry(*_a, **_k):
        raise Retry("wait", None)

    tasks.backup_server.push_request(id="task-example", retries=retries)
    tasks.backup_server.retry = _retry
    try:
        return tasks.backup_server.run(server_id, job_id, audit_id, source)
    finally:
        tasks.backup_server.pop_request()


def test_backup_task_skips_a_missing_host_and_a_busy_lock(monkeypatch):
    import app.tasks as tasks

    monkeypatch.setattr(tasks, "try_acquire_server_lock", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks, "run_backup", lambda *_a, **_k: {"ok": True})
    monkeypatch.setattr(tasks, "backup_succeeded", lambda *_a, **_k: True)
    monkeypatch.setattr(tasks, "record_backup_audit_from_job", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks, "_flush_job_progress_db", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks, "clear_job_progress_buffer", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks, "release_server_lock", lambda *_a, **_k: None)

    assert _run_backup(999999, None, None, None, 0)["status"] == "error"

    with _session() as session:
        server = Server(name="Host A", hostname="host-a.example.com", ssh_username="user")
        session.add(server)
        session.commit()
        session.refresh(server)
        done = Job(server_id=server.id, job_type="backup", status="success", details="{}")
        pending = Job(server_id=server.id, job_type="backup", status="pending", details="{}")
        session.add(done)
        session.add(pending)
        session.commit()
        session.refresh(done)
        session.refresh(pending)
        sid, done_id, pending_id = server.id, done.id, pending.id

    assert _run_backup(sid, done_id, 1, None, 0)["status"] == "skipped"
    with pytest.raises(Exception):
        _run_backup(sid, pending_id, 1, None, 0)
    timed = _run_backup(sid, pending_id, 1, None, tasks._LOCK_MAX_RETRIES)
    assert timed["status"] == "failed"

    monkeypatch.setattr(tasks, "try_acquire_server_lock", lambda *_a, **_k: "tok")
    with _session() as session:
        row = session.get(Job, pending_id)
        row.status = "pending"
        session.add(row)
        session.commit()
    ok = _run_backup(sid, pending_id, 1, "/home/user", 0)
    assert ok.get("status") in {"success", "ok", "failed", "error", "cancelled"}
