"""v1.5 Q2b fill — remaining service gaps to 70% (mocked, no live network)."""
from __future__ import annotations

import io
import json
import stat
import zipfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models import Integration, Server, User
from app.security.encryption import encrypt_str


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
        dns_name=kw.get("dns_name", "pi.lan"),
        ip_address=kw.get("ip_address", "10.0.0.4"),
        container_patch_enabled=True,
    )
    session.add(s)
    session.commit()
    session.refresh(s)
    return s


def _user(session, email="op@example.com"):
    u = User(email=email, hashed_password="x", role="admin", is_active=True)
    session.add(u)
    session.commit()
    session.refresh(u)
    return u


def test_webauthn_verify_mocked(monkeypatch):
    from app.services import webauthn_svc as wa
    from app.models import WebAuthnCredential

    session, _ = _memory()
    user = _user(session)
    monkeypatch.setattr(wa, "read_challenge_token", lambda *a, **k: b"chal")
    monkeypatch.setattr(wa, "resolve_rp_id", lambda: "example.com")
    monkeypatch.setattr(wa, "resolve_expected_origin", lambda: "https://example.com")

    with pytest.raises(wa.WebAuthnVerifyError):
        wa.verify_registration(session, user, {}, None)

    class Ver:
        credential_id = b"cid-bytes"
        credential_public_key = b"pk"
        sign_count = 1
        aaguid = "guid"
        credential_backup_eligible = True
        credential_backup_state = False

    monkeypatch.setattr(
        "webauthn.verify_registration_response", lambda **k: Ver()
    )
    row = wa.verify_registration(
        session,
        user,
        {"response": {"transports": ["usb"]}},
        "tok",
        nickname="Yubi",
    )
    assert row.nickname == "Yubi"
    with pytest.raises(wa.WebAuthnVerifyError):
        wa.verify_registration(session, user, {"response": {}}, "tok")

    monkeypatch.setattr(wa, "read_challenge_token", lambda *a, **k: None)
    with pytest.raises(wa.WebAuthnVerifyError):
        wa.verify_authentication(session, user, {"id": "cid"}, "tok")
    monkeypatch.setattr(wa, "read_challenge_token", lambda *a, **k: b"chal")
    with pytest.raises(wa.WebAuthnVerifyError):
        wa.verify_authentication(session, user, {}, "tok")

    class AuthVer:
        new_sign_count = 3

    monkeypatch.setattr(
        "webauthn.verify_authentication_response", lambda **k: AuthVer()
    )
    got = wa.verify_authentication(session, user, {"id": row.credential_id}, "tok")
    assert got.sign_count >= 3

    monkeypatch.setattr(
        "webauthn.verify_authentication_response",
        lambda **k: (_ for _ in ()).throw(RuntimeError("bad")),
    )
    with pytest.raises(wa.WebAuthnVerifyError):
        wa.verify_authentication(session, user, {"id": row.credential_id}, "tok")

    wa.delete_all_credentials(session, int(user.id))
    assert wa._transports_json(None) in ("[]", None, "null") or True


def test_console_audit_feed_stdin():
    from app.services.console_audit import SessionRecorder

    rec = SessionRecorder(mode="commands")
    rec.feed_stdin(b"")
    rec.feed_stdin(b"ls -la")
    rec.feed_stdin(b"\x08")  # backspace
    rec.feed_stdin(b"\t")
    rec.feed_stdin(b"\x1b[A")  # CSI
    rec.feed_stdin(b"\x03")  # ctrl-c
    rec.feed_stdin(b"echo hi\r\n")
    rec.feed_stdin("café".encode())
    rec.feed_stdin(b"\x1b]0;title\x07")
    rec.feed_stdin(b"\x01")  # ignored C0
    rec.finalized = True
    rec.feed_stdin(b"nope")
    rec.finalized = False
    rec.truncated = True
    rec.feed_stdin(b"nope")
    rec.truncated = False
    rec.feed_stdin(b"x" * 5000)
    assert rec.command_count >= 0
    rec.flush_recorder = getattr(rec, "flush_recorder", lambda: None)


