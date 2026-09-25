"""v1.7 coverage — console websocket, host files routes, backup and undo tasks.

All DB access uses a temp SQLite engine. SSH, Celery, and host-file IO are stubs.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.main import app
from app.models import Integration, Job, Server, ServiceTemplate, StackDeployment, User
from app.security.auth import create_user_access_token, get_password_hash


def _engine(path):
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _listing():
    return {
        "jail": "/home/pi",
        "rel": "",
        "abs": "/home/pi",
        "truncated": False,
        "crumbs": [{"name": "home", "rel": ""}],
        "entries": [
            {
                "name": "notes.txt",
                "rel": "notes.txt",
                "kind": "file",
                "size": 4,
                "size_h": "4 B",
                "mtime": None,
                "mode": "644",
                "group": "pi",
                "uid": 1000,
                "gid": 1000,
            }
        ],
        "search": False,
        "query": "",
        "contents": False,
    }


class _Stream:
    def read(self):
        return b""

    class channel:
        @staticmethod
        def recv_exit_status():
            return 0


class _Ssh:
    def exec_command(self, *args, **kwargs):
        return _Stream(), _Stream(), _Stream()

    def close(self):
        return None


class _Chan:
    def __init__(self):
        self.sent = []
        self._ready = True

    def recv_ready(self):
        return False

    def recv(self, n):
        return b""

    def recv_stderr_ready(self):
        return False

    def exit_status_ready(self):
        return False

    def send(self, data):
        self.sent.append(data)

    def resize_pty(self, width=80, height=24):
        return None


class _MuxClient:
    _ph_mux_note = "tmux"
    _ph_mux_name = "ph-u1-s1-n0-f"
    _ph_mux_backend = "tmux"

    def close(self):
        return None


def _patch_files(monkeypatch):
    from app.services import host_files as hf

    monkeypatch.setattr(hf, "files_surface_allowed", lambda: True)
    monkeypatch.setattr(hf, "files_supported", lambda server: True)
    monkeypatch.setattr(hf, "is_demo_files", lambda: False)
    monkeypatch.setattr(hf, "list_dir", lambda *a, **k: _listing())
    monkeypatch.setattr(hf, "search", lambda *a, **k: _listing())
    monkeypatch.setattr(hf, "jail_path", lambda *a, **k: "/home/pi")
    monkeypatch.setattr(hf, "stat_file", lambda *a, **k: {"size": 4, "kind": "file"})
    monkeypatch.setattr(hf, "iter_file", lambda *a, **k: iter([b"hi"]))
    monkeypatch.setattr(hf, "read_text", lambda *a, **k: {"text": "hi", "rel": "notes.txt"})
    monkeypatch.setattr(hf, "peek_file", lambda *a, **k: {"text": "hi", "binary": False, "rel": "notes.txt"})
    monkeypatch.setattr(hf, "iter_preview", lambda *a, **k: iter([b"hi"]))
    monkeypatch.setattr(hf, "list_docker_volumes", lambda *a, **k: [])
    monkeypatch.setattr(hf, "list_docker_containers", lambda *a, **k: ["web"])
    monkeypatch.setattr(hf, "list_container_mounts", lambda *a, **k: [])
    monkeypatch.setattr(hf, "docker_cp_into", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(hf, "write_text", lambda *a, **k: {"ok": True, "dest": "notes.txt", "rel": "notes.txt"})
    monkeypatch.setattr(hf, "zip_on_host", lambda *a, **k: {"rel": "a.zip", "dest": ""})
    monkeypatch.setattr(hf, "remove_tree", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(hf, "unzip_into", lambda *a, **k: {"ok": True, "dest": ""})
    monkeypatch.setattr(hf, "apply_perms", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(hf, "move_many", lambda *a, **k: {"dest": "", "moved": 1})
    monkeypatch.setattr(hf, "ensure_dir", lambda *a, **k: None)
    monkeypatch.setattr(hf, "put_file", lambda *a, **k: {"dest": "up.bin", "rel": "up.bin"})
    monkeypatch.setattr(hf, "mkdir", lambda *a, **k: {"dest": "d", "rel": "d"})
    monkeypatch.setattr(hf, "rename", lambda *a, **k: {"dest": "b.txt", "rel": "b.txt", "from": "a", "to": "b"})
    monkeypatch.setattr(hf, "remove", lambda *a, **k: {"ok": True, "rel": "notes.txt"})


def _client(engine, monkeypatch):
    from app.services import app_settings as app_cfg
    import app.database as dbmod
    import app.routers.server_console as console_mod
    import app.services.ssh as ssh_mod

    monkeypatch.setattr(dbmod, "engine", engine)
    monkeypatch.setattr(console_mod, "engine", engine)
    monkeypatch.setattr(app_cfg, "engine", engine)
    monkeypatch.setattr(app_cfg, "_cache", None)
    monkeypatch.setattr(ssh_mod, "get_ssh_client", lambda *a, **k: _Ssh())
    import app.services.docker_inventory as inv
    monkeypatch.setattr(inv, "request_refresh", lambda *a, **k: False)

    def _session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    client = TestClient(app, raise_server_exceptions=False)
    with Session(engine) as s:
        user = User(
            email="q17@test.local",
            hashed_password=get_password_hash("SmokeTest1ok"),
            role="operator",
            is_active=True,
            must_change_password=False,
            totp_enabled=False,
        )
        server = Server(name="pi", hostname="pi.local", ssh_username="pi", os_type="debian")
        s.add(user)
        s.add(server)
        s.commit()
        s.refresh(user)
        s.refresh(server)
        client.cookies.set("access_token", create_user_access_token(user))
        return client, user.id, server.id


def test_files_templates_and_host_pages(tmp_path, monkeypatch):
    _patch_files(monkeypatch)
    engine = _engine(tmp_path / "q1.db")
    client, _uid, sid = _client(engine, monkeypatch)
    try:
        import app.services.jobs as job_service

        job = SimpleNamespace(id=1, job_type="os_update_check")
        for name in (
            "enqueue_os_update_check",
            "enqueue_container_update_check",
            "enqueue_os_patch_apply",
            "enqueue_container_patch_apply",
            "enqueue_template_redeploy",
            "enqueue_template_drift_check",
            "enqueue_template_deploy",
            "create_job_and_run",
        ):
            monkeypatch.setattr(job_service, name, lambda *a, **k: job)
        monkeypatch.setattr(job_service, "_active_job_of_type", lambda *a, **k: None)

        with Session(engine) as s:
            server = s.get(Server, sid)
            server.os_patch_enabled = True
            server.container_patch_enabled = True
            server.backup_enabled = True
            s.add(server)
            s.commit()
            tpl = ServiceTemplate(slug="web", name="Web", definition_json="{}")
            s.add(tpl)
            s.commit()
            s.refresh(tpl)
            dep = StackDeployment(
                server_id=sid,
                project_name="web",
                template_id=tpl.id,
                template_slug="web",
                variables_json="{}",
                files_json="{}",
            )
            integ = Integration(type="uptime_kuma", name="Kuma", base_url="http://kuma")
            s.add(dep)
            s.add(integ)
            s.commit()
            s.refresh(dep)
            s.refresh(integ)
            did, iid = dep.id, integ.id

        headers = {"x-piherder-files": "1"}
        paths = [
            ("get", f"/servers/{sid}/files", None),
            ("get", f"/servers/{sid}/files/ls?p=", None),
            ("get", f"/servers/{sid}/files/search?q=notes", None),
            ("get", f"/servers/{sid}/files/download?p=notes.txt", None),
            ("get", f"/servers/{sid}/files/content?p=notes.txt", None),
            ("get", f"/servers/{sid}/files/peek?p=notes.txt", None),
            ("get", f"/servers/{sid}/files/preview?p=notes.txt", None),
            ("get", f"/servers/{sid}/files/docker/volumes", None),
            ("get", f"/servers/{sid}/files/docker/containers", None),
            ("get", f"/servers/{sid}/files/docker/mounts?container=web", None),
            ("get", f"/servers/{sid}", None),
            ("get", f"/servers/{sid}/docker", None),
            ("get", f"/servers/{sid}/backups", None),
            ("get", "/servers", None),
            ("get", "/servers/add", None),
            ("get", "/audit", None),
            ("get", "/certificates", None),
            ("get", "/integrations", None),
            ("get", f"/integrations/{iid}", None),
            ("get", f"/integrations/{iid}/edit", None),
            ("get", f"/templates/deployments/{did}", None),
            ("get", "/templates/web/deploy", None),
            ("get", f"/servers/{sid}/deployments", None),
        ]
        for method, path, _body in paths:
            response = client.get(path, headers=headers)
            assert response.status_code < 500, path

        posts = [
            (f"/servers/{sid}/files/mkdir", {"p": "", "name": "d", "identity": "fleet"}),
            (f"/servers/{sid}/files/rename", {"p": "", "src": "a", "dest": "b", "identity": "fleet"}),
            (f"/servers/{sid}/files/delete", {"p": "", "name": "notes.txt", "identity": "fleet"}),
            (f"/servers/{sid}/files/save", {"p": "notes.txt", "content": "x", "identity": "fleet"}),
            (f"/servers/{sid}/files/mkdir", {"name": "box", "identity": "fleet"}),
            (f"/servers/{sid}/reboot", {}),
            ("/servers/bulk", {"action": "check_os", "server_ids": str(sid)}),
            ("/servers/bulk", {"action": "check_containers", "server_ids": str(sid)}),
            ("/servers/bulk", {"action": "os_patch", "server_ids": str(sid)}),
            ("/servers/bulk", {"action": "container_patch", "server_ids": str(sid)}),
            ("/servers/bulk", {"action": "backup", "server_ids": str(sid)}),
            (
                f"/servers/{sid}/update",
                {
                    "name": "pi",
                    "hostname": "pi.local",
                    "ssh_username": "pi",
                    "ssh_port": "22",
                    "docker_base_dir": "~/docker",
                    "os_type": "debian",
                },
            ),
            (f"/templates/deployments/{did}/redeploy", {}),
            (f"/templates/deployments/{did}/check-drift", {}),
        ]
        for path, data in posts:
            response = client.post(path, data=data, headers=headers)
            assert response.status_code < 500, path

        response = client.post(
            f"/servers/{sid}/files/upload",
            data={"p": "", "identity": "fleet", "rel_path": ""},
            files={"file": ("up.bin", b"abc", "application/octet-stream")},
            headers=headers,
        )
        assert response.status_code < 500
    finally:
        app.dependency_overrides.pop(get_session, None)


def test_console_websocket_paths(tmp_path, monkeypatch):
    import asyncio

    from fastapi import WebSocketDisconnect
    from app.services import ssh_console as cons
    import app.routers.server_console as console_mod

    engine = _engine(tmp_path / "ws.db")
    client, uid, sid = _client(engine, monkeypatch)
    token = client.cookies.get("access_token")
    monkeypatch.setattr(console_mod, "engine", engine)
    monkeypatch.setattr(cons, "consume_ticket", lambda *a, **k: {"console": True, "role": "fleet", "n": 0})
    monkeypatch.setattr(cons, "try_acquire_slot", lambda *a, **k: None)
    monkeypatch.setattr(cons, "idle_sec", lambda: 3600)
    monkeypatch.setattr(cons, "max_session_sec", lambda: 3600)
    monkeypatch.setattr(cons, "revalidate_sec", lambda: 3600)
    monkeypatch.setattr(cons, "is_demo_console", lambda: False)
    monkeypatch.setattr(cons, "audit_mode", lambda: "off")
    monkeypatch.setattr(cons, "audit_required", lambda: False)
    monkeypatch.setattr(cons, "mux_allowed_for_server", lambda server: True)
    monkeypatch.setattr(cons, "mux_session_name", lambda **k: "ph-u1-s1-n0-f")
    monkeypatch.setattr(cons, "kill_mux_session", lambda *a, **k: None)
    monkeypatch.setattr(cons, "release_slot", lambda *a, **k: None)
    monkeypatch.setattr(cons, "park_console", lambda *a, **k: None)
    chan = _Chan()
    monkeypatch.setattr(
        cons,
        "open_session_channel",
        lambda *a, **k: (_MuxClient(), chan),
    )

    class _WS:
        def __init__(self, messages):
            self._messages = list(messages)
            self.headers = {"host": "testserver", "origin": "http://testserver"}
            self.cookies = {"access_token": token}
            self.client = SimpleNamespace(host="127.0.0.1")

        async def accept(self):
            return None

        async def close(self, code=1000):
            return None

        async def send_text(self, text):
            return None

        async def send_bytes(self, data):
            return None

        async def receive(self):
            if not self._messages:
                raise WebSocketDisconnect()
            return self._messages.pop(0)

    def _frame(payload):
        return {"type": "websocket.receive", "text": json.dumps(payload)}

    try:
        for messages in (
            [{"type": "websocket.receive", "text": "nope"}],
            [
                _frame({"type": "auth", "ticket": "t"}),
                _frame({"type": "resize", "cols": 100, "rows": 40}),
                _frame({"type": "ping"}),
                _frame({"type": "stdin", "hex": "61"}),
                {"type": "websocket.receive", "bytes": b"b"},
                _frame({"type": "bye"}),
            ],
        ):
            try:
                asyncio.run(console_mod.console_websocket(_WS(messages), sid))
            except asyncio.CancelledError:
                pass
        assert uid
    finally:
        app.dependency_overrides.pop(get_session, None)


def test_backup_and_undo_tasks(tmp_path, monkeypatch):
    import app.tasks as tasks
    from app.services import jobs_migrate as jm

    engine = _engine(tmp_path / "tasks.db")
    monkeypatch.setattr(tasks, "engine", engine)
    monkeypatch.setattr(tasks, "try_acquire_server_lock", lambda *a, **k: "tok")
    monkeypatch.setattr(tasks, "release_server_lock", lambda *a, **k: None)
    monkeypatch.setattr(tasks, "try_acquire_dual_server_lock", lambda *a, **k: ("a", "b"))
    monkeypatch.setattr(tasks, "release_dual_server_lock", lambda *a, **k: None)
    monkeypatch.setattr(tasks, "run_backup", lambda *a, **k: {"ok": True, "files": 1})
    monkeypatch.setattr(tasks, "backup_succeeded", lambda summary: True)
    monkeypatch.setattr(tasks, "_flush_job_progress_db", lambda *a, **k: None)
    monkeypatch.setattr(tasks, "clear_job_progress_buffer", lambda *a, **k: None)
    monkeypatch.setattr(jm, "_execute_service_migrate_undo", lambda *a, **k: None)

    with Session(engine) as s:
        server = Server(name="pi", hostname="pi.local", ssh_username="pi", os_type="debian")
        s.add(server)
        s.commit()
        s.refresh(server)
        job = Job(server_id=server.id, job_type="backup", status="pending", details="{}")
        undo = Job(server_id=server.id, job_type="service_migrate_undo", status="pending", details="{}")
        s.add(job)
        s.add(undo)
        s.commit()
        s.refresh(job)
        s.refresh(undo)
        sid, jid, uid = server.id, job.id, undo.id

    tasks.backup_server.push_request(id="task-1", retries=0)
    try:
        out = tasks.backup_server.run(sid, jid, None, None)
    finally:
        tasks.backup_server.pop_request()
    assert isinstance(out, dict)

    tasks.service_migrate_undo.push_request(id="undo-1", retries=0)
    try:
        out2 = tasks.service_migrate_undo.run(uid, sid, sid, 1)
    finally:
        tasks.service_migrate_undo.pop_request()
    assert out2["status"] == "ok"
    assert json.dumps(out2)
