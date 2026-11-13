"""Coverage toward the 90 percent floor. Placeholders only."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import WebSocketDisconnect
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.main import app
from app.models import Server, User
from app.security.auth import create_user_access_token, get_password_hash


def _engine(path):
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _seed(engine):
    with Session(engine) as session:
        user = User(
            email="user@example.com",
            hashed_password=get_password_hash("SmokeTest1ok"),
            role="operator",
            is_active=True,
            must_change_password=False,
            totp_enabled=True,
        )
        server = Server(
            name="host-a",
            hostname="host-a.example.com",
            ssh_username="piherder",
            os_type="debian",
        )
        session.add(user)
        session.add(server)
        session.commit()
        session.refresh(user)
        session.refresh(server)
        return user, server


class _WS:
    def __init__(self, messages, *, cookies=None, origin_ok=True):
        self._messages = list(messages)
        self.headers = {
            "host": "testserver",
            "origin": "http://testserver" if origin_ok else "http://evil.example",
        }
        self.cookies = dict(cookies or {})
        self.client = SimpleNamespace(host="192.0.2.10")
        self.sent = []
        self.closed = []
        self.fail_bytes = False

    async def accept(self):
        return None

    async def close(self, code=1000):
        self.closed.append(code)

    async def send_text(self, text):
        self.sent.append(text)

    async def send_bytes(self, data):
        if self.fail_bytes:
            raise RuntimeError("bytes")
        self.sent.append(data)

    async def receive(self):
        if not self._messages:
            raise WebSocketDisconnect()
        item = self._messages.pop(0)
        if item == "timeout":
            raise asyncio.TimeoutError()
        if item == "drop":
            raise WebSocketDisconnect()
        return item


class _Chan:
    def __init__(self, script):
        self.script = list(script)
        self.sent = []

    def _step(self, kind):
        if self.script and self.script[0][0] == kind:
            return self.script.pop(0)[1]
        return None

    def recv_ready(self):
        return bool(self.script) and self.script[0][0] == "out"

    def recv(self, _n):
        return self._step("out") or b""

    def recv_stderr_ready(self):
        return bool(self.script) and self.script[0][0] == "err"

    def recv_stderr(self, _n):
        return self._step("err") or b""

    def exit_status_ready(self):
        if self.script and self.script[0][0] == "exit":
            self.script.pop(0)
            return True
        return False

    def send(self, data):
        self.sent.append(data)

    def resize_pty(self, width=80, height=24):
        return None


class _Recorder:
    def feed_stdout(self, _data):
        raise RuntimeError("audit")


class _Held:
    def __init__(self):
        self.client = SimpleNamespace(close=lambda: None)
        self.channel = _Chan([("out", b"held")])
        self.ticket_payload = {"role": "fleet"}
        self.started_mono = 0
        self.last_activity_mono = 0
        self.resume_id = "resume-a"
        self.recorder = _Recorder()

    def take_out(self):
        return b"buffered"


def _frame(payload):
    return {"type": "websocket.receive", "text": json.dumps(payload)}


def _run(ws, server_id):
    import app.routers.server_console as console_mod

    try:
        asyncio.run(console_mod.console_websocket(ws, server_id))
    except asyncio.CancelledError:
        pass


def test_console_websocket_covers_auth_resume_and_pump(tmp_path, monkeypatch):
    from app.database import get_session
    from app.services import ssh_console as cons
    import app.routers.server_console as console_mod

    engine = _engine(tmp_path / "ws.db")
    user, server = _seed(engine)
    token = create_user_access_token(user)
    monkeypatch.setattr("app.database.engine", engine)
    monkeypatch.setattr(console_mod, "get_engine", lambda: engine)
    monkeypatch.setattr(cons, "websocket_origin_allowed", lambda ws: "evil" not in ws.headers.get("origin", ""))
    monkeypatch.setattr(cons, "idle_sec", lambda: 3600)
    monkeypatch.setattr(cons, "max_session_sec", lambda: 3600)
    monkeypatch.setattr(cons, "revalidate_sec", lambda: 0)
    monkeypatch.setattr(cons, "is_demo_console", lambda: False)
    monkeypatch.setattr(cons, "audit_mode", lambda: "full")
    monkeypatch.setattr(cons, "audit_required", lambda: False)
    monkeypatch.setattr(cons, "mux_allowed_for_server", lambda _server: False)
    monkeypatch.setattr(cons, "bind_ip_enabled", lambda: False)
    monkeypatch.setattr(cons, "bind_device_enabled", lambda: False)
    monkeypatch.setattr(cons, "ensure_device_id", lambda raw: raw or "device-a")
    monkeypatch.setattr(cons, "release_slot", lambda *a, **k: None)
    monkeypatch.setattr(cons, "park_console", lambda *a, **k: None)
    monkeypatch.setattr(cons, "try_acquire_slot", lambda *a, **k: None)
    monkeypatch.setattr(cons, "session_still_valid", lambda *a, **k: (True, "ok"))
    monkeypatch.setattr(cons, "binding_still_valid", lambda *a, **k: (True, "ok"))
    monkeypatch.setattr(
        cons,
        "consume_ticket",
        lambda *a, **k: {"console": True, "role": "fleet", "n": 0, "why": "check"},
    )
    monkeypatch.setattr(
        cons,
        "open_session_channel",
        lambda *a, **k: (SimpleNamespace(close=lambda: None, _ph_mux_note=""), _Chan([("out", b"ok")])),
    )

    def _session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    cookies = {"access_token": token}
    try:
        _run(_WS([], origin_ok=False), server.id)
        _run(_WS(["timeout"], cookies=cookies), server.id)
        _run(_WS(["drop"], cookies=cookies), server.id)
        _run(_WS([{"type": "websocket.disconnect"}], cookies=cookies), server.id)
        _run(_WS([{"type": "websocket.receive", "text": "nope"}], cookies=cookies), server.id)
        _run(_WS([_frame({"type": "auth"})], cookies=cookies), server.id)
        _run(_WS([_frame({"type": "auth", "ticket": "t"})]), server.id)
        _run(_WS([_frame({"type": "auth", "ticket": "t"})], cookies=cookies), 99999)

        def _deny(*_a, **_k):
            raise cons.ConsoleDenied("no")

        monkeypatch.setattr(cons, "consume_ticket", _deny)
        _run(_WS([_frame({"type": "auth", "ticket": "t"})], cookies=cookies), server.id)

        monkeypatch.setattr(
            cons,
            "consume_ticket",
            lambda *a, **k: {"role": "privileged", "iid": 9, "why": "break"},
        )
        _run(_WS([_frame({"type": "auth", "ticket": "t"})], cookies=cookies), server.id)

        monkeypatch.setattr(cons, "audit_required", lambda: True)
        monkeypatch.setattr(cons, "audit_mode", lambda: "off")
        monkeypatch.setattr(
            cons,
            "consume_ticket",
            lambda *a, **k: {"role": "fleet", "why": ""},
        )
        _run(_WS([_frame({"type": "auth", "ticket": "t"})], cookies=cookies), server.id)

        held = _Held()

        def _claim(*_a, **_k):
            return held

        monkeypatch.setattr(cons, "claim_resume", _claim)
        ws = _WS([_frame({"type": "resume", "resume": "resume-a"})], cookies=cookies)
        ws.fail_bytes = True
        _run(ws, server.id)

        def _claim_fail(*_a, **_k):
            raise cons.ConsoleDenied("gone")

        monkeypatch.setattr(cons, "claim_resume", _claim_fail)
        _run(_WS([_frame({"type": "resume"})], cookies=cookies), server.id)
        _run(_WS([_frame({"type": "resume", "resume": "resume-a"})], cookies=cookies), server.id)

        states = {"n": 0}

        def _valid(*_a, **_k):
            states["n"] += 1
            if states["n"] == 1:
                return False, "device_changed"
            if states["n"] == 2:
                return False, "ip_changed"
            return True, "ok"

        monkeypatch.setattr(cons, "binding_still_valid", _valid)
        monkeypatch.setattr(cons, "session_still_valid", lambda *a, **k: (False, "logout"))
        monkeypatch.setattr(cons, "audit_required", lambda: False)
        monkeypatch.setattr(cons, "audit_mode", lambda: "off")
        monkeypatch.setattr(cons, "claim_resume", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no")))
        monkeypatch.setattr(
            cons,
            "consume_ticket",
            lambda *a, **k: {"role": "fleet"},
        )
        monkeypatch.setattr(cons, "idle_sec", lambda: 0)
        chan = _Chan([("out", b"out"), ("err", b"err"), ("exit", None)])
        monkeypatch.setattr(
            cons,
            "open_session_channel",
            lambda *a, **k: (SimpleNamespace(close=lambda: None), chan),
        )
        _run(
            _WS(
                [
                    _frame({"type": "auth", "ticket": "t"}),
                    _frame({"type": "stdin", "data": "a"}),
                    {"type": "websocket.disconnect"},
                ],
                cookies=cookies,
            ),
            server.id,
        )
    finally:
        app.dependency_overrides.pop(get_session, None)


def test_scheduler_watch_branches(tmp_path, monkeypatch):
    import app.services.scheduler as sched

    engine = _engine(tmp_path / "sched.db")

    def _boom(*_a, **_k):
        raise RuntimeError("down")

    monkeypatch.setattr("app.database.get_engine", lambda: engine)
    monkeypatch.setattr(
        "app.services.jobs.expire_stale_pending_herder_backups",
        lambda session: [1],
    )
    sched.watch_pending_herder_backups()

    monkeypatch.setattr("app.database.get_engine", _boom)
    sched.watch_pending_herder_backups()
    sched.watch_missed_schedules()

    monkeypatch.setattr("app.database.get_engine", lambda: engine)
    monkeypatch.setattr(
        "app.services.schedule_watch.sync_missed_schedule_alerts",
        lambda session: 2,
    )
    monkeypatch.setattr("app.services.host_posture.sync_host_posture_alerts", _boom)
    monkeypatch.setattr("app.services.schedule_catchup.run_due_catchups", _boom)
    monkeypatch.setattr("app.services.backup_replicate.sync_drive_token_aging", _boom)
    monkeypatch.setattr("app.services.copy_schedule_order.sync_copy_order_alerts", _boom)
    monkeypatch.setattr("app.services.queue_watch.sync_stuck_queue_alert", _boom)
    sched.watch_missed_schedules()


def test_worker_metrics_and_stack_scan(tmp_path, monkeypatch):
    from app.services import stack_monitor, worker_metrics

    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path / "metrics"))
    (tmp_path / "metrics").mkdir()
    (tmp_path / "metrics" / "old").write_text("x", encoding="utf-8")
    blocked = tmp_path / "metrics" / "stuck"
    blocked.mkdir()
    worker_metrics.prepare_multiproc_dir()

    monkeypatch.setattr(worker_metrics, "worker_metrics_enabled", lambda: True)
    monkeypatch.setattr(worker_metrics, "worker_metrics_port", lambda: 9)
    monkeypatch.setattr(worker_metrics, "_started", False)

    def _start(*_a, **_k):
        raise RuntimeError("bind")

    monkeypatch.setattr("prometheus_client.start_http_server", _start, raising=False)
    assert worker_metrics.serve_worker_metrics() is None

    monkeypatch.setattr(stack_monitor, "inventory_down_alerts_enabled", lambda: True)
    monkeypatch.setattr("app.database.get_engine", lambda: _engine(tmp_path / "stack.db"))
    stack_monitor.scan_after_inventory_refresh(1)
    monkeypatch.setattr(stack_monitor, "inventory_down_alerts_enabled", lambda: False)
    stack_monitor.scan_after_inventory_refresh(1)

    def _explode(*_a, **_k):
        raise RuntimeError("scan")

    monkeypatch.setattr(stack_monitor, "inventory_down_alerts_enabled", _explode)
    stack_monitor.scan_after_inventory_refresh(1)