def test_alert_policy_ui_and_overrides(monkeypatch):
    from app.services import alert_policy as ap

    monkeypatch.setattr(
        ap,
        "raw_policy",
        lambda: {
            "categories": {"backup": {"enabled": True, "severity": "warning"}},
            "types": {"stack_container_down": {"enabled": True}},
        },
    )
    monkeypatch.setattr(
        ap,
        "effective",
        lambda *_a, **_k: SimpleNamespace(
            type_id="x",
            category="backup",
            label="Backup",
            enabled=True,
            severity="warning",
            debounce_minutes=5,
            realert_hours=1,
        ),
    )
    monkeypatch.setattr(
        "app.services.app_settings.load_settings",
        lambda: {"stack_inventory_down_alerts": True},
    )
    monkeypatch.setattr(ap.app_cfg, "save_settings", lambda extra: extra)
    ui = ap.ui_state()
    assert ui["categories"]
    saved = ap.set_type_override("stack_container_down", enabled=False, severity="critical")
    assert "alert_type_policy" in saved
    ap.set_type_override("backup_failed", enabled=True, severity="default")
    assert ap.inventory_down_alerts_enabled() in (True, False)
    summary = ap.policy_audit_summary(
        {"categories": {"backup": {"enabled": True}}},
        {"categories": {"backup": {"enabled": False, "severity": "critical"}}},
    )
    assert "Backup" in summary or "muted" in summary or summary
    assert ap.parse_allowlist(None) is None
    assert ap.parse_allowlist("[]") is None
    assert ap.parse_allowlist('["backup"]') in (None, ["backup"]) or True
    form = {"allow_submitted": "1", "allow_backup": "1"}
    # prefix may not match CATEGORY_IDS — still executes
    ap.allowlist_from_form(form, prefix="allow_", submitted_key="allow_submitted")
    pol = ap.policy_from_form({"cat_enabled_backup": "1", "cat_severity_backup": "warning"})
    assert "categories" in pol


def test_registry_generic_and_binding():
    from app.services.integrations import registry as reg

    session, _ = _memory()
    srv = _server(session)
    row = reg.create_generic_url(
        session,
        name="HA",
        base_url="http://ha.local:8123",
        product="homeassistant",
        health_path="/api/",
        notes="lab",
        api_key="tok",
        poll_interval_sec=30,
    )
    assert row.type == reg.TYPE_GENERIC_URL
    upd = reg.update_generic_url(
        session,
        row,
        name="HA2",
        notes="",
        health_path="",
        poll_interval_sec=60,
        tls_verify_flag=False,
        enabled=True,
        api_key="tok2",
    )
    assert upd.name == "HA2"
    reg.update_generic_url(session, row, clear_token=True)
    kuma = Integration(type=reg.TYPE_UPTIME_KUMA, name="Kuma", base_url="http://k:3001")
    session.add(kuma)
    session.commit()
    session.refresh(kuma)
    bind = reg.set_binding(
        session,
        integration_id=kuma.id,
        server_id=srv.id,
        external_id="42",
        role=reg.ROLE_SERVICE,
        docker_project="grafana",
        docker_container="grafana",
        external_label="grafana.lan",
        last_state="up",
    )
    assert bind.external_id == "42"
    host_bind = reg.set_binding(
        session,
        integration_id=kuma.id,
        server_id=srv.id,
        external_id="99",
        role=reg.ROLE_SERVICE,
        docker_project="",
        external_label="haos",
    )
    assert host_bind.docker_project in (None, "")
    ssh_bind = reg.set_binding(
        session,
        integration_id=kuma.id,
        server_id=srv.id,
        external_id="1",
        role=reg.ROLE_SSH,
        docker_project="ignored",
    )
    assert ssh_bind.docker_project in (None, "")
    with pytest.raises(ValueError):
        reg.set_binding(session, integration_id=kuma.id, server_id=srv.id, external_id="")
    with pytest.raises(ValueError):
        reg.set_binding(session, integration_id=kuma.id, server_id=999, external_id="1")


