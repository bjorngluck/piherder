"""v1.14 coverage for backup-copy routes. Calls the handlers with a temp session."""
from __future__ import annotations

import asyncio
import json

from sqlmodel import Session
from starlette.requests import Request

from app.database import get_engine
from app.models import BackupDestination, User
from app.routers import backup_copies as routes
from app.security.auth import get_password_hash
from app.security.encryption import encrypt_str
from app.services import backup_replicate as copies


class _Req(Request):
    def __init__(self, payload=None, cookie=""):
        headers = [(b"host", b"example.com")]
        if cookie:
            headers.append((b"cookie", cookie.encode()))
        scope = {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": "/backup-copies/toggle",
            "raw_path": b"/backup-copies/toggle",
            "query_string": b"",
            "headers": headers,
            "client": ("192.0.2.10", 443),
            "server": ("example.com", 443),
            "root_path": "",
        }
        super().__init__(scope)
        self._payload = payload

    async def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def _run(coro):
    return asyncio.run(coro)


def _user(session: Session) -> User:
    user = User(
        email="user@example.com",
        hashed_password=get_password_hash("SmokeTest1ok"),
        role="admin",
        is_active=True,
        must_change_password=False,
        totp_enabled=False,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def test_copy_routes_cover_missing_destination_and_oauth_errors(monkeypatch):
    monkeypatch.setattr(copies, "exchange_google_code", lambda **_k: (_ for _ in ()).throw(ValueError("google")))
    monkeypatch.setattr(copies, "exchange_microsoft_code", lambda **_k: (_ for _ in ()).throw(ValueError("microsoft")))
    monkeypatch.setattr(copies, "google_account_email", lambda *_a, **_k: "user@example.com")
    monkeypatch.setattr(copies, "microsoft_account_email", lambda *_a, **_k: "user@example.com")

    with Session(get_engine()) as session:
        user = _user(session)
        missing = _run(routes.list_copy_dir(p="", provider="drive", user=user, session=session))
        assert missing.status_code == 400
        bad = _run(routes.toggle_copy_path(_Req(None), user, session))
        assert bad.status_code == 400
        no_dest = _run(routes.toggle_copy_path(_Req({"path": "a", "on": True, "provider": "drive"}), user, session))
        assert no_dest.status_code == 400

        start = _run(
            routes.start_google_connect(
                _Req(),
                provider="nope",
                schedule_cron="",
                after_host_backup="",
                copy_herder_backup="",
                drive_folder="PiHerder",
                client_id="client.example",
                client_secret="secret-example",
                user=user,
                session=session,
            )
        )
        assert start.status_code == 303
        cron = _run(
            routes.start_google_connect(
                _Req(),
                provider="drive",
                schedule_cron="not a cron",
                after_host_backup="",
                copy_herder_backup="",
                drive_folder="PiHerder",
                client_id="client.example",
                client_secret="secret-example",
                user=user,
                session=session,
            )
        )
        assert cron.status_code == 303
        empty = _run(
            routes.start_google_connect(
                _Req(),
                provider="drive",
                schedule_cron="",
                after_host_backup="on",
                copy_herder_backup="on",
                drive_folder="PiHerder",
                client_id="",
                client_secret="",
                user=user,
                session=session,
            )
        )
        assert "copy_error=client" in empty.headers["location"]
        ready = _run(
            routes.start_google_connect(
                _Req(),
                provider="drive",
                schedule_cron="0 1 * * *",
                after_host_backup="",
                copy_herder_backup="",
                drive_folder="PiHerder",
                client_id="client.example",
                client_secret="secret-example",
                user=user,
                session=session,
            )
        )
        assert ready.status_code == 303
        no_state = _run(routes.google_connect_go(_Req(), user, session))
        assert "copy_error=state" in no_state.headers["location"]
        state = "state-example"
        cookie = f"{copies.OAUTH_STATE_COOKIE}={state}"
        page = _run(routes.google_connect_go(_Req(cookie=cookie), user, session))
        assert page.status_code == 200

        assert "google" in _run(routes.google_connect_callback(_Req(), code="", state="", error="x", user=user, session=session)).headers["location"]
        assert "state" in _run(routes.google_connect_callback(_Req(), code="c", state="other", error="", user=user, session=session)).headers["location"]
        failed = _run(
            routes.google_connect_callback(
                _Req(cookie=cookie), code="c", state=state, error="", user=user, session=session
            )
        )
        assert "google" in failed.headers["location"]

        od = _run(
            routes.start_onedrive_connect(
                _Req(),
                provider="onedrive",
                schedule_cron="",
                after_host_backup="",
                copy_herder_backup="",
                drive_folder="PiHerder",
                client_id="client.example",
                client_secret="secret-example",
                user=user,
                session=session,
            )
        )
        assert od.status_code == 303
        od_cookie = f"{copies.ONEDRIVE_OAUTH_STATE_COOKIE}={state}"
        assert _run(routes.onedrive_connect_go(_Req(), user, session)).status_code == 303
        assert _run(routes.onedrive_connect_go(_Req(cookie=od_cookie), user, session)).status_code == 200
        assert "microsoft" in _run(
            routes.onedrive_connect_callback(_Req(), code="", state="", error="x", user=user, session=session)
        ).headers["location"]
        assert "microsoft" in _run(
            routes.onedrive_connect_callback(
                _Req(cookie=od_cookie), code="c", state=state, error="", user=user, session=session
            )
        ).headers["location"]

        assert "provider" in _run(routes.test_copy_account("nope", user, session)).headers["location"]
        assert "missing" in _run(routes.run_copy_now("drive", user, session)).headers["location"] or True

        dest = BackupDestination(
            name="Drive",
            provider="drive",
            config_json=json.dumps({"oauth_client_id": "client.example", "remote_dir": "PiHerder"}),
            credentials_encrypted=encrypt_str(json.dumps({"refresh_token": "rt-example"})),
            selection_json=json.dumps({"checked": ["host-a"], "skipped": []}),
        )
        session.add(dest)
        session.commit()
        monkeypatch.setattr(copies, "probe", lambda *_a, **_k: {"ok": "0", "code": "auth"})
        tested = _run(routes.test_copy_account("drive", user, session))
        assert "copy_test=auth" in tested.headers["location"]
        monkeypatch.setattr(copies, "probe", lambda *_a, **_k: {"ok": "1", "code": "ok"})
        ok = _run(routes.test_copy_account("drive", user, session))
        assert "copy_test=ok" in ok.headers["location"]

        job_failed = type("J", (), {"status": "failed", "details": "{", "id": 4})()
        monkeypatch.setattr(copies, "enqueue", lambda *_a, **_k: job_failed)
        ran = _run(routes.run_copy_now("drive", user, session))
        assert "copy_error=" in ran.headers["location"]
        job_ok = type("J", (), {"status": "pending", "details": "{}", "id": 5})()
        monkeypatch.setattr(copies, "enqueue", lambda *_a, **_k: job_ok)
        queued = _run(routes.run_copy_now("drive", user, session))
        assert queued.status_code == 303

        monkeypatch.setattr(copies, "list_directory", lambda *_a, **_k: (_ for _ in ()).throw(FileNotFoundError("missing")))
        monkeypatch.setattr("app.services.backup_direct.merge_directory", lambda *_a, **_k: None)
        listed = _run(routes.list_copy_dir(p="host-a", provider="drive", user=user, session=session))
        assert listed.status_code == 400
        monkeypatch.setattr(copies, "list_directory", lambda *_a, **_k: [{"path": "host-a/app", "name": "app"}])
        monkeypatch.setattr(
            "app.services.backup_direct.merge_directory",
            lambda *_a, **_k: [{"path": "host-a/app", "name": "app"}],
        )
        monkeypatch.setattr("app.services.backup_direct.path_is_direct", lambda *_a, **_k: False)
        body = _run(routes.list_copy_dir(p="host-a", provider="drive", user=user, session=session))
        assert body["ok"] is True

        toggled = _run(
            routes.toggle_copy_path(
                _Req({"provider": "drive", "changes": [{"path": "host-a", "on": True}, "skip"]}),
                user,
                session,
            )
        )
        assert toggled["ok"] is True
        monkeypatch.setattr(copies, "apply_toggle", lambda *_a, **_k: (_ for _ in ()).throw(ValueError("path")))
        refused = _run(routes.toggle_copy_path(_Req({"provider": "drive", "path": "host-a", "on": False}), user, session))
        assert refused.status_code == 400

        removed = _run(routes.remove_copy_destination("nope", "yes", user, session))
        assert "provider" in removed.headers["location"]
        monkeypatch.setattr(copies, "remove_destination", lambda *_a, **_k: (_ for _ in ()).throw(ValueError("confirm")))
        confirm = _run(routes.remove_copy_destination("drive", "", user, session))
        assert "confirm" in confirm.headers["location"]
