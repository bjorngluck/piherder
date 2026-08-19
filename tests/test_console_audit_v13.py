"""v1.3 slice 5 Deep — W-audit command capture + transcripts."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.database import get_session
from app.main import app
from app.models import ConsoleTranscript, User
from app.security.auth import create_access_token, get_password_hash
from app.services import app_settings as cfg
from app.services import console_audit as ca
from app.services import ssh_console as cons


@pytest.fixture()
def engine(tmp_path):
    eng = create_engine(
        f"sqlite:///{tmp_path / 'waudit.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture()
def client(engine, monkeypatch):
    store: dict = {}

    def fake_load():
        return dict(store)

    def fake_write(data: dict):
        store.clear()
        store.update(data)

    monkeypatch.setattr(cfg, "_load_raw_from_db", fake_load)
    monkeypatch.setattr(cfg, "_write_raw_to_db", fake_write)
    cfg.clear_cache()
    monkeypatch.setattr(cons.settings, "PIHERDER_SSH_CONSOLE", True)
    cons.reset_runtime_state_for_tests()

    def _session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    tc = TestClient(app, raise_server_exceptions=False)
    try:
        yield tc, engine
    finally:
        app.dependency_overrides.clear()
        cons.reset_runtime_state_for_tests()
        cfg.clear_cache()


def _user(session: Session, *, role: str = "admin", email: str = "audit@test.local") -> User:
    u = User(
        email=email,
        hashed_password=get_password_hash("SmokeTest1ok"),
        role=role,
        is_active=True,
        must_change_password=False,
        totp_enabled=True,
    )
    session.add(u)
    session.commit()
    session.refresh(u)
    return u


def _cookie(uid: int) -> dict:
    return {"access_token": create_access_token({"sub": str(uid)})}


def test_line_editor_backspace_then_command():
    rec = ca.SessionRecorder(mode=ca.MODE_COMMANDS)
    rec.feed_stdin(b"lx")
    rec.feed_stdin(b"\x7f")
    rec.feed_stdin(b"s -la\r")
    cmds = [e["text"] for e in rec.events() if e["kind"] == "cmd"]
    assert cmds == ["ls -la"]


def test_tab_completion_uses_echo_line():
    rec = ca.SessionRecorder(mode=ca.MODE_COMMANDS)
    rec.feed_stdin(b"do\t")
    rec.feed_stdout(b"docker")
    rec.feed_stdin(b"\r")
    cmds = [e["text"] for e in rec.events() if e["kind"] == "cmd"]
    assert cmds == ["docker"]


def test_password_prompt_redacts_secret():
    rec = ca.SessionRecorder(mode=ca.MODE_COMMANDS)
    rec.feed_stdout(b"Password: ")
    rec.feed_stdin(b"hunter2\r")
    body = rec.dumps()
    assert "hunter2" not in body
    cmds = [e for e in rec.events() if e["kind"] == "cmd"]
    assert cmds[0]["text"] == "[redacted]"
    assert cmds[0]["reason"] == "password_prompt"


def test_sudo_password_prompt():
    rec = ca.SessionRecorder(mode=ca.MODE_COMMANDS)
    rec.feed_stdout(b"[sudo] password for bjorn: ")
    rec.feed_stdin(b"secret-pass\r")
    assert "secret-pass" not in rec.dumps()


def test_pem_and_token_redacted_in_output():
    rec = ca.SessionRecorder(mode=ca.MODE_COMMANDS_OUTPUT)
    rec.feed_stdin(b"cat key\r")
    rec.feed_stdout(
        b"-----BEGIN RSA PRIVATE KEY-----\nMIIFAKEKEY\n-----END RSA PRIVATE KEY-----\n"
        b"token ghp_abcdefghijklmnopqrstuvwxyz123456\n"
    )
    rec.feed_stdin(b"exit\r")
    body = rec.dumps()
    assert "BEGIN RSA PRIVATE KEY" not in body
    assert "MIIFAKEKEY" not in body
    assert "ghp_abcdefghijklmnopqrstuvwxyz123456" not in body
    assert "[redacted-pem]" in body
    assert "[redacted-token]" in body


def test_event_cap_sets_truncated(monkeypatch):
    monkeypatch.setattr(ca, "EVENTS_MAX", 8)
    rec = ca.SessionRecorder(mode=ca.MODE_COMMANDS)
    for i in range(20):
        rec.feed_stdin(f"cmd{i}\r".encode())
    assert rec.truncated is True
    assert rec.command_count <= 8


def test_feed_8kib_is_cpu_only():
    rec = ca.SessionRecorder(mode=ca.MODE_COMMANDS_OUTPUT)
    blob = b"x" * 8192
    t0 = time.perf_counter()
    rec.feed_stdout(blob)
    rec.feed_stdin(b"echo hi\r")
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.05


def test_close_details_metadata_only():
    rec = ca.SessionRecorder(mode=ca.MODE_COMMANDS)
    rec.transcript_id = 9
    rec.feed_stdin(b"whoami\r")
    details = ca.close_details(rec, "duration_sec=12 ip=1.2.3.4")
    assert "whoami" not in details
    assert "transcript_id=9" in details
    assert "cmds=1" in details


def test_audit_mode_required_clamps_off(monkeypatch, _unused=None):
    store: dict = {}

    def fake_load():
        return dict(store)

    def fake_write(data: dict):
        store.clear()
        store.update(data)

    monkeypatch.setattr(cfg, "_load_raw_from_db", fake_load)
    monkeypatch.setattr(cfg, "_write_raw_to_db", fake_write)
    cfg.clear_cache()
    cfg.save_settings({"console_audit_mode": "off", "console_audit_required": True})
    assert cons.audit_mode_setting() == "off"
    assert cons.audit_required() is True
    assert cons.audit_mode() == "commands"
    cfg.clear_cache()


def test_unknown_mode_clamps_off():
    assert cons.clamp_console_policy({"console_audit_mode": "ttyrec"})[
        "console_audit_mode"
    ] == "off"
    assert cons.clamp_console_policy({"console_audit_mode": "commands_output"})[
        "console_audit_mode"
    ] == "commands_output"


def test_persist_round_trip_and_purge(engine):
    rec = ca.SessionRecorder(mode=ca.MODE_COMMANDS)
    rec.feed_stdin(b"uptime\r")
    with Session(engine) as s:
        started = ca.start_session(
            s,
            session_key="sess-1",
            user_id=1,
            server_id=2,
            mode=ca.MODE_COMMANDS,
        )
        assert started is not None
        started.feed_stdin(b"uptime\r")
        tid = ca.flush_recorder(s, started, finalize=True)
        row = s.get(ConsoleTranscript, tid)
        assert row is not None
        assert row.body_encrypted
        ev = ca.decrypt_events(row)
        assert any(e.get("text") == "uptime" for e in ev if e.get("kind") == "cmd")
        n = ca.purge_transcript_bodies(s, older_than_days=1)
        # too new to purge
        assert n == 0
        row.updated_at = row.updated_at.replace(year=2020)
        s.add(row)
        s.commit()
        n = ca.purge_transcript_bodies(s, older_than_days=1)
        s.refresh(row)
        assert n == 1
        assert not row.body_encrypted
        assert row.purged_at is not None
        assert row.command_count >= 1
        assert ca.decrypt_events(row) == []


def test_demo_never_starts(monkeypatch, engine):
    monkeypatch.setattr(cons, "is_demo_console", lambda: True)
    monkeypatch.setattr(cons, "audit_mode", lambda: "commands")
    with Session(engine) as s:
        rec = ca.start_session(s, session_key="demo", user_id=1, server_id=1)
        assert rec is None
        assert s.exec(select(ConsoleTranscript)).first() is None


def test_mode_off_never_starts(monkeypatch, engine):
    monkeypatch.setattr(cons, "is_demo_console", lambda: False)
    monkeypatch.setattr(cons, "audit_mode", lambda: "off")
    with Session(engine) as s:
        rec = ca.start_session(s, session_key="off", user_id=1, server_id=1)
        assert rec is None


def test_viewer_transcript_403(client):
    tc, engine = client
    with Session(engine) as s:
        op = _user(s, role="operator", email="op-t@test.local")
        viewer = _user(s, role="viewer", email="view-t@test.local")
        op_id, view_id = int(op.id), int(viewer.id)
        rec = ca.start_session(
            s,
            session_key="http-1",
            user_id=op_id,
            server_id=None,
            mode=ca.MODE_COMMANDS,
        )
        rec.feed_stdin(b"id\r")
        tid = ca.flush_recorder(s, rec, finalize=True)
    r = tc.get(f"/audit/console/{tid}", cookies=_cookie(view_id))
    assert r.status_code == 403
    r2 = tc.get(f"/audit/console/{tid}", cookies=_cookie(op_id))
    assert r2.status_code == 200
    assert "id" in r2.text


def test_policy_summary_includes_audit(_memory_settings=None, monkeypatch=None):
    p = cons.clamp_console_policy({})
    s = cons.console_policy_summary(p)
    assert "audit=off" in s
    assert "audit_req=0" in s