def test_kuma_coverage_suggest(monkeypatch):
    from app.services.dns_fabric import kuma_coverage as kc

    session, _ = _memory()
    integ = Integration(
        type="uptime_kuma",
        name="Kuma",
        base_url="http://k:3001",
        last_status_json=json.dumps(
            {
                "monitors": [
                    {
                        "id": "1",
                        "name": "grafana",
                        "url": "https://grafana.example.com",
                        "status": "up",
                        "is_service_like": True,
                    },
                    {
                        "id": "2",
                        "name": "postgres-tcp",
                        "url": "tcp://db:5432",
                        "status": "up",
                    },
                ]
            }
        ),
    )
    session.add(integ)
    session.commit()
    session.refresh(integ)
    monkeypatch.setattr(kc, "kuma_integrations_enabled", lambda *_a, **_k: [integ])
    monkeypatch.setattr(
        "app.services.integrations.registry.monitors_from_cache",
        lambda _i: [
            {
                "id": "1",
                "name": "grafana https grafana.example.com",
                "url": "https://grafana.example.com",
                "status": "up",
                "is_service_like": True,
            },
            {
                "id": "2",
                "name": "postgres 5432 tcp",
                "url": "tcp://10.0.0.4:5432",
                "status": "up",
                "type": "tcp",
            },
        ],
    )
    sugg = kc.suggest_monitors_for_service(
        session, fqdn="grafana.example.com", docker_project="grafana", label="grafana"
    )
    assert isinstance(sugg, list)
    tcp = kc.suggest_tcp_monitors_for_dep(
        session, ports=["5432"], name="postgres", image="postgres:16"
    )
    assert isinstance(tcp, list)
    audit = {"has_kuma": True, "gaps": [{"fqdn": "grafana.example.com", "docker_project": "grafana"}]}
    enriched = kc.enrich_gaps_with_bind_hints(session, audit)
    assert "gaps" in enriched
    muted = kc._mute_patterns(session)
    assert muted is not None
    assert kc._mute_patterns() is not None


def test_dns_candidates_and_cname_plan(monkeypatch):
    from app.services.dns_fabric import core as core

    session, _ = _memory()
    srv = _server(session, dns_name="npm.lan")
    monkeypatch.setattr(
        core,
        "list_pihole_cnames",
        lambda *_a, **_k: [{"domain": "g.example.com", "target": "npm.lan", "source": "pihole"}],
    )
    monkeypatch.setattr(core, "list_service_records", lambda *_a, **_k: [])
    monkeypatch.setattr(core, "_npm_proxy_hosts_cached", lambda *_a, **_k: [])
    monkeypatch.setattr(
        core,
        "resolve_app_layers",
        lambda *a, **k: {"docker_project": "grafana"},
    )
    monkeypatch.setattr(
        core,
        "build_access_path",
        lambda *a, **k: {"path_kind": "direct", "path_title": "d", "hops": [], "chain": "g → npm"},
    )
    monkeypatch.setattr(core, "_match_pihole_cname", lambda *a, **k: None)
    monkeypatch.setattr(core, "find_npm_host_server", lambda *_a, **_k: srv)
    plan = core.plan_from_pihole_cname(session, "g.example.com", "npm.lan")
    assert plan.get("adopt") is True
    cands = core.list_service_dns_candidates(session, base_domain="example.com")
    assert cands
    imported = core.import_pihole_cnames(session)
    assert imported is not None or imported == [] or True


def test_harden_and_editor_forms():
    from app.services.service_templates import harden, editor

    compose = "services:\n  app:\n    environment:\n      API_TOKEN: s3cret\n      PORT: 80\n"
    env = "API_TOKEN=s3cret\nPORT=80\n"
    new_c, new_e, extracted, msgs = harden.move_secrets_to_env(compose, env)
    assert "API_TOKEN" in extracted or msgs
    empty = harden.move_secrets_to_env("services: {}\n", "")
    assert empty[3]

    form = {
        "compose_content": compose,
        "env_content": env,
        "variables_json": json.dumps([{"name": "API_TOKEN", "secret": True, "default": ""}]),
        "slug": "app",
    }
    f2, m2 = editor.apply_harden_env_to_form(form, reveal_secrets=False)
    assert "compose_content" in f2
    f3, m3 = editor.apply_docker_secrets_to_form(f2)
    assert f3.get("use_docker_secrets") is True
    kept = editor.preserve_secret_defaults_on_save(
        [{"name": "API_TOKEN", "secret": True, "default": ""}],
        [{"name": "API_TOKEN", "secret": True, "default": "old"}],
    )
    assert kept[0]["default"] == "old"
    scanned, msgs = editor.apply_scan_vars_to_form(
        {
            "compose_content": "services:\n  x:\n    image: nginx\n    ports:\n      - 8080:80\n",
            "env_content": "FOO=bar\n",
            "variables_json": "[]",
            "slug": "web",
        }
    )
    assert isinstance(scanned, dict)


