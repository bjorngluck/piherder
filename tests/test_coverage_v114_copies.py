"""v1.14 coverage for backup copy routes and replicate gaps.

SQLite only. OAuth and drive probes use a stand-in for urllib. rclone is not run.
"""
from __future__ import annotations

import io
import json
import subprocess
import urllib.error
from types import SimpleNamespace
from urllib.parse import urlparse as real_urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.config import settings
from app.database import get_session
from app.main import app
from app.models import BackupDestination, Job, User
from app.routers import backup_copies as routes
from app.security.auth import create_access_token, create_user_access_token, decode_token_payload, get_password_hash
from app.security.encryption import decrypt_str
from app.services import backup_replicate as copies

_PASSWORD = get_password_hash("SmokeTest1ok")
_ARCHIVE_A = "piherder-20261027-120000-full.tar.gz"
_ARCHIVE_B = "piherder-20261027-120100-full.tar.gz"
_ARCHIVE_C = "piherder-20261027-120200-full.tar.gz"
_ARCHIVE_D = "piherder-20261027-120300-full.tar.gz"


def _engine(path):
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _assign(obj, name, value):
    try:
        setattr(obj, name, value)
    except Exception:
        object.__setattr__(obj, name, value)


class _Body:
    def __init__(self, payload):
        self._payload = payload if isinstance(payload, bytes) else payload.encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _loc(response):
    assert response.status_code in (200, 303), response.text[:300]
    return response.headers.get("location", "")


def _cookie(response, name):
    direct = response.cookies.get(name)
    if direct:
        return direct
    for header in response.headers.get_list("set-cookie"):
        bit = header.split(";", 1)[0]
        if bit.startswith(name + "="):
            return bit.split("=", 1)[1]
    return ""


