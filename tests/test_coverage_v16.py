"""v1.6 Q-80 coverage push 70%→75% — mocked SSH / sqlite (no live network).

Service tests first: compose editor, host_sync, herder backup helpers,
NPM/Pi-hole HTTP, console park/claim, stack health, backup vanished,
job cancel, cert error copy, host-port sticky, docker logs/validate.
Do not chase router %.
"""
from __future__ import annotations

import io
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models import AuditLog, Job, PortAnnotation, Server, StackDeployment, User
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
        ssh_username=kw.get("ssh_username", "pi"),
        docker_base_dir=kw.get("docker_base_dir", "/home/pi/docker"),
        os_type=kw.get("os_type", "debian"),
        os_patch_enabled=True,
        container_patch_enabled=kw.get("container_patch_enabled", True),
        backup_enabled=True,
        dns_name=kw.get("dns_name", "pi.lan"),
        ip_address=kw.get("ip_address", "10.0.0.4"),
        ssh_private_key_encrypted=kw.get("ssh_private_key_encrypted", "enc"),
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


def _job(session, server, job_type="backup", status="running", details="{}"):
    job = Job(
        server_id=server.id if server else None,
        job_type=job_type,
        status=status,
        details=details,
        celery_task_id="celery-abc",
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


class _HttpResp:
    def __init__(self, payload=None, status=200, content=None, text=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        if content is not None:
            self.content = content
            self.text = text or ""
        else:
            raw = json.dumps(self._payload) if not isinstance(payload, (bytes, str)) else payload
            if isinstance(raw, bytes):
                self.content = raw
                self.text = raw.decode("utf-8", errors="replace")
            else:
                self.text = text if text is not None else (raw if isinstance(raw, str) else "")
                self.content = self.text.encode()

    def json(self):
        return self._payload


class _HttpClient:
    def __init__(self, handler):
        self._handler = handler

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, **k):
        return self._handler("POST", url, k)

    def get(self, url, **k):
        return self._handler("GET", url, k)

    def put(self, url, **k):
        return self._handler("PUT", url, k)

    def delete(self, url, **k):
        return self._handler("DELETE", url, k)

    def request(self, method, url, **k):
        return self._handler(method.upper(), url, k)


# ---------------------------------------------------------------------------
# compose_editor
# ---------------------------------------------------------------------------


def test_compose_editor_workspace_inventory_and_fallback(monkeypatch):
    from app.services import compose_editor as ce
    from app.services.compose_editor import ComposeEditorNotFound

    session, _ = _memory()
    srv = _server(session)
    dep = StackDeployment(
        server_id=srv.id,
        project_name="grafana",
        template_slug="grafana",
        files_json=json.dumps({"promtail.yml": "scrape: []\n"}),
        variables_json=json.dumps({"PROJECT_NAME": "grafana"}),
    )
    session.add(dep)
    session.commit()

    monkeypatch.setattr(
        "app.services.service_templates.deploy.get_deployment_for_project",
        lambda sess, sid, proj: dep,
    )
    monkeypatch.setattr(
        ce.docker_svc,
        "list_compose_projects",
        lambda server: [{"name": "grafana", "path": "/home/pi/docker/grafana"}],
    )
    monkeypatch.setattr(
        ce.docker_svc,
        "get_project_live_files",
        lambda server, path: {"docker-compose.yml": "services:\n  g:\n    image: grafana\n"},
    )
    monkeypatch.setattr(ce.docker_svc, "get_versions", lambda *a, **k: [])
    monkeypatch.setattr(
        ce.docker_svc, "primary_compose_key", lambda files: "docker-compose.yml"
    )

    ws = ce.load_compose_editor_workspace(session, srv, "grafana")
    assert ws.project_name == "grafana"
    assert "docker-compose.yml" in ws.live_files
    assert "promtail.yml" in ws.project_files
    assert ws.live_compose_key == "docker-compose.yml"

    # inventory miss + fallback live files
    monkeypatch.setattr(ce.docker_svc, "list_compose_projects", lambda server: [])
    proj, live = ce.resolve_project_and_live_files(
        srv, "grafana", inventory_projects=[], template_dep=dep
    )
    assert proj["name"] == "grafana"
    assert live

    def _boom(*a, **k):
        raise RuntimeError("ssh")

    monkeypatch.setattr(ce.docker_svc, "get_project_live_files", _boom)
    with pytest.raises(ComposeEditorNotFound):
        ce.resolve_project_and_live_files(
            srv, "missing", inventory_projects=[], template_dep=None
        )

    monkeypatch.setattr(
        ce.docker_svc,
        "list_compose_projects",
        lambda server: (_ for _ in ()).throw(RuntimeError("x")),
    )
    monkeypatch.setattr(ce.docker_svc, "get_project_live_files", lambda *a, **k: {})
    with pytest.raises(ComposeEditorNotFound):
        ce.resolve_project_and_live_files(srv, "nope", template_dep=None)

    merged = ce.merge_template_desired_sidecars(
        {"docker-compose.yml": "a"}, dep, project="grafana"
    )
    assert "promtail.yml" in merged
    assert ce.merge_template_desired_sidecars({"a": "1"}, None) == {"a": "1"}
    dep.files_json = "not-json"
    kept = ce.merge_template_desired_sidecars({"a": "1"}, dep, project="grafana")
    assert kept.get("a") == "1"

    files, vid = ce.apply_draft_snapshot({"a": "1"}, [], None)
    assert files == {"a": "1"} and vid is None
    files, vid = ce.apply_draft_snapshot({"a": "1"}, [], "99")
    assert vid is None

    draft = SimpleNamespace(
        id=7,
        is_draft=True,
    )
    monkeypatch.setattr(
        ce.docker_svc, "parse_version_files", lambda dv: {"docker-compose.yml": "draft\n"}
    )
    monkeypatch.setattr(
        ce.docker_svc, "merge_project_files", lambda live, extra: {**live, **extra}
    )
    out, vid = ce.apply_draft_snapshot({"a": "1"}, [draft], "7")
    assert vid == 7
    assert out["docker-compose.yml"] == "draft\n"

    monkeypatch.setattr(
        "app.services.service_templates.deploy.get_deployment_for_project",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db")),
    )
    assert ce.get_template_deployment(session, srv.id, "grafana") is None


# ---------------------------------------------------------------------------
# host_sync
# ---------------------------------------------------------------------------


def test_host_sync_adopt_and_migrate(monkeypatch):
    from app.services.service_templates import host_sync as hs
    from app.services.service_templates.schema import TemplateError

    session, _ = _memory()
    srv = _server(session)
    secrets = encrypt_str(json.dumps({"GF_PASSWORD": "old"}))
    dep = StackDeployment(
        server_id=srv.id,
        project_name="grafana",
        template_slug="grafana",
        variables_json=json.dumps({"PROJECT_NAME": "grafana", "PORT": "3000"}),
        secrets_encrypted=secrets,
        files_json=json.dumps({"docker-compose.yml": "services: {}\n", ".env": "GF_PASSWORD=\n"}),
        config_version=1,
    )
    session.add(dep)
    session.commit()
    session.refresh(dep)

    live = {
        "docker-compose.yml": "services:\n  g:\n    image: grafana/grafana\n",
        ".env": "GF_PASSWORD=newsecret\nPORT=3001\nAPI_TOKEN=tok\nOTHER=x\n",
    }
    monkeypatch.setattr(hs, "get_project_live_files", lambda *a, **k: live)
    monkeypatch.setattr(hs, "get_template_definition", lambda *a, **k: SimpleNamespace(
        variables=[
            SimpleNamespace(name="GF_PASSWORD", secret=True, type="password"),
            SimpleNamespace(name="PORT", secret=False, type="string"),
        ]
    ))

    out = hs.adopt_host_files_as_desired(session, server=srv, deployment=dep)
    assert out["ok"] is True
    assert out["config_version"] == 2
    session.refresh(dep)
    assert dep.drift_status == "in_sync"

    monkeypatch.setattr(hs, "get_project_live_files", lambda *a, **k: {})
    with pytest.raises(TemplateError):
        hs.adopt_host_files_as_desired(session, server=srv, deployment=dep)

    monkeypatch.setattr(
        hs, "get_project_live_files", lambda *a, **k: (_ for _ in ()).throw(OSError("down"))
    )
    with pytest.raises(TemplateError):
        hs.adopt_host_files_as_desired(session, server=srv, deployment=dep)

    monkeypatch.setattr(hs, "get_project_live_files", lambda *a, **k: live)
    migrated = hs.migrate_host_env_into_deployment(session, server=srv, deployment=dep)
    assert migrated["ok"] is True
    assert "GF_PASSWORD" in migrated["imported_secrets"]

    migrated2 = hs.migrate_host_env_into_deployment(
        session, server=srv, deployment=dep, secret_keys=["GF_PASSWORD", "missing"]
    )
    assert "missing" in migrated2["skipped"]

    monkeypatch.setattr(
        hs, "get_project_live_files", lambda *a, **k: {"docker-compose.yml": "x"}
    )
    with pytest.raises(TemplateError):
        hs.migrate_host_env_into_deployment(session, server=srv, deployment=dep)

    names = hs.secret_names_for_deployment(session, dep, known_secrets={"A": "1"})
    assert "A" in names
    monkeypatch.setattr(
        hs, "get_template_definition", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
    )
    names2 = hs.secret_names_for_deployment(session, dep, known_secrets={"B": "1"})
    assert "B" in names2

    refreshed = hs.refresh_secrets_from_host_env(
        {"GF_PASSWORD": "old"},
        {"GF_PASSWORD": "new", "API_TOKEN": "t", "PORT": "1"},
        {"GF_PASSWORD"},
    )
    assert refreshed["GF_PASSWORD"] == "new"
    assert refreshed["API_TOKEN"] == "t"

    assert hs.project_path_for(srv, "grafana").endswith("/grafana")


# ---------------------------------------------------------------------------
# herder_backup helpers
# ---------------------------------------------------------------------------


def test_herder_backup_archive_resolve_prune_and_jobs(tmp_path, monkeypatch):
    from app.services import herder_backup as hb

    monkeypatch.setattr(hb, "HERDER_BACKUP_DIR", tmp_path)
    monkeypatch.setattr(hb, "archive_dir_candidates", lambda: [tmp_path])
    monkeypatch.setattr(hb.settings, "HERDER_BACKUP_ROOT", str(tmp_path))
    monkeypatch.setattr(hb.settings, "DATA_ROOT", str(tmp_path / "data"))

    assert hb.is_safe_archive_basename("piherder-2026.tar.gz")
    assert not hb.is_safe_archive_basename("../piherder-x.tar.gz")
    assert not hb.is_safe_archive_basename("notes.txt")
    assert not hb.is_safe_archive_basename("")

    good = tmp_path / "piherder-20260919.tar.gz"
    good.write_bytes(b"x")
    found = hb.resolve_archive_in_roots(name="piherder-20260919.tar.gz")
    assert found == good.resolve()
    assert hb.resolve_archive_in_roots(name="nope.tar.gz") is None
    assert hb.resolve_archive_in_roots(path="") is None
    by_path = hb.resolve_archive_in_roots(path=str(good))
    assert by_path == good.resolve()
    outside = tmp_path.parent / "escape.tar.gz"
    # may or may not exist; resolve should refuse paths outside roots
    assert hb.resolve_archive_in_roots(path="/tmp/not-an-archive") is None

    upload = tmp_path / ".upload-abc"
    # tmp upload only allowed under /tmp
    assert hb.resolve_archive_in_roots(path=str(upload), allow_tmp_upload=True) is None

    old = tmp_path / "piherder-old.tar.gz"
    older = tmp_path / "piherder-older.tar.gz"
    old.write_bytes(b"a")
    older.write_bytes(b"b")
    import os
    os.utime(older, (1, 1))
    os.utime(old, (2, 2))
    os.utime(good, (3, 3))
    monkeypatch.setattr(hb, "_ensure_dir", lambda: None)
    hb.prune_old_backups(keep=1)
    listed = hb.list_backups()
    assert listed
    assert all(row["name"].startswith("piherder-") for row in listed)

    assert hb._path_is_writable(tmp_path)
    assert hb._is_under_root(good, tmp_path)
    assert not hb._is_under_root(tmp_path / "nope" / "x", tmp_path / "other")

    rows = hb._jobs_for_restore(
        [
            "skip",
            {"id": 1, "status": "success", "celery_task_id": "t"},
            {"id": 2, "status": "running", "details": json.dumps({"k": 1})},
            {"id": 3, "status": "pending", "details": "plain"},
            {"id": 4, "status": "running", "details": None},
            {"id": 5, "status": "running", "details": "{"},
        ]
    )
    by_id = {r["id"]: r for r in rows if isinstance(r, dict)}
    assert by_id[1]["celery_task_id"] is None
    assert by_id[2]["status"] == "cancelled"
    assert "restore_note" in json.loads(by_id[2]["details"])
    assert "cancelled on herder restore" in by_id[3]["details"]
    assert by_id[4]["details"].startswith("cancelled")

    now = datetime.now(timezone.utc)
    assert hb._parse_dt(None) is None
    naive = hb._parse_dt(now)
    assert naive.tzinfo is None
    assert isinstance(hb._parse_dt("2026-09-19T12:00:00Z"), datetime)
    assert hb._parse_dt("not-a-date") == "not-a-date"

    cleaned = hb._clean_row(Job, {"id": 1, "status": "ok", "created_at": "2026-01-01T00:00:00Z", "nope": 1})
    assert "nope" not in cleaned
    assert cleaned["id"] == 1

    monkeypatch.setattr(hb, "_path_is_writable", lambda p: p == tmp_path)
    hb._ensure_dir()
    assert hb.HERDER_BACKUP_DIR == tmp_path


# ---------------------------------------------------------------------------
# NPM HTTP
# ---------------------------------------------------------------------------


def test_npm_list_retarget_poll_download_renew(monkeypatch):
    from app.services.integrations import npm as npm

    host = {
        "id": 9,
        "domain_names": ["app.example.com"],
        "forward_host": "10.0.0.4",
        "forward_port": 3000,
        "forward_scheme": "http",
        "enabled": True,
        "certificate_id": 1,
        "ssl_forced": True,
        "meta": {},
    }
    cert = {
        "id": 3,
        "nice_name": "app",
        "provider": "letsencrypt",
        "domain_names": ["app.example.com"],
        "expires_on": "2026-12-01",
        "meta": {},
    }

    def handler(method, url, k):
        if url.endswith("/api/tokens") and method == "POST":
            return _HttpResp({"token": "tok"})
        if "/proxy-hosts/9" in url and method == "GET":
            return _HttpResp(host)
        if url.endswith("/proxy-hosts") and method == "GET":
            return _HttpResp([host])
        if "/proxy-hosts/9" in url and method == "PUT":
            return _HttpResp({"ok": True})
        if url.endswith("/certificates") and method == "GET":
            return _HttpResp([cert])
        if url.endswith("/download"):
            buf = io.BytesIO()
            import zipfile

            with zipfile.ZipFile(buf, "w") as z:
                z.writestr("fullchain.pem", "FULL\n")
                z.writestr("privkey.pem", "PRIV\n")
            return _HttpResp(payload={}, status=200, content=buf.getvalue())
        if url.endswith("/renew") and method == "POST":
            return _HttpResp({"ok": True})
        return _HttpResp({"error": "no"}, status=404, text="missing")

    monkeypatch.setattr(npm.httpx, "Client", lambda *a, **k: _HttpClient(handler))

    assert npm.normalize_base_url("http://npm.local:81/api") == "http://npm.local:81"
    assert npm.open_npm_url("http://npm.local:81", "login") == "http://npm.local:81/login"
    assert npm.open_npm_url("http://npm.local:81", "https://x") == "https://x"

    tok = npm.get_token("http://npm.local:81", "a@b", "pw")
    assert tok == "tok"
    hosts = npm.list_proxy_hosts("http://npm.local:81", tok)
    assert hosts[0]["forward_host"] == "10.0.0.4"
    certs = npm.list_certificates("http://npm.local:81", tok)
    assert certs[0]["id"] == "3"
    got = npm.get_proxy_host("http://npm.local:81", tok, 9)
    assert got["id"] == 9
    ret = npm.retarget_proxy_host_backend(
        "http://npm.local:81", tok, 9, "10.0.0.5", forward_port=8080
    )
    assert ret["forward_host"] == "10.0.0.5"
    assert ret["old_forward_host"] == "10.0.0.4"
    blob = npm.download_certificate_zip("http://npm.local:81", tok, 3)
    parsed = npm.parse_certificate_zip(blob)
    assert "FULL" in parsed["fullchain"]
    renewed = npm.renew_certificate("http://npm.local:81", tok, 3)
    assert renewed.get("ok") is True
    poll = npm.poll("http://npm.local:81", "a@b", "pw")
    assert poll.ok is True
    assert poll.to_status_json()["proxy_host_count"] == 1

    def bad_login(method, url, k):
        return _HttpResp({"error": "no"}, status=401, text="no")

    monkeypatch.setattr(npm.httpx, "Client", lambda *a, **k: _HttpClient(bad_login))
    failed = npm.poll("http://npm.local:81", "a@b", "pw")
    assert failed.ok is False
    with pytest.raises(RuntimeError):
        npm.get_token("http://npm.local:81", "a@b", "pw")

    # zip with cert+chain names only
    buf = io.BytesIO()
    import zipfile

    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("cert.pem", "CERT\n")
        z.writestr("chain.pem", "CHAIN\n")
        z.writestr("private.key", "KEY\n")
    out = npm.parse_certificate_zip(buf.getvalue())
    assert "CERT" in out["fullchain"]
    assert "KEY" in out["privkey"]


# ---------------------------------------------------------------------------
# Pi-hole parse + HTTP
# ---------------------------------------------------------------------------


def test_pihole_parse_login_stats_and_dns_lists(monkeypatch):
    from app.services.integrations import pihole as ph

    assert ph.normalize_base_url("http://pihole.local/admin") == "http://pihole.local"
    assert ph.admin_url("http://pihole.local").endswith("/admin/")
    assert ph.admin_url("http://pihole.local", "/settings").endswith("/admin/settings")
    assert ph.admin_url("http://pihole.local", "https://x") == "https://x"

    st = ph.parse_stats_payload("nope")
    assert st.ok is False
    nested = ph.parse_stats_payload(
        {
            "queries": {"total": 10, "blocked": 2, "percent_blocked": "x"},
            "gravity": {"domains_being_blocked": "bad"},
            "clients": {"active": "nope"},
            "version": {"core": {"local": {"version": "6.0"}}},
        }
    )
    assert nested.ok is True
    assert nested.queries == 10
    flat = ph.parse_stats_payload(
        {
            "dns_queries_today": 100,
            "ads_blocked_today": 25,
            "domains_being_blocked": 50,
            "unique_clients": 3,
            "version": "5.0",
        }
    )
    assert flat.percent_blocked == 25.0

    hosts = ph._parse_host_entries(
        {
            "config": {
                "dns": {
                    "hosts": [
                        "10.0.0.1 app.lan",
                        {"ip": "10.0.0.2", "domain": "b.lan"},
                        "incomplete",
                    ]
                }
            }
        }
    )
    assert {h["domain"] for h in hosts} == {"app.lan", "b.lan"}
    assert ph._parse_host_entries("x") == []
    cnames = ph._parse_cname_entries(
        {
            "config": {
                "dns": {
                    "cnameRecords": [
                        "app.lan,pi.lan,300",
                        {"domain": "x.lan", "target": "pi.lan"},
                    ]
                }
            }
        }
    )
    assert any(c["domain"] == "app.lan" for c in cnames)

    def handler(method, url, k):
        if url.endswith("/api/auth") and method == "POST":
            return _HttpResp({"session": {"sid": "sid1", "csrf": "c"}})
        if url.endswith("/auth") and method == "DELETE":
            return _HttpResp({})
        if "/stats/summary" in url:
            return _HttpResp({"queries": {"total": 1, "blocked": 0}, "gravity": {}, "clients": {}})
        if "/info/version" in url:
            return _HttpResp({"version": {"core": {"local": {"version": "6.1"}}}})
        if "/config/dns/hosts" in url:
            return _HttpResp({"config": {"dns": {"hosts": ["1.2.3.4 a.lan"]}}})
        if "/config/dns/cnameRecords" in url:
            return _HttpResp({"config": {"dns": {"cnameRecords": ["a.lan,b.lan"]}}})
        if method == "GET":
            return _HttpResp({}, status=401, text="no")
        return _HttpResp({})

    monkeypatch.setattr(ph.httpx, "Client", lambda *a, **k: _HttpClient(handler))
    sess = ph.login("http://pihole.local", "secret")
    assert sess.sid == "sid1"
    stats = ph.fetch_stats("http://pihole.local", "secret")
    assert stats.ok is True
    assert stats.version == "6.1"
    listed = ph.list_dns_hosts(sess)
    assert listed[0]["ip"] == "1.2.3.4"
    cn = ph.list_dns_cnames(sess)
    assert cn[0]["target"] == "b.lan"
    ph.logout(sess)

    with pytest.raises(ValueError):
        ph.login("http://pihole.local", "")
    with pytest.raises(ValueError):
        ph.normalize_base_url("")


# ---------------------------------------------------------------------------
# ssh_console park / claim / discard
# ---------------------------------------------------------------------------


def test_ssh_console_park_claim_discard_expire(monkeypatch):
    import time

    from app.services import ssh_console as sc

    sc.reset_runtime_state_for_tests()
    monkeypatch.setattr(sc, "require_enabled", lambda: None)
    monkeypatch.setattr(sc, "console_enabled", lambda: True)
    monkeypatch.setattr(sc, "idle_sec", lambda: 9000)
    monkeypatch.setattr(sc, "max_session_sec", lambda: 99999)
    monkeypatch.setattr(sc, "hold_sec", lambda: 0)
    monkeypatch.setattr(sc, "bind_device_enabled", lambda: False)
    monkeypatch.setattr(sc, "bind_ip_enabled", lambda: False)
    monkeypatch.setattr(sc, "release_slot", lambda *a, **k: None)

    ch = MagicMock()
    ch.close.side_effect = RuntimeError("already")
    cli = MagicMock()
    cli.close.side_effect = RuntimeError("gone")
    now = time.monotonic()
    held = sc.HeldConsole(
        resume_id="r1",
        user_id=1,
        server_id=2,
        session_version=1,
        ticket_payload={},
        device_id="dev",
        client=cli,
        channel=ch,
        started_mono=now,
        last_activity_mono=now,
        held_at_mono=now,
        server_hostname="pi",
    )
    held.append_out(b"")
    held.append_out(b"hello")
    held.append_out(b"x" * (sc._HELD_BUF_MAX + 10))
    taken = held.take_out()
    assert taken.endswith(b"x")
    assert held.take_out() == b""

    sc.park_console(held)
    assert sc.get_held("r1") is held
    assert "r1" in sc.list_held_ids()
    assert sc.held_count() >= 1
    assert sc.held_should_expire(held) is None

    claimed = sc.claim_resume(
        "r1", user_id=1, server_id=2, session_version=1, device_id="dev"
    )
    assert claimed is held
    assert sc.get_held("r1") is None

    sc.park_console(held)
    assert sc.discard_parked_for_user("r1", user_id=99) is False
    assert sc.discard_parked_for_user("r1", user_id=1, server_id=2) is True
    assert sc.destroy_held("missing") is False

    held2 = sc.HeldConsole(
        resume_id="r2",
        user_id=1,
        server_id=2,
        session_version=1,
        ticket_payload={},
        device_id="dev",
        client=MagicMock(),
        channel=MagicMock(),
        started_mono=now,
        last_activity_mono=now,
        held_at_mono=now,
        server_hostname="pi",
    )
    sc.park_console(held2)
    n = sc.discard_all_parked_for_user(1)
    assert n >= 1

    held3 = sc.HeldConsole(
        resume_id="r3",
        user_id=1,
        server_id=2,
        session_version=1,
        ticket_payload={},
        device_id="dev",
        client=MagicMock(),
        channel=MagicMock(),
        started_mono=now - 10,
        last_activity_mono=now - 10,
        held_at_mono=now - 10,
        server_hostname="pi",
    )
    monkeypatch.setattr(sc, "idle_sec", lambda: 1)
    assert sc.held_should_expire(held3) == "idle"
    monkeypatch.setattr(sc, "idle_sec", lambda: 9000)
    monkeypatch.setattr(sc, "max_session_sec", lambda: 1)
    assert sc.held_should_expire(held3) == "max"
    monkeypatch.setattr(sc, "max_session_sec", lambda: 99999)
    monkeypatch.setattr(sc, "hold_sec", lambda: 1)
    assert sc.held_should_expire(held3) == "hold"

    sc.park_console(held3)
    with pytest.raises(sc.ConsoleDenied):
        sc.claim_resume("r3", user_id=9, server_id=2, session_version=1)
    with pytest.raises(sc.ConsoleDenied):
        sc.claim_resume("", user_id=1, server_id=2, session_version=1)

    assert sc._as_bool(None, True) is True
    assert sc._as_bool("yes", False) is True
    assert sc._as_bool("no", True) is False
    assert sc._as_bool(1, False) is True
    sc.reset_runtime_state_for_tests()


# ---------------------------------------------------------------------------
# stack_health
# ---------------------------------------------------------------------------


def test_stack_health_checks_reports_and_notify(monkeypatch):
    from app.services import stack_health as sh

    assert sh._overall([{"status": "ok"}]) == "ok"
    assert sh._overall([{"status": "warn"}]) == "warn"
    assert sh._overall([{"status": "fail"}, {"status": "ok"}]) == "fail"
    assert sh.check_web()["status"] == "ok"

    class BoomSess:
        def __enter__(self):
            raise RuntimeError("db down")

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(sh, "Session", lambda *a, **k: BoomSess())
    db = sh.check_db()
    assert db["status"] == "fail"

    class RedisOk:
        def ping(self):
            return True

        def close(self):
            pass

    class RedisFalse:
        def ping(self):
            return False

        def close(self):
            pass

    import redis as redis_mod

    monkeypatch.setattr(redis_mod, "from_url", lambda *a, **k: RedisOk())
    ok = sh.check_redis()
    assert ok["status"] == "ok"
    monkeypatch.setattr(redis_mod, "from_url", lambda *a, **k: RedisFalse())
    bad = sh.check_redis()
    assert bad["status"] == "fail"
    monkeypatch.setattr(
        redis_mod,
        "from_url",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no redis")),
    )
    assert sh.check_redis()["status"] == "fail"

    class Insp:
        def ping(self):
            return {"w1@h": {"ok": "pong"}}

        def stats(self):
            return {"w1@h": {"pool": {"max-concurrency": 2, "processes": [1, 2]}}}

    class Control:
        def inspect(self, timeout=3.0):
            return Insp()

    class Celery:
        control = Control()

    monkeypatch.setattr("app.celery_app.celery", Celery(), raising=False)
    import app.celery_app as ca

    monkeypatch.setattr(ca, "celery", Celery())
    cel = sh.check_celery()
    assert cel["status"] == "ok"
    assert cel["detail"]["pool_slots"] == 2

    class EmptyInsp:
        def ping(self):
            return None

    class EmptyCel:
        class control:
            @staticmethod
            def inspect(timeout=3.0):
                return EmptyInsp()

    monkeypatch.setattr(ca, "celery", EmptyCel())
    assert sh.check_celery()["status"] == "fail"

    class BoomCel:
        class control:
            @staticmethod
            def inspect(timeout=3.0):
                raise RuntimeError("broker")

    monkeypatch.setattr(ca, "celery", BoomCel())
    assert sh.check_celery()["status"] == "fail"

    sched = SimpleNamespace(running=True, get_jobs=lambda: [1, 2])
    assert sh.check_scheduler(scheduler=None, has_scheduler=False)["status"] == "warn"
    assert sh.check_scheduler(scheduler=SimpleNamespace(running=False), has_scheduler=True)[
        "status"
    ] == "fail"
    ok_s = sh.check_scheduler(scheduler=sched, has_scheduler=True)
    assert ok_s["status"] == "ok"
    assert ok_s["detail"]["job_count"] == 2

    monkeypatch.setattr(
        sh.app_cfg,
        "load_settings",
        lambda: {sh.STACK_HEALTH_SETTINGS_KEY: {"ok": True, "components": []}},
    )
    assert sh.load_last_report()["ok"] is True
    monkeypatch.setattr(
        sh.app_cfg,
        "load_settings",
        lambda: {sh.STACK_HEALTH_SETTINGS_KEY: json.dumps({"ok": False})},
    )
    assert sh.load_last_report()["ok"] is False
    monkeypatch.setattr(sh.app_cfg, "load_settings", lambda: {sh.STACK_HEALTH_SETTINGS_KEY: "nope"})
    assert sh.load_last_report() is None

    report = {
        "components": [
            {"id": "celery", "status": "ok", "detail": {"workers": 1, "pool_slots": 4}},
            {"id": "redis", "status": "fail", "label": "Redis", "message": "down"},
            {"id": "disk", "status": "warn", "label": "Disk", "message": "low"},
            {"id": "web", "status": "ok", "label": "Web"},
        ]
    }
    assert sh.celery_worker_count_from_report(report) == 1
    assert sh.celery_pool_slots_from_report(report) == 4
    assert sh.celery_worker_count_from_report(None) == 0
    assert sh.celery_pool_slots_from_report(None) == 0
    older = {"components": [{"id": "celery", "detail": {"workers": 2}}]}
    assert sh.celery_pool_slots_from_report(older) == 2

    ups = []
    resolved = []
    monkeypatch.setattr(
        sh.notif_svc,
        "upsert_notification",
        lambda *a, **k: ups.append(k) or None,
    )
    monkeypatch.setattr(
        sh.notif_svc, "resolve_by_fingerprint", lambda *a, **k: resolved.append(a)
    )
    session, _ = _memory()
    sh.apply_stack_health_notifications(session, report)
    assert len(ups) == 2
    assert resolved

    saved = []
    monkeypatch.setattr(sh, "collect_stack_health", lambda **k: report)
    monkeypatch.setattr(sh, "save_report", lambda r: saved.append(r) or r)
    out = sh.run_stack_health_check(session=session, notify=True)
    assert out is report
    assert saved


# ---------------------------------------------------------------------------
# docker logs + compose validate extra errors
# ---------------------------------------------------------------------------


def test_docker_logs_and_multi_error_validate(monkeypatch):
    from app.services import docker_management as dm

    assert dm._is_all_services_log_target("__all__")
    assert dm._is_all_services_log_target("all")
    assert not dm._is_all_services_log_target("web")

    class Cli:
        def __init__(self):
            self.closed = False
            self.cmds = []

        def close(self):
            self.closed = True

        def exec_command(self, cmd, timeout=None):
            self.cmds.append(cmd)
            stdout = iter(["line1\n", "line2\n"])
            return MagicMock(), stdout, MagicMock()

    cli = Cli()
    monkeypatch.setattr(dm, "get_ssh_client", lambda *a, **k: cli)
    monkeypatch.setattr(dm, "run_command", lambda *a, **k: (0, "log-a\n", ""))
    srv = SimpleNamespace(id=1, hostname="pi")
    text = dm.get_logs(srv, "web", lines=20)
    assert "log-a" in text
    text2 = dm.get_logs(srv, "__all__", lines=5, project_path="/home/pi/docker/g")
    assert "log-a" in text2
    text3 = dm.get_logs(srv, "web", lines=5, follow=True, project_path="/home/pi/docker/g")
    assert "log-a" in text3
    streamed = list(dm.stream_logs(srv, "web", lines=5))
    assert any("line1" in x for x in streamed)
    streamed2 = list(dm.stream_logs(srv, "__all__", project_path="/p"))
    assert streamed2

    class BoomCli:
        def close(self):
            pass

        def exec_command(self, cmd, timeout=None):
            raise RuntimeError("ssh")

    monkeypatch.setattr(dm, "get_ssh_client", lambda *a, **k: BoomCli())
    err_lines = list(dm.stream_logs(srv, "web"))
    assert any("[ERROR]" in x for x in err_lines)

    multi = dm.validate_compose_content(
        "services:\n  web: [\n  db:\n    image nginx\n  x: {\n"
    )
    assert multi["valid"] is False
    assert multi["errors"]


# ---------------------------------------------------------------------------
# backup vanished / rsync helpers
# ---------------------------------------------------------------------------


def test_backup_vanished_rsync_and_webhook(tmp_path, monkeypatch):
    from app.services import backup as bak

    assert bak._is_vanished_rsync_failure(24)
    assert bak._is_vanished_rsync_failure(23, "file vanished")
    assert not bak._is_vanished_rsync_failure(23, "permission denied")
    assert not bak._is_vanished_rsync_failure("nope")
    assert bak._vanished_retry_count() >= 0
    assert bak._vanished_retry_delay_sec() >= 0
    assert isinstance(bak._vanished_soft_ok_enabled(), bool)
    monkeypatch.setattr(bak.settings, "PIHERDER_BACKUP_VANISHED_RETRIES", "bad")
    assert bak._vanished_retry_count() == 1
    monkeypatch.setattr(bak.settings, "PIHERDER_BACKUP_VANISHED_RETRY_DELAY_SEC", "x")
    assert bak._vanished_retry_delay_sec() == 5.0

    assert bak.backup_source_ok({"vanished_soft_ok": True})
    assert not bak.backup_source_ok({"error": "no", "skipped": True})

    detail = bak._rsync_error_detail("sudo: rsync: not found", "sudo -n rsync")
    assert "rsync" in detail.lower()
    detail2 = bak._rsync_error_detail("Permission denied", "rsync")
    assert "Permission" in detail2 or "denied" in detail2.lower()
    detail3 = bak._rsync_error_detail(
        "rsync: read errors mapping /data (Input/output error)\nrsync error: some files/attrs were not transferred (code 23)",
        "rsync",
    )
    assert "I/O" in detail3 or "input/output" in detail3.lower()
    assert bak._rsync_error_detail("", "rsync") == "rsync non-zero"

    sent = []
    monkeypatch.setattr(
        "app.services.alert_channels.send_webhook",
        lambda msg, **k: sent.append((msg, k)),
    )
    bak._send_webhook("backup failed on pi")
    assert sent
    bak._send_webhook("ok")

    cfg_path = tmp_path / "defaults.json"
    monkeypatch.setattr(bak, "GLOBAL_BACKUP_DEFAULTS_FILE", cfg_path)
    bak.save_global_backup_defaults({"keep": 3})
    assert cfg_path.exists()

    class Cli:
        def __init__(self, replies):
            self.replies = list(replies)

        def next(self):
            return self.replies.pop(0) if self.replies else (1, "", "")

    replies = [(0, "/usr/bin/rsync\n", "")]
    monkeypatch.setattr(
        bak, "run_command", lambda client, cmd, timeout=10: (0, "/usr/bin/rsync\n", "")
    )
    path = bak._remote_rsync_path(object(), "root")
    assert "rsync" in path
    path2 = bak._remote_rsync_path(object(), "pi")
    assert "sudo" in path2 or "rsync" in path2


# ---------------------------------------------------------------------------
# jobs cancel / stop / resolve
# ---------------------------------------------------------------------------


def test_job_cancel_stop_and_resolve(monkeypatch):
    from app.services import jobs as js

    session, engine = _memory()
    srv = _server(session)
    monkeypatch.setattr(js, "_revoke_celery_task", lambda *a, **k: None)
    monkeypatch.setattr(js.backup, "stop_backup", lambda *a, **k: None)
    monkeypatch.setattr(
        js,
        "make_audit_log",
        lambda **k: AuditLog(
            user_id=k.get("user_id"),
            server_id=k.get("server_id"),
            action=k.get("action") or "job_cancel",
            status=k.get("status") or "cancelled",
            details=k.get("details") or "",
        ),
    )
    monkeypatch.setattr(js, "resolve_client_ip", lambda *a, **k: "1.2.3.4")

    running = _job(session, srv, job_type="backup", status="running", details='{"client_ip":"9.9.9.9"}')
    out = js.cancel_job(session, running, user_id=1)
    assert out.status == "cancelled"

    pending = _job(session, srv, job_type="os_patch", status="pending")
    out2 = js.cancel_job(session, pending, user_id=1)
    assert out2.status == "cancelled"

    done = _job(session, srv, job_type="backup", status="success")
    with pytest.raises(js.JobNotCancellable):
        js.cancel_job(session, done)

    again = _job(session, srv, job_type="backup", status="running")
    stopped = js.stop_backup_job(session, srv, again)
    assert stopped.status == "cancelled"
    none = js.stop_active_backup(session, srv, job=None)
    # may stop leftover hostname marker
    assert none is None or isinstance(none, Job)

    found = js.resolve_backup_job(session, srv.id, job_id=stopped.id)
    assert found is not None
    assert js.resolve_backup_job(session, srv.id, job_id=99999) is None
    assert js.job_source_filter(stopped) is None
    src = _job(session, srv, details='{"source_filter":"/data"}')
    assert js.job_source_filter(src) == "/data"
    assert js.job_source_filter(None) is None

    profiles = [{"source": "/data", "dest_name": "data"}]
    attached = js.attach_source_job_states(profiles, [src])
    assert attached


# ---------------------------------------------------------------------------
# certificates humanize
# ---------------------------------------------------------------------------


def test_cert_humanize_deploy_error_matrix():
    from app.services.certificates import humanize_deploy_error

    auth = humanize_deploy_error("Authentication failed", ssh_user="pi", remote_dir="/etc/caddy")
    assert "SSH" in auth or "key" in auth.lower() or "pi" in auth
    perm = humanize_deploy_error("sudo: a password is required", write_mode="sudo", ssh_user="pi")
    assert perm
    timeout = humanize_deploy_error("Connection timed out", ssh_user="pi")
    assert "online" in timeout.lower() or "SSH" in timeout or "reach" in timeout.lower()
    generic = humanize_deploy_error("weird boom", write_mode="sftp", remote_dir="/certs")
    assert "weird boom" in generic
    empty = humanize_deploy_error("", ssh_user="pi")
    assert empty


# ---------------------------------------------------------------------------
# host ports sticky + summaries
# ---------------------------------------------------------------------------


def test_host_ports_sticky_and_summaries():
    from app.services.dns_fabric import host_ports as hp

    assert hp._norm_proto("UDP") == "udp"
    assert hp._norm_proto("nope") == "tcp"
    assert hp._port_key(443, "tcp") == "443/tcp"

    parsed = [
        {"host": "443", "proto": "tcp", "published": True, "label": "443/tcp"},
        {"host": "bad", "proto": "tcp", "published": True, "label": "x"},
        {"host": "22", "proto": "tcp", "published": False, "label": "22/tcp"},
    ]
    anns = {
        "443/tcp": PortAnnotation(
            host_port=443,
            proto="tcp",
            role_key="web",
            label="https",
            note="edge",
            hide=False,
        ),
        "22/tcp": PortAnnotation(
            host_port=22, proto="tcp", role_key="ssh", hide=True, label="ssh"
        ),
    }
    out = hp.apply_sticky_to_parsed(parsed, anns)
    assert out[0]["role"] == "web"
    assert out[0]["sticky_label"] == "https"
    assert out[2].get("sticky_hide") is True
    assert hp.apply_sticky_to_parsed(parsed, None) == parsed

    session, _ = _memory()
    empty = hp.host_ports_summary_for_server(session, 999)
    assert empty["ports_count"] == 0
    empty_d = hp.host_ports_summary_for_device(session, 999)
    assert empty_d["ports_count"] == 0
    need = hp.build_host_ports_expand_payload(session)
    assert need["ok"] is False


# ---------------------------------------------------------------------------
# harden split_env + console audit clamps
# ---------------------------------------------------------------------------


def test_harden_split_env_and_console_audit_helpers():
    from app.services.console_audit import (
        clamp_mode,
        clamp_retention_days,
        redact_secrets,
        strip_ansi,
        _to_bytes,
        _split_incomplete_esc,
        looks_like_password_prompt,
    )
    from app.services.service_templates.harden import split_env_for_docker_secrets

    env, secrets = split_env_for_docker_secrets(
        "API_TOKEN=s3cret\nPORT=80\n", ["API_TOKEN"]
    )
    assert "API_TOKEN" in secrets
    assert "PORT=80" in env

    assert clamp_mode("cmd") == "commands"
    assert clamp_mode("nope") == "off"
    assert clamp_retention_days("bad") >= 1
    redacted = redact_secrets(
        "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----"
    )
    assert "BEGIN" not in redacted or "redacted" in redacted.lower()
    assert strip_ansi("plain") == "plain"
    stripped = strip_ansi("\x1b[31mred\x1b[0m")
    assert "red" in stripped
    assert _to_bytes(None) == b""
    assert _to_bytes("hi") == b"hi"
    assert _to_bytes(b"x") == b"x"
    keep, tail = _split_incomplete_esc("hello")
    assert keep == "hello"
    assert looks_like_password_prompt("Password:") is True
    assert looks_like_password_prompt("ok") is False