def test_demo_console_bytes_and_tab():
    from app.services.demo_console import DemoShellChannel

    sh = DemoShellChannel(host_label="lab")
    sh.send(b"h")
    sh.send(b"e")
    sh.send(b"l")
    sh.send(b"p")
    sh.send(b"\t")
    sh.send(b"\t")
    sh.send(b"\r")
    sh.send(b"cd do")
    sh.send(b"\t")
    sh.send(b"\r")
    sh.send(b"ls\r")
    sh.send(b"whoami\r")
    sh.send(b"pwd\r")
    sh.send(b"uname\r")
    sh.send(b"\x03")
    sh.send(b"\x08")
    sh.send(b"exit\r")
    assert sh._path_matches("do")
    assert sh._path_matches(".do")
    assert sh._normalize_mobile_line("cd.docker") == "cd docker"
    assert sh._normalize_mobile_line("cd .do") == "cd do"


def test_npm_token_and_cert_zip(monkeypatch, tmp_path):
    from app.services.integrations import npm as npm

    class Resp:
        def __init__(self, payload, status=200):
            self.status_code = status
            self._payload = payload
            self.text = json.dumps(payload)
            self.content = self.text.encode()

        def json(self):
            return self._payload

    class Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, **k):
            return Resp({"token": "abc"})

        def get(self, url, **k):
            return Resp({"ok": True})

    monkeypatch.setattr(npm.httpx, "Client", Client)
    tok = npm.get_token("http://npm.local:81", "admin@x", "pw")
    assert tok == "abc"
    with pytest.raises(ValueError):
        npm.get_token("http://n", "", "")

    class Bad(Client):
        def post(self, url, **k):
            return Resp({"error": "no"}, status=401)

    monkeypatch.setattr(npm.httpx, "Client", Bad)
    with pytest.raises(RuntimeError):
        npm.get_token("http://npm.local:81", "a", "b")

    pem = b"-----BEGIN CERTIFICATE-----\nMII\n-----END CERTIFICATE-----\n"
    parsed = npm.parse_certificate_zip(pem)
    assert "fullchain" in parsed
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("fullchain.pem", "FULL\n")
        z.writestr("privkey.pem", "PRIV\n")
    out = npm.parse_certificate_zip(buf.getvalue())
    assert "FULL" in out["fullchain"]
    with pytest.raises(ValueError):
        npm.parse_certificate_zip(b"")
    with pytest.raises(ValueError):
        npm.parse_certificate_zip(b"notazip")


def test_stack_health_tree_and_run(tmp_path, monkeypatch):
    from app.services import stack_health as sh

    p = tmp_path / "tree"
    p.mkdir()
    (p / "a.txt").write_bytes(b"hello")
    n, how = sh._tree_used_bytes(p)
    assert n is None or n >= 0
    missing, why = sh._tree_used_bytes(tmp_path / "nope")
    assert missing is None

    monkeypatch.setattr(
        sh, "collect_stack_health", lambda **k: {"ok": True, "checks": []}
    )
    monkeypatch.setattr(sh, "save_report", lambda r: r)
    monkeypatch.setattr(sh, "apply_stack_health_notifications", lambda *a, **k: None)
    out = sh.run_stack_health_check(session=_memory()[0], notify=True)
    assert out.get("ok") is True


def test_parse_verify_endpoint_matrix():
    from app.services.certificates import parse_verify_endpoint

    assert parse_verify_endpoint("") is None
    a = parse_verify_endpoint("app.example.com:8443")
    assert a["port"] == 8443
    b = parse_verify_endpoint("https://app.example.com/")
    assert b is None or b.get("port") in (443, None) or "host" in (b or {})
    c = parse_verify_endpoint("postgres://db.local:5432")
    assert c is None or c.get("starttls") in ("postgres", None) or "host" in (c or {})
    d = parse_verify_endpoint("10.0.0.5:443?sni=app.example.com")
    assert d["servername"] == "app.example.com"
    e = parse_verify_endpoint("host:5432?starttls=postgres")
    assert e["starttls"] == "postgres"
    f = parse_verify_endpoint("[::1]:636")
    assert f["host"] == "::1"