@pytest.fixture
def api(tmp_path, monkeypatch):
    engine = _engine(tmp_path / "copies.db")
    monkeypatch.setattr("app.database.engine", engine)
    from app.services import app_settings as app_cfg

    monkeypatch.setattr(app_cfg, "_cache", None)
    old_public = getattr(settings, "PIHERDER_PUBLIC_URL", None)
    old_secure = getattr(settings, "COOKIE_SECURE", None)
    _assign(settings, "PIHERDER_PUBLIC_URL", "http://host-a.example.com")
    _assign(settings, "COOKIE_SECURE", "false")

    def _session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    try:
        client = TestClient(app, raise_server_exceptions=False, follow_redirects=False)
        with Session(engine) as db:
            user = User(
                email="user@example.com",
                hashed_password=_PASSWORD,
                role="admin",
                is_active=True,
                must_change_password=False,
                totp_enabled=False,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            client.cookies.set("access_token", create_user_access_token(user))
        yield client, engine
    finally:
        app.dependency_overrides.pop(get_session, None)
        _assign(settings, "PIHERDER_PUBLIC_URL", old_public)
        _assign(settings, "COOKIE_SECURE", old_secure)


def _drive_fields(**extra):
    data = {
        "provider": "drive",
        "schedule_cron": "0 4 * * *",
        "after_host_backup": "1",
        "copy_herder_backup": "1",
        "drive_folder": "PiHerder/host-a",
        "client_id": "copy-client",
        "client_secret": "copy-secret",
    }
    data.update(extra)
    return data


def test_list_toggle_run_and_test(api, tmp_path, monkeypatch):
    client, engine = api
    root = tmp_path / "backups"
    (root / "host-a" / "keep").mkdir(parents=True)
    (root / "host-a" / "notes.txt").write_text("hi", encoding="utf-8")
    monkeypatch.setattr(copies, "backup_root", lambda: root)
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)

    missing = client.get("/backup-copies/list", params={"provider": "drive"})
    assert missing.status_code == 400
    assert missing.json()["ok"] is False

    toggled = client.post("/backup-copies/toggle", json={"provider": "drive", "path": "host-a", "on": True})
    assert toggled.status_code == 400
    assert "not saved" in toggled.json()["error"]

    assert "copy_error=missing" in _loc(client.post("/backup-copies/test", data={"provider": "drive"}))
    assert "copy_provider=onedrive" in _loc(client.post("/backup-copies/run", data={"provider": "onedrive"}))
    bad = _loc(client.post("/backup-copies/run", data={"provider": "dropbox"}))
    assert "copy_error=provider" in bad
    assert "copy_provider=" not in bad
    assert "copy_error=provider" in _loc(client.post("/backup-copies/test", data={"provider": "dropbox"}))

    with Session(engine) as db:
        row = BackupDestination(
            provider="drive",
            name="Google Drive",
            enabled=True,
            selection_json=copies.selection_json(["host-a"], []),
            schedule="0 4 * * *",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        drive_id = row.id

    listed = client.get("/backup-copies/list", params={"p": "host-a", "provider": "drive"})
    assert listed.status_code == 200, listed.text[:500]
    body = listed.json()
    assert body["ok"] is True
    assert body["state"] == "on"
    assert body["included"] is True
    assert body["direct"] is False
    names = {item["name"] for item in body["entries"]}
    assert names == {"keep", "notes.txt"}
    assert all(item["included"] for item in body["entries"])

    root_list = client.get("/backup-copies/list", params={"p": "", "provider": "drive"})
    assert root_list.status_code == 200
    assert root_list.json()["state"] == "partial"

    escaped = client.get("/backup-copies/list", params={"p": "../etc", "provider": "drive"})
    assert escaped.status_code == 400
    assert "escapes" in escaped.json()["error"]

    def _missing(*_a, **_k):
        raise FileNotFoundError()

    monkeypatch.setattr(copies, "list_directory", _missing)
    empty = client.get("/backup-copies/list", params={"p": "host-a", "provider": "drive"})
    assert empty.status_code == 400
    assert empty.json()["error"] == "That folder was not found."

    def _boom(*_a, **_k):
        raise ValueError("off the drive")

    monkeypatch.setattr(copies, "list_directory", _boom)
    valued = client.get("/backup-copies/list", params={"p": "host-a", "provider": "nope"})
    assert valued.status_code == 400
    assert "not saved" in valued.json()["error"]
    valued = client.get("/backup-copies/list", params={"p": "host-a", "provider": "drive"})
    assert valued.json()["error"] == "off the drive"
    monkeypatch.setattr(copies, "list_directory", copies.list_directory)

    bad_json = client.post(
        "/backup-copies/toggle",
        content=b"{",
        headers={"content-type": "application/json"},
    )
    assert bad_json.status_code == 400
    assert "Choose a folder" in bad_json.json()["error"]
    empty_body = client.post(
        "/backup-copies/toggle",
        content=b"null",
        headers={"content-type": "application/json"},
    )
    assert empty_body.status_code == 400

    parent = client.post(
        "/backup-copies/toggle",
        json={"changes": "nope", "provider": "drive", "path": "host-a", "on": False},
    )
    assert parent.status_code == 200, parent.text[:400]
    assert parent.json()["checked"] == []

    mixed = client.post(
        "/backup-copies/toggle",
        json={"provider": "drive", "changes": [1, {"path": "host-a", "on": True}]},
    )
    assert mixed.status_code == 200
    assert mixed.json()["checked"] == ["host-a"]

    refused = client.post(
        "/backup-copies/toggle",
        json={"provider": "drive", "changes": [{"path": "..", "on": True}]},
    )
    assert refused.status_code == 400
    assert "escapes" in refused.json()["error"]
    with Session(engine) as db:
        saved = db.get(BackupDestination, drive_id)
        assert copies.parse_selection(saved.selection_json)[0] == ["host-a"]

    assert "copy_test=account" in _loc(client.post("/backup-copies/test", data={"provider": "drive"}))
    assert "copy_error=removed" in _loc(client.post("/backup-copies/run", data={"provider": "drive"}))

    with Session(engine) as db:
        smb = BackupDestination(provider="smb", name="LAN NAS / SMB", enabled=True)
        copies.store_smb(
            smb,
            host="192.0.2.10",
            share="backups",
            path="PiHerder",
            username="backup",
            password="copy-secret",
            domain="",
            schedule=None,
            after_host_backup=False,
        )
        db.add(smb)
        db.commit()

    class _Proc:
        def __init__(self, code, stderr):
            self.returncode = code
            self.stderr = stderr
            self.stdout = ""

    state = {"code": 0, "stderr": ""}

    def _run(cmd, **_kwargs):
        assert cmd[0:2] == ["rclone", "lsd"]
        return _Proc(state["code"], state["stderr"])

    monkeypatch.setattr(copies.subprocess, "run", _run)
    assert "copy_test=ok" in _loc(client.post("/backup-copies/test", data={"provider": "smb"}))
    state["code"] = 1
    state["stderr"] = "authentication failed"
    assert "copy_test=auth" in _loc(client.post("/backup-copies/test", data={"provider": "smb"}))

    monkeypatch.setattr(copies, "probe", lambda _dest: {"ok": "0"})
    assert "copy_test=rclone" in _loc(client.post("/backup-copies/test", data={"provider": "drive"}))
    monkeypatch.setattr(copies, "probe", lambda _dest: {"ok": "1", "code": "ok"})
    assert "copy_test=ok" in _loc(client.post("/backup-copies/test", data={"provider": "drive"}))

    with Session(engine) as db:
        drive = db.get(BackupDestination, drive_id)
        drive.credentials_encrypted = copies.encrypt_str(
            copies.pack_oauth_token(
                client_id="copy-client",
                client_secret="copy-secret",
                refresh_token="refresh-example",
                access_token="access-example",
                email="user@example.com",
                expires_in=3600,
            )
        )
        db.add(drive)
        db.commit()

    def _down(_job_id):
        raise RuntimeError("queue-down")

    monkeypatch.setattr("app.tasks.replicate_backup.delay", _down)
    queued = _loc(client.post("/backup-copies/run", data={"provider": "drive"}))
    assert "copy_error=queue-down" in queued
    with Session(engine) as db:
        job = db.exec(select(Job).where(Job.job_type == "backup_replicate")).first()
        assert job.status == "failed"
        assert json.loads(job.details)["error"] == "queue-down"

    monkeypatch.setattr(
        copies,
        "enqueue",
        lambda *_a, **_k: Job(id=12, status="failed", details="{"),
    )
    assert "copy_error=failed" in _loc(client.post("/backup-copies/run", data={"provider": "drive"}))
    monkeypatch.setattr(
        copies,
        "enqueue",
        lambda *_a, **_k: Job(id=13, status="failed", details="{}"),
    )
    assert "copy_error=failed" in _loc(client.post("/backup-copies/run", data={"provider": "drive"}))
    monkeypatch.setattr(
        copies,
        "enqueue",
        lambda *_a, **_k: Job(id=11, status="pending", details="{}"),
    )
    highlight = _loc(client.post("/backup-copies/run", data={"provider": "drive"}))
    assert highlight.endswith("/jobs?highlight=11") or "highlight=11" in highlight


def test_save_drive_form_and_commit(api, monkeypatch):
    client, engine = api
    saved = client.post("/backup-copies/config", data=_drive_fields(), follow_redirects=False)
    assert "copy_saved=1" in _loc(saved)
    assert "copy_provider=drive" in saved.headers["location"]
    with Session(engine) as db:
        row = db.exec(select(BackupDestination).where(BackupDestination.provider == "drive")).one()
        cfg = json.loads(row.config_json)
        assert cfg["remote_dir"] == "PiHerder/host-a"
        assert cfg["copy_herder_backup"] is True
        assert cfg["oauth_client_id"] == "copy-client"
        assert decrypt_str(cfg["oauth_client_secret_encrypted"]) == "copy-secret"
        assert row.schedule == "0 4 * * *"
        assert row.after_host_backup is True

    failed = client.post(
        "/backup-copies/config",
        data=_drive_fields(schedule_cron="not-a-cron"),
        follow_redirects=False,
    )
    location = _loc(failed)
    assert "copy_error=cron" in location
    assert "copy-secret" not in location
    token = _cookie(failed, routes.COPY_DRAFT_COOKIE)
    payload = decode_token_payload(token)
    assert payload["client_secret"] == "copy-secret"
    assert payload["cd"] == "1"
    draft = routes.read_copy_draft(SimpleNamespace(cookies={routes.COPY_DRAFT_COOKIE: token}))
    assert draft["client_id"] == "copy-client"
    assert routes.read_copy_draft(SimpleNamespace(cookies={})) is None
    assert routes.read_copy_draft(SimpleNamespace(cookies={routes.COPY_DRAFT_COOKIE: "nope"})) is None
    other = create_access_token({"sub": "1"})
    assert routes.read_copy_draft(SimpleNamespace(cookies={routes.COPY_DRAFT_COOKIE: other})) is None

    folder = _loc(
        client.post(
            "/backup-copies/config",
            data=_drive_fields(drive_folder="PiHerder/../../etc"),
            follow_redirects=False,
        )
    )
    assert "copy_error=folder" in folder

    def _raise(*_a, **_k):
        raise RuntimeError("schedule down")

    monkeypatch.setattr("app.services.scheduler.sync_backup_copy_schedule", _raise)
    again = _loc(
        client.post(
            "/backup-copies/config",
            data=_drive_fields(drive_folder="Other"),
            follow_redirects=False,
        )
    )
    assert "copy_saved=1" in again
    with Session(engine) as db:
        row = db.exec(select(BackupDestination).where(BackupDestination.provider == "drive")).one()
        assert json.loads(row.config_json)["remote_dir"] == "Other"

    def _weird(*_a, **_k):
        raise ValueError("weird")

    monkeypatch.setattr(copies, "store_oauth_client", _weird)
    client_error = _loc(
        client.post("/backup-copies/config", data=_drive_fields(), follow_redirects=False)
    )
    assert "copy_error=client" in client_error

    blank = BackupDestination(provider="drive")
    assert routes._apply_drive_form(
        blank,
        provider="smb",
        schedule_cron="",
        after_host_backup="",
        copy_herder_backup="",
        drive_folder="",
        client_id="",
        client_secret="",
    ) == "provider"


def _install_urlopen(monkeypatch):
    mode = {"exchange": "ok", "email": "ok"}

    def _open(request, timeout=20):
        assert timeout == 20
        url = request.full_url
        if "userinfo" in url or url.rstrip("/").endswith("/me"):
            if mode["email"] == "down":
                raise TimeoutError("down")
            if mode["email"] == "upn":
                return _Body(b'{"mail": "", "userPrincipalName": "user@example.com"}')
            if mode["email"] == "empty":
                return _Body(b"[]")
            if mode["email"] == "blank":
                return _Body(b"{}")
            return _Body(b'{"email": "user@example.com", "mail": "user@example.com"}')
        if mode["exchange"] == "http":
            raise urllib.error.HTTPError(url, 400, "bad", {}, io.BytesIO(b"nope"))
        if mode["exchange"] == "down":
            raise TimeoutError("down")
        if mode["exchange"] == "junk":
            return _Body(b"not-json")
        if mode["exchange"] == "norefresh":
            return _Body(b'{"access_token": "only"}')
        if mode["exchange"] == "list":
            return _Body(b"[]")
        assert request.get_method() == "POST"
        assert b"grant_type=authorization_code" in (request.data or b"")
        if mode["exchange"] == "noexp":
            return _Body(b'{"refresh_token": "refresh-example", "access_token": "access-example"}')
        return _Body(
            b'{"refresh_token": "refresh-example", "access_token": "access-example", "expires_in": 3600}'
        )

    monkeypatch.setattr(copies.urllib.request, "urlopen", _open)
    return mode


def _exercise_connect(client, engine, monkeypatch, kind):
    if kind == "google":
        spec = {
            "start": "/backup-copies/google/start",
            "go": "/backup-copies/google/go",
            "callback": "/backup-copies/google/callback",
            "cookie": copies.OAUTH_STATE_COOKIE,
            "provider": "drive",
            "error": "google",
            "title": "Continue to Google",
            "host": "accounts.google.com",
            "fields": _drive_fields(),
        }
    else:
        spec = {
            "start": "/backup-copies/onedrive/start",
            "go": "/backup-copies/onedrive/go",
            "callback": "/backup-copies/onedrive/callback",
            "cookie": copies.ONEDRIVE_OAUTH_STATE_COOKIE,
            "provider": "onedrive",
            "error": "microsoft",
            "title": "Continue to Microsoft",
            "host": "login.microsoftonline.com",
            "fields": _drive_fields(provider="onedrive"),
        }
    mode = _install_urlopen(monkeypatch)
    provider = spec["provider"]

    assert "copy_error=state" in _loc(client.get(spec["go"]))
    client.cookies.set(spec["cookie"], "state-1")
    early = _loc(client.get(spec["go"]))
    assert "copy_error=client" in early

    if kind == "google":
        denied = client.post(spec["start"], data=_drive_fields(provider="smb"), follow_redirects=False)
        assert "copy_error=provider" in _loc(denied)
        assert "copy_provider=drive" in denied.headers["location"]

    cron = client.post(
        spec["start"], data=_drive_fields(provider=provider, schedule_cron="nope"), follow_redirects=False
    )
    assert "copy_error=cron" in _loc(cron)
    assert "copy-secret" not in cron.headers["location"]
    assert _cookie(cron, routes.COPY_DRAFT_COOKIE)

    folder = _loc(
        client.post(
            spec["start"],
            data=_drive_fields(provider=provider, drive_folder="../secret"),
            follow_redirects=False,
        )
    )
    assert "copy_error=folder" in folder
    spaced = _loc(
        client.post(
            spec["start"],
            data=_drive_fields(provider=provider, client_id="copy client"),
            follow_redirects=False,
        )
    )
    assert "copy_error=client" in spaced

    empty = _loc(
        client.post(
            spec["start"],
            data=_drive_fields(provider=provider, client_id="", client_secret=""),
            follow_redirects=False,
        )
    )
    assert "copy_error=client" in empty

    started = client.post(spec["start"], data=spec["fields"], follow_redirects=False)
    assert spec["go"] in _loc(started)
    state = _cookie(started, spec["cookie"])
    assert state
    client.cookies.set(spec["cookie"], state)
    page = client.get(spec["go"])
    assert page.status_code == 200, page.text[:400]
    assert spec["title"] in page.text
    assert spec["host"] in page.text
    assert "&amp;" in page.text

    monkeypatch.setattr("app.services.password_reset.configured_public_origin", lambda raw=None: "")
    monkeypatch.setattr(
        "app.routers.backup_copies.urlparse",
        lambda _value: SimpleNamespace(scheme="", netloc=""),
    )
    assert "copy_error=origin" in _loc(client.get(spec["go"]))
    origin_start = _loc(client.post(spec["start"], data=spec["fields"], follow_redirects=False))
    assert "copy_error=origin" in origin_start
    monkeypatch.setattr(
        "app.services.password_reset.configured_public_origin",
        lambda raw=None: "http://host-a.example.com",
    )
    monkeypatch.setattr("app.routers.backup_copies.urlparse", real_urlparse)

    monkeypatch.setattr(
        "app.services.password_reset.configured_public_origin",
        lambda raw=None: "https://host-a.example.com",
    )
    secure = client.post(spec["start"], data=spec["fields"], follow_redirects=False)
    assert spec["go"] in _loc(secure)
    joined = " ".join(secure.headers.get_list("set-cookie")).lower()
    assert "secure" in joined
    monkeypatch.setattr(
        "app.services.password_reset.configured_public_origin",
        lambda raw=None: "http://host-a.example.com",
    )

    assert f"copy_error={spec['error']}" in _loc(client.get(spec["callback"], params={"error": "access_denied", "code": "abc"}))
    assert f"copy_error={spec['error']}" in _loc(client.get(spec["callback"]))
    client.cookies.set(spec["cookie"], "state-1")
    mismatch = _loc(client.get(spec["callback"], params={"code": "abc", "state": "state-2"}))
    assert "copy_error=state" in mismatch

    with Session(engine) as db:
        row = db.exec(select(BackupDestination).where(BackupDestination.provider == provider)).one()
        row.config_json = "{}"
        row.credentials_encrypted = None
        db.add(row)
        db.commit()
    bare = _loc(client.get(spec["callback"], params={"code": "abc", "state": "state-1"}))
    assert "copy_error=client" in bare

    with Session(engine) as db:
        row = db.exec(select(BackupDestination).where(BackupDestination.provider == provider)).one()
        copies.store_oauth_client(
            row,
            client_id="copy-client",
            client_secret="copy-secret",
            folder="PiHerder",
            schedule="0 4 * * *",
            after_host_backup=True,
            copy_herder_backup=True,
        )
        db.add(row)
        db.commit()
    monkeypatch.setattr("app.services.password_reset.configured_public_origin", lambda raw=None: "")
    monkeypatch.setattr(
        "app.routers.backup_copies.urlparse",
        lambda _value: SimpleNamespace(scheme="ftp", netloc=""),
    )
    assert "copy_error=client" in _loc(client.get(spec["callback"], params={"code": "abc", "state": "state-1"}))
    monkeypatch.setattr(
        "app.services.password_reset.configured_public_origin",
        lambda raw=None: "http://host-a.example.com",
    )
    monkeypatch.setattr("app.routers.backup_copies.urlparse", real_urlparse)

    mode["exchange"] = "http"
    assert f"copy_error={spec['error']}" in _loc(
        client.get(spec["callback"], params={"code": "abc", "state": "state-1"})
    )
    mode["exchange"] = "norefresh"
    assert f"copy_error={spec['error']}" in _loc(
        client.get(spec["callback"], params={"code": "abc", "state": "state-1"})
    )
    mode["exchange"] = "ok"
    mode["email"] = "down"
    quiet = client.get(spec["callback"], params={"code": "abc", "state": "state-1"})
    assert "copy_saved=1" in _loc(quiet)
    with Session(engine) as db:
        row = db.exec(select(BackupDestination).where(BackupDestination.provider == provider)).one()
        token = json.loads(decrypt_str(row.credentials_encrypted))
        assert token["refresh_token"] == "refresh-example"
        assert token["email"] == ""
    mode["email"] = "ok"
    mode["exchange"] = "noexp"
    done = client.get(spec["callback"], params={"code": "abc", "state": "state-1"})
    assert "copy_saved=1" in _loc(done)
    deleted = " ".join(done.headers.get_list("set-cookie"))
    assert spec["cookie"] in deleted
    with Session(engine) as db:
        row = db.exec(select(BackupDestination).where(BackupDestination.provider == provider)).one()
        token = json.loads(decrypt_str(row.credentials_encrypted))
        assert token["email"] == "user@example.com"
        assert token["refresh_token"] == "refresh-example"
        assert "expires_in" not in token


def test_google_and_onedrive_connect(api, monkeypatch):
    client, engine = api
    _exercise_connect(client, engine, monkeypatch, "google")
    _exercise_connect(client, engine, monkeypatch, "onedrive")


def test_oauth_store_exchange_and_email(monkeypatch):
    pem = "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n"
    assert copies.normalize_private_key(json.dumps({"private_key": pem})).startswith("-----BEGIN")
    assert copies.normalize_private_key(json.dumps({"email": "user@example.com"})).startswith("{")
    assert copies.normalize_private_key("{not json") == "{not json"
    assert copies.normalize_private_key("") == ""
    assert copies.normalize_private_key(json.dumps({"private_key": " \n"})) == ""
    crlf = "-----BEGIN PRIVATE KEY-----\r\nabc\r\n-----END PRIVATE KEY-----"
    assert "\r" not in copies.normalize_private_key(crlf)
    assert "\n" in copies.normalize_private_key(crlf)
    quoted = json.dumps(pem)
    assert copies.normalize_private_key(quoted).startswith("-----BEGIN")

    dest = BackupDestination(provider="drive", config_json="{")
    copies.store_oauth_client(
        dest,
        client_id="   ",
        client_secret="",
        folder="PiHerder",
        schedule=None,
        after_host_backup=False,
    )
    assert json.loads(dest.config_json)["oauth_client_id"] == ""
    dest.config_json = "[]"
    copies.store_oauth_client(
        dest,
        client_id="copy-client",
        client_secret="copy-secret",
        folder="Backups",
        schedule="0 1 * * *",
        after_host_backup=True,
        copy_herder_backup=True,
    )
    saved = json.loads(dest.config_json)
    assert saved["remote_dir"] == "Backups"
    assert saved["copy_herder_backup"] is True
    secret_blob = saved["oauth_client_secret_encrypted"]
    copies.store_oauth_client(
        dest,
        client_id="copy-client",
        client_secret="",
        folder="Backups",
        schedule=None,
        after_host_backup=False,
    )
    assert json.loads(dest.config_json)["oauth_client_secret_encrypted"] == secret_blob
    with pytest.raises(ValueError, match="client"):
        copies.store_oauth_client(
            dest,
            client_id="copy client",
            client_secret="copy-secret",
            folder="Backups",
            schedule=None,
            after_host_backup=False,
        )
    with pytest.raises(ValueError, match="client"):
        copies.store_oauth_client(
            dest,
            client_id="https://host-a.example.com/app",
            client_secret="copy-secret",
            folder="Backups",
            schedule=None,
            after_host_backup=False,
        )

    dest.credentials_encrypted = "not-a-token"
    before = dest.credentials_encrypted
    copies.store_oauth_client(
        dest,
        client_id="copy-client",
        client_secret="copy-secret",
        folder="Backups",
        schedule=None,
        after_host_backup=False,
    )
    assert dest.credentials_encrypted == before

    dest.credentials_encrypted = copies.encrypt_str(json.dumps(["nope"]))
    before = dest.credentials_encrypted
    copies.store_oauth_client(
        dest,
        client_id="copy-client",
        client_secret="copy-secret",
        folder="Backups",
        schedule=None,
        after_host_backup=False,
    )
    assert dest.credentials_encrypted == before

    dest.credentials_encrypted = copies.encrypt_str(json.dumps({"access_token": "only"}))
    before = dest.credentials_encrypted
    copies.store_oauth_client(
        dest,
        client_id="copy-client",
        client_secret="copy-secret",
        folder="Backups",
        schedule=None,
        after_host_backup=False,
    )
    assert dest.credentials_encrypted == before

    dest.credentials_encrypted = copies.encrypt_str(
        json.dumps({"refresh_token": "refresh-example", "client_id": "old", "client_secret": "old"})
    )
    copies.store_oauth_client(
        dest,
        client_id="copy-client",
        client_secret="copy-secret",
        folder="Backups",
        schedule=None,
        after_host_backup=False,
    )
    rewritten = json.loads(copies.decrypt_token(dest))
    assert rewritten["client_id"] == "copy-client"
    assert rewritten["client_secret"] == "copy-secret"
    assert rewritten["refresh_token"] == "refresh-example"

    mode = _install_urlopen(monkeypatch)
    ok = copies.exchange_google_code(
        client_id="copy-client",
        client_secret="copy-secret",
        code="code-1",
        redirect_uri="http://host-a.example.com/backup-copies/google/callback",
    )
    assert ok["refresh_token"] == "refresh-example"
    assert copies.exchange_microsoft_code(
        client_id="copy-client",
        client_secret="copy-secret",
        code="code-1",
        redirect_uri="http://host-a.example.com/backup-copies/onedrive/callback",
    )["access_token"] == "access-example"
    for label, fn in (("google", copies.exchange_google_code), ("microsoft", copies.exchange_microsoft_code)):
        for exchange_mode in ("http", "down", "junk", "norefresh", "list"):
            mode["exchange"] = exchange_mode
            with pytest.raises(ValueError, match=label):
                fn(
                    client_id="copy-client",
                    client_secret="copy-secret",
                    code="code-1",
                    redirect_uri="http://host-a.example.com/cb",
                )
    mode["exchange"] = "ok"
    mode["email"] = "ok"
    assert copies.google_account_email("access-example") == "user@example.com"
    assert copies.microsoft_account_email("access-example") == "user@example.com"
    mode["email"] = "upn"
    assert copies.microsoft_account_email("access-example") == "user@example.com"
    mode["email"] = "empty"
    assert copies.google_account_email("access-example") == ""
    assert copies.microsoft_account_email("access-example") == ""
    mode["email"] = "blank"
    assert copies.google_account_email("access-example") == ""
    mode["email"] = "down"
    assert copies.google_account_email("access-example") == ""
    assert copies.microsoft_account_email("access-example") == ""


def _proc(code, stderr):
    return SimpleNamespace(returncode=code, stderr=stderr, stdout="")


def test_probe_smb_drive_and_saved_destination(tmp_path, monkeypatch):
    monkeypatch.setattr(copies.urllib.request, "urlopen", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("network")))
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: True)
    smb = BackupDestination(provider="smb", config_json="{}")
    assert copies.probe(smb)["code"] == "demo"
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    assert copies.probe(BackupDestination(provider="box"))["code"] == "provider"
    assert copies.probe(BackupDestination(provider="drive", credentials_encrypted="  "))["code"] == "account"
    assert copies.has_saved_destination(None) is False
    assert copies.has_saved_destination(BackupDestination(provider="drive", config_json="{")) is False
    assert copies.has_saved_destination(BackupDestination(provider="drive", config_json="[]")) is False
    assert copies.has_saved_destination(BackupDestination(provider="drive", schedule="0 3 * * *")) is True
    assert copies.has_saved_destination(BackupDestination(provider="drive", after_host_backup=True)) is True
    assert copies.has_saved_destination(
        BackupDestination(provider="smb", config_json=json.dumps({"host": "192.0.2.10"}))
    ) is True
    assert copies.has_saved_destination(
        BackupDestination(provider="smb", config_json=json.dumps({"share": "backups"}))
    ) is True
    assert copies.has_saved_destination(
        BackupDestination(provider="drive", config_json=json.dumps({"oauth_client_id": "copy-client"}))
    ) is True
    assert copies.has_saved_destination(
        BackupDestination(
            provider="drive",
            config_json=json.dumps({"oauth_client_secret_encrypted": "blob"}),
        )
    ) is True
    picked = BackupDestination(
        provider="drive",
        selection_json=copies.selection_json(["host-a"], []),
    )
    assert copies.has_saved_destination(picked) is True
    skipped = BackupDestination(
        provider="drive",
        selection_json=json.dumps({"checked": [], "skipped": ["host-a"]}),
    )
    assert copies.has_saved_destination(skipped) is False
    creds = copies.encrypt_str(json.dumps({"refresh_token": "refresh-example"}))
    assert copies.has_saved_destination(BackupDestination(provider="drive", credentials_encrypted=creds)) is True

    assert copies._probe_smb(BackupDestination(provider="smb", config_json="{"))["code"] == "account"
    assert copies._probe_smb(
        BackupDestination(
            provider="smb",
            config_json=json.dumps({"host": "192.0.2.10", "share": "backups"}),
        )
    )["code"] == "account"

    guest = BackupDestination(provider="smb", name="LAN NAS / SMB")
    copies.store_smb(
        guest,
        host="192.0.2.10",
        share="backups",
        path="",
        username="",
        password="",
        domain="",
        schedule=None,
        after_host_backup=False,
    )
    removed = []
    real_discard = copies.discard_rclone_config

    def _discard(path):
        removed.append(path)
        real_discard(path)

    monkeypatch.setattr(copies, "discard_rclone_config", _discard)

    def _raise_write(*_a, **_k):
        raise RuntimeError("disk")

    real_write = copies.write_smb_rclone_config
    monkeypatch.setattr(copies, "write_smb_rclone_config", _raise_write)
    assert copies._probe_smb(guest)["code"] == "account"
    monkeypatch.setattr(copies, "write_smb_rclone_config", lambda *_a, **_k: str(tmp_path / "rclone.conf"))

    codes = []

    def _run(cmd, **_kwargs):
        kind = codes.pop(0)
        if kind == "missing":
            raise FileNotFoundError("rclone")
        if kind == "timeout":
            raise subprocess.TimeoutExpired(cmd, 25)
        if kind == "boom":
            raise RuntimeError("boom")
        stderr = {
            "ok": "",
            "auth": "permission denied",
            "folder": "object_name_not_found",
            "host": "no route",
            "other": "mystery",
            "drive-folder": "404 not found",
            "drive-auth": "invalid grant",
        }[kind]
        return _proc(0 if kind == "ok" else 1, stderr)

    monkeypatch.setattr(copies.subprocess, "run", _run)
    expect = {
        "missing": "rclone",
        "timeout": "timeout",
        "ok": "ok",
        "auth": "auth",
        "folder": "folder",
        "host": "host",
        "other": "rclone",
        "boom": "account",
    }
    for kind, code in expect.items():
        codes.append(kind)
        assert copies._probe_smb(guest)["code"] == code
    assert removed

    monkeypatch.setattr(copies, "write_smb_rclone_config", real_write)
    codes.append("ok")
    assert copies.probe(guest)["code"] == "ok"

    drive = BackupDestination(
        provider="drive",
        credentials_encrypted=copies.encrypt_str(
            copies.pack_oauth_token(
                client_id="copy-client",
                client_secret="copy-secret",
                refresh_token="refresh-example",
                access_token="access-example",
                email="user@example.com",
                expires_in=None,
            )
        ),
        config_json=json.dumps({"remote_dir": "PiHerder"}),
    )
    drive_expect = {
        "missing": "rclone",
        "timeout": "timeout",
        "ok": "ok",
        "drive-folder": "folder",
        "drive-auth": "auth",
        "other": "rclone",
        "boom": "auth",
    }
    for kind, code in drive_expect.items():
        codes.append(kind)
        assert copies.probe(drive)["code"] == code

    real_decrypt = copies.decrypt_token
    monkeypatch.setattr(copies, "decrypt_token", lambda _dest: (_ for _ in ()).throw(ValueError("x")))
    assert copies.probe(drive)["code"] == "auth"
    monkeypatch.setattr(copies, "decrypt_token", real_decrypt)

    one = BackupDestination(
        provider="onedrive",
        credentials_encrypted=drive.credentials_encrypted,
        config_json=json.dumps({"remote_dir": "PiHerder"}),
    )
    monkeypatch.setattr(copies, "onedrive_default_drive", lambda _token: ("drive-1", "personal"))
    codes.append("ok")
    assert copies.probe(one)["code"] == "ok"