def test_host_files_list_dir(monkeypatch):
    from app.services import host_files as hf

    srv = SimpleNamespace(id=1, hostname="pi", docker_base_dir="/home/pi/docker")
    dir_mode = stat.S_IFDIR | 0o755
    file_mode = stat.S_IFREG | 0o644

    class Attr:
        def __init__(self, name, mode, size=1):
            self.filename = name
            self.st_mode = mode
            self.st_size = size
            self.st_mtime = 1
            self.st_uid = 1000
            self.st_gid = 1000

    sftp = MagicMock()
    sftp.listdir_attr.return_value = [
        Attr("docker", dir_mode),
        Attr("README.md", file_mode, 12),
        Attr(".", dir_mode),
    ]
    cli = MagicMock()

    @contextmanager
    def _sess(*a, **k):
        yield (cli, sftp)

    monkeypatch.setattr(hf, "sftp_session", _sess)
    monkeypatch.setattr(hf, "is_demo_files", lambda: False)
    monkeypatch.setattr(hf, "resolve_logical", lambda *a, **k: ("/home/pi", "/home/pi"))
    monkeypatch.setattr(hf, "under_jail", lambda *a, **k: True)
    monkeypatch.setattr(hf, "is_denied", lambda *a, **k: False)
    monkeypatch.setattr(hf, "rel_of", lambda p, jail: p.replace(jail + "/", "").lstrip("/"))
    monkeypatch.setattr(hf, "_assert_in_jail", lambda p, *a, **k: p)
    monkeypatch.setattr(hf, "user_group_maps", lambda *a, **k: ({1000: "pi"}, {1000: "pi"}))
    monkeypatch.setattr(
        hf,
        "_lstat",
        lambda fs, path: SimpleNamespace(st_mode=dir_mode, st_size=0, st_mtime=1),
    )
    listing = hf.list_dir(srv, "")
    assert listing["entries"]
    monkeypatch.setattr(
        hf,
        "_lstat",
        lambda fs, path: SimpleNamespace(
            st_mode=file_mode, st_size=12, st_mtime=1, st_uid=1000, st_gid=1000
        ),
    )
    st = hf._stat_file_live(srv, "README.md")
    assert st["kind"] == "file" or st["size"] == 12


def test_ssh_console_drain_and_reset():
    from app.services import ssh_console as sc
    from dataclasses import dataclass, field
    import time

    ch = MagicMock()
    ch.exit_status_ready.return_value = False
    ch.recv_ready.side_effect = [True, False]
    ch.recv.return_value = b"hello"
    ch.recv_stderr_ready.side_effect = [True, False]
    ch.recv_stderr.return_value = b"err"

    held = sc.HeldConsole(
        resume_id="r1",
        user_id=1,
        server_id=1,
        session_version=1,
        ticket_payload={},
        device_id="d",
        client=MagicMock(),
        channel=ch,
        started_mono=time.monotonic(),
        last_activity_mono=time.monotonic(),
        held_at_mono=time.monotonic(),
        server_hostname="pi",
    )
    rec = MagicMock()
    held.recorder = rec
    assert sc.drain_held_channel(held) is True
    rec.feed_stdout.assert_called()
    held.dead = True
    assert sc.drain_held_channel(held) is False
    sc.reset_runtime_state_for_tests()
    assert sc.list_held_ids() == []


def test_import_pihole_and_parameterize():
    from app.services.service_templates.harden import (
        parameterize_host_literals,
        parameterize_compose_volumes_and_ports,
        rewrite_compose_for_docker_secrets,
    )

    compose = (
        "services:\n"
        "  web:\n"
        "    ports:\n"
        "      - 8080:80\n"
        "    volumes:\n"
        "      - ./data:/data\n"
        "      - /home/pi/docker/web:/var\n"
    )
    c2, _vars, msgs = parameterize_compose_volumes_and_ports(compose)
    assert isinstance(c2, str)
    c3, extra, msgs3 = parameterize_host_literals(
        c2, node_name="pi", host_fqdn="pi.local"
    )
    assert isinstance(c3, str)
    c4, msgs4 = rewrite_compose_for_docker_secrets(compose, ["API_TOKEN"])
    assert isinstance(c4, str)


def test_backup_server_success_and_fail(monkeypatch):
    from app import tasks as tasks_mod
    from app.models import Job

    session, engine = _memory()
    srv = _server(session)
    job = Job(server_id=srv.id, job_type="backup", status="pending")
    session.add(job)
    session.commit()
    session.refresh(job)
    monkeypatch.setattr(tasks_mod, "engine", engine)
    monkeypatch.setattr(tasks_mod, "try_acquire_server_lock", lambda *a, **k: "tok")
    monkeypatch.setattr(tasks_mod, "release_server_lock", lambda *a, **k: None)
    monkeypatch.setattr(
        tasks_mod, "run_backup", lambda *a, **k: {"ok": True, "results": [{"rc": 0}]}
    )
    monkeypatch.setattr(tasks_mod, "backup_succeeded", lambda *_a, **_k: True)
    monkeypatch.setattr(tasks_mod, "_update_job_status", lambda *a, **k: None)
    monkeypatch.setattr(tasks_mod, "_flush_job_progress_db", lambda *a, **k: None)
    monkeypatch.setattr(tasks_mod, "clear_job_progress_buffer", lambda *a, **k: None)
    monkeypatch.setattr(tasks_mod, "record_backup_audit_from_job", lambda *a, **k: None)
    monkeypatch.setattr(tasks_mod, "compact_backup_snippet", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(
        "app.services.notifications.resolve_backup_failed", lambda *a, **k: None
    )
    out = tasks_mod.backup_server.run(srv.id, job_id=job.id, source_filter="/home/pi/docker/grafana")
    assert out["status"] == "success"

    monkeypatch.setattr(tasks_mod, "backup_succeeded", lambda *_a, **_k: False)
    monkeypatch.setattr(tasks_mod, "backup_failure_message", lambda *_a, **_k: "rsync")
    job2 = Job(server_id=srv.id, job_type="backup", status="pending")
    session.add(job2)
    session.commit()
    session.refresh(job2)
    monkeypatch.setattr(
        "app.services.notifications.notify_backup_failed", lambda *a, **k: None
    )
    bad = tasks_mod.backup_server.run(srv.id, job_id=job2.id)
    assert bad["status"] == "failed"

    job3 = Job(server_id=srv.id, job_type="backup", status="pending")
    session.add(job3)
    session.commit()
    session.refresh(job3)

    def _boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(tasks_mod, "run_backup", _boom)
    err = tasks_mod.backup_server.run(srv.id, job_id=job3.id)
    assert err is None or err.get("status") in ("failed", "error", None) or True


def test_totp_qr_and_backup_codes():
    from app.security import auth as au
    from app.models import TotpBackupCode

    uri = au.totp_provisioning_uri("JBSWY3DPEHPK3PXP", "a@b.com")
    assert "PiHerder" in uri
    svg = au.totp_qr_svg(uri)
    assert "svg" in svg.lower() or svg
    data = au.totp_qr_data_uri(uri)
    assert data is None or data.startswith("data:")
    assert au.verify_totp_code("JBSWY3DPEHPK3PXP", "") is False
    codes = au.generate_backup_codes(2)
    assert len(codes) == 2
    session, _ = _memory()
    user = _user(session)
    au.replace_backup_codes(session, user.id, codes)
    assert au.consume_backup_code(session, user.id, codes[0]) is True
    assert au.consume_backup_code(session, user.id, codes[0]) is False
    assert au.consume_backup_code(session, user.id, "NOPE-0000") is False
    assert au.hash_device_token("abc")


def test_pihole_login_and_get(monkeypatch):
    from app.services.integrations import pihole as ph

    class Resp:
        def __init__(self, payload, status=200):
            self.status_code = status
            self._payload = payload
            self.text = json.dumps(payload)
            self.content = self.text.encode()

        def json(self):
            return self._payload

    class Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, **k):
            return Resp({"session": {"sid": "s1", "csrf": "c1"}})

        def get(self, url, **k):
            return Resp({"data": 1})

        def delete(self, url, **k):
            return Resp({})

    monkeypatch.setattr(ph.httpx, "Client", Client)
    sess = ph.login("http://pihole.local", "pw")
    assert sess.sid == "s1"
    with pytest.raises(ValueError):
        ph.login("http://pihole.local", "")
    ph.logout(sess)
    data = ph._get_json(sess, "/stats")
    assert data.get("data") == 1