def test_herder_archive_and_unstarted(tmp_path, monkeypatch):
    engine = _engine(tmp_path / "herder.db")
    calls = []

    def _delay(job_id):
        calls.append(job_id)
        return SimpleNamespace(id=f"task-{job_id}")

    def _down(_job_id):
        calls.append("down")
        raise RuntimeError("queue-down")

    monkeypatch.setattr("app.tasks.replicate_backup.delay", _delay)
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    with Session(engine) as session:
        blank = BackupDestination(provider="drive", name="Google Drive", enabled=True)
        session.add(blank)
        session.commit()
        session.refresh(blank)
        refused = copies.enqueue_herder_archive(session, blank, _ARCHIVE_A)
        assert refused.id is None
        assert json.loads(refused.details)["error"] == "removed"
        assert session.exec(select(Job)).first() is None

        drive = BackupDestination(provider="drive", name="Google Drive", enabled=True)
        copies.store_oauth_client(
            drive,
            client_id="copy-client",
            client_secret="copy-secret",
            folder="PiHerder",
            schedule=None,
            after_host_backup=False,
            copy_herder_backup=True,
        )
        drive.credentials_encrypted = copies.encrypt_str(
            copies.pack_oauth_token(
                client_id="copy-client",
                client_secret="copy-secret",
                refresh_token="refresh-example",
                access_token="access-example",
                email="user@example.com",
                expires_in=3600,
            )
        )
        smb = BackupDestination(provider="smb", name="LAN NAS / SMB", enabled=True)
        copies.store_smb(
            smb,
            host="198.51.100.10",
            share="backups",
            path="PiHerder",
            username="backup",
            password="copy-secret",
            domain="",
            schedule=None,
            after_host_backup=False,
            copy_herder_backup=True,
        )
        one = BackupDestination(provider="onedrive", name="OneDrive", enabled=True)
        copies.store_oauth_client(
            one,
            client_id="copy-client",
            client_secret="copy-secret",
            folder="PiHerder",
            schedule=None,
            after_host_backup=False,
        )
        one.credentials_encrypted = drive.credentials_encrypted
        session.add(drive)
        session.add(smb)
        session.add(one)
        session.commit()
        session.refresh(drive)
        session.refresh(smb)
        session.refresh(one)

        bad_name = copies.enqueue_herder_archive(session, drive, "notes.txt")
        assert bad_name.id is None
        first = copies.enqueue_herder_archive(session, drive, f"/home/user/{_ARCHIVE_A}", user_id=7)
        assert first.status == "pending"
        details = json.loads(first.details)
        assert details["herder_archive"] == _ARCHIVE_A
        assert details["user_id"] == 7
        assert "Drive" in details["log_lines"][0]
        again = copies.enqueue_herder_archive(session, drive, _ARCHIVE_A)
        assert again.id == first.id
        assert calls == [first.id]

        session.add(
            Job(job_type="backup_replicate", status="pending", details="{")
        )
        session.add(
            Job(
                job_type="backup_replicate",
                status="running",
                details=json.dumps({"destination_id": 999999, "herder_archive": _ARCHIVE_B}),
            )
        )
        session.add(
            Job(
                job_type="backup_replicate",
                status="pending",
                details=json.dumps({"destination_id": drive.id, "herder_archive": "piherder-other.tar.gz"}),
            )
        )
        session.commit()
        second = copies.enqueue_herder_archive(session, drive, _ARCHIVE_B)
        assert second.id != first.id
        assert calls == [first.id, second.id]

        smb_job = copies.enqueue_herder_archive(session, smb, _ARCHIVE_A)
        assert "LAN share" in json.loads(smb_job.details)["log_lines"][0]
        one_job = copies.enqueue_herder_archive(session, one, _ARCHIVE_A)
        assert "OneDrive" in json.loads(one_job.details)["log_lines"][0]

        monkeypatch.setattr("app.services.demo.demo_mode", lambda: True)
        monkeypatch.setattr("app.tasks.replicate_backup.delay", _down)
        demo_job = copies.enqueue_herder_archive(session, drive, _ARCHIVE_C)
        assert demo_job.status == "failed"
        assert json.loads(demo_job.details)["error"] == "Demo does not upload"
        assert "down" not in calls
        demo_copy = copies.enqueue(session, drive)
        assert json.loads(demo_copy.details)["error"] == "Demo does not upload"
        assert "direct_host" not in json.loads(demo_copy.details)

        monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
        failed = copies.enqueue(session, drive)
        assert failed.status == "failed"
        assert json.loads(failed.details)["error"] == "queue-down"
        herder_failed = copies.enqueue_herder_archive(session, drive, _ARCHIVE_D)
        assert json.loads(herder_failed.details)["error"] == "queue-down"
        assert herder_failed.celery_task_id is None

        job = Job(job_type="backup_replicate", status="pending")
        session.add(job)
        session.commit()
        session.refresh(job)
        quiet = copies._fail_unstarted(session, job, drive, "x" * 600, direct_host=False)
        payload = json.loads(quiet.details)
        assert "direct_host" not in payload
        assert len(payload["error"]) == 500
        assert payload["done"] is True
        assert quiet.finished_at is not None

        other = Job(job_type="backup_replicate", status="running")
        session.add(other)
        session.commit()
        session.refresh(other)
        marked = copies._fail_unstarted(session, other, drive, "stopped", direct_host=True)
        assert json.loads(marked.details)["direct_host"] is True
        assert marked.status == "failed"