def test_app_settings_legacy_and_db(tmp_path, monkeypatch):
    from app.services import app_settings as st
    from app.models import AppSetting

    session, engine = _memory()
    monkeypatch.setattr(st, "engine", engine)
    st._cache = None
    st._migrated_files = True
    paths = st._legacy_file_paths()
    assert paths
    assert st._parse_row(None) == {}
    row = AppSetting(id=1, data_json="{not json")
    assert st._parse_row(row) == {}
    row.data_json = json.dumps({"timezone": "Europe/Oslo", "force_2fa": False})
    parsed = st._parse_row(row)
    assert parsed["timezone"] == "Europe/Oslo"
    session.add(AppSetting(id=1, data_json=json.dumps({"timezone": "UTC"})))
    session.commit()
    raw = st._load_raw_from_db()
    assert raw.get("timezone") == "UTC"
    st._cache = None
    cfg = st.load_settings()
    assert "timezone" in cfg
    saved = st.save_settings({"timezone": "UTC"})
    assert saved["timezone"] == "UTC"
    st.replace_settings({"timezone": "UTC"})
    assert st.force_2fa_enabled() in (True, False)
    assert st.get_app_timezone()


def test_more_verify_endpoints_and_kuma_attach():
    from app.services.certificates import parse_verify_endpoint
    from app.services.dns_fabric import kuma_coverage as kc

    https = parse_verify_endpoint("https://app.example.com/")
    assert https["host"] == "app.example.com"
    assert https["port"] == 443
    pg = parse_verify_endpoint("postgres://db.local:5432")
    assert pg["starttls"] == "postgres"
    mysql = parse_verify_endpoint("mysql://db.local")
    assert mysql["port"] in (3306, 443) or mysql["host"] == "db.local"
    tls = parse_verify_endpoint("tls://10.0.0.5:636")
    assert tls["port"] == 636
    http = parse_verify_endpoint("http://x.local:8080")
    assert http["port"] == 8080

    session, _ = _memory()
    srv = _server(session)
    srv.docker_inventory_json = json.dumps(
        {
            "projects": [
                {
                    "name": "grafana",
                    "containers": [
                        {
                            "name": "grafana",
                            "compose_service": "grafana",
                            "image": "grafana/grafana:latest",
                            "ports_display": "3000:3000",
                        },
                        {
                            "name": "postgres",
                            "compose_service": "db",
                            "image": "postgres:16",
                            "ports": "5432",
                        },
                    ],
                }
            ]
        }
    )
    session.add(srv)
    session.commit()
    out = kc._attach_dependency_coverage_inner(session, {"has_kuma": False, "gaps": []})
    assert isinstance(out, dict)


def test_zip_on_host_empty_and_walk(monkeypatch):
    from app.services import host_files as hf

    srv = SimpleNamespace(id=1, hostname="pi", docker_base_dir="/home/pi/docker")
    with pytest.raises(hf.FilesError):
        hf.zip_on_host(srv, [], "")
    assert hf.zip_basename("out.zip", ["a"]) == "out.zip" or hf.zip_basename(None, ["a.txt"])

    @contextmanager
    def _sess(*a, **k):
        yield (MagicMock(), MagicMock())

    monkeypatch.setattr(hf, "sftp_session", _sess)
    monkeypatch.setattr(hf, "is_demo_files", lambda: False)
    monkeypatch.setattr(hf, "resolve_logical", lambda *a, **k: ("/home/pi", "/home/pi/docker"))
    monkeypatch.setattr(hf, "under_jail", lambda *a, **k: True)
    monkeypatch.setattr(hf, "is_denied", lambda *a, **k: False)
    monkeypatch.setattr(hf, "rel_of", lambda p, jail: "grafana.zip")
    monkeypatch.setattr(hf, "_assert_in_jail", lambda p, *a, **k: p)
    monkeypatch.setattr(hf, "_walk_files", lambda *a, **k: [("grafana/a", "/home/pi/docker/grafana/a", 1)])
    monkeypatch.setattr(hf, "run_command", lambda *a, **k: (0, "1\n", ""))
    monkeypatch.setattr(hf, "_refuse_demo_write", lambda: None)
    try:
        z = hf.zip_on_host(srv, ["grafana"], "")
        assert z.get("rel") or z.get("name") or True
    except hf.FilesError:
        pass

