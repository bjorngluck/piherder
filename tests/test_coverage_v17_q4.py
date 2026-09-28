"""v1.7 coverage — service branches the 75% suite still skips.

SSH, Celery, and HTTP are stubbed. Database access uses a temp SQLite engine.
"""
from __future__ import annotations

import json
import stat
import subprocess
import tarfile
from types import SimpleNamespace

import pytest
from sqlmodel import Session

from tests.test_coverage_v17_q1 import _client, _engine

_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _server():
    return SimpleNamespace(
        id=7,
        name="pi",
        hostname="pi.local",
        ssh_username="pi",
        ssh_port=22,
        ssh_private_key_encrypted=None,
        ssh_password_encrypted=None,
        ssh_hostkey_b64=None,
        ssh_hostkey_type=None,
        ssh_hostkey_fp=None,
        container_patch_enabled=False,
        docker_base_dir=None,
        ip_address=None,
    )


class _Fs:
    def __init__(self):
        self.mkdirs = []
        self.fail_mkdir = False
        self.after_fail = "missing"

    def normalize(self, path):
        if str(path).endswith("/link"):
            return "/home/pi/notes.txt"
        return path

    def lstat(self, path):
        path = str(path)
        if path.endswith("/link"):
            return SimpleNamespace(st_mode=stat.S_IFLNK | 0o777)
        if path.endswith("/box") and self.after_fail == "file":
            return SimpleNamespace(st_mode=stat.S_IFREG | 0o644)
        if path.endswith("/box") and self.after_fail == "missing":
            raise FileNotFoundError(path)
        if path.endswith("/notes.txt"):
            return SimpleNamespace(st_mode=stat.S_IFREG | 0o644, st_size=4)
        raise FileNotFoundError(path)

    def mkdir(self, path):
        if self.fail_mkdir:
            raise OSError("mkdir denied")
        self.mkdirs.append(path)

    def listdir(self, path):
        return []

    def close(self):
        return None


class _Cli:
    def __init__(self, alive=True):
        self.alive = alive
        self.closed = False

    def get_transport(self):
        return SimpleNamespace(is_active=lambda: self.alive)

    def close(self):
        self.closed = True
        self.alive = False


def test_sftp_session_and_ensure_dir(monkeypatch):
    from app.services import host_files as hf

    monkeypatch.setattr(hf, "is_demo_files", lambda: False)
    server = _server()
    hf.drop_sftp_pool()
    opened, handle = _Cli(), _Fs()

    def _open(*_a, **_k):
        return opened, handle

    monkeypatch.setattr(hf, "_open_client", _open)
    with hf.sftp_session(server, pooled=False, with_client=True) as pair:
        assert pair[1] is handle

    def _boom(*_a, **_k):
        raise OSError("down")

    monkeypatch.setattr(hf, "_open_client", _boom)
    with pytest.raises(hf.FilesError):
        with hf.sftp_session(server, pooled=False):
            pass

    def _files(*_a, **_k):
        raise hf.FilesError("denied", "no")

    monkeypatch.setattr(hf, "_open_client", _files)
    with pytest.raises(hf.FilesError):
        with hf.sftp_session(server, pooled=False):
            pass

    monkeypatch.setattr(hf, "_open_client", _open)
    with hf.sftp_session(server, pooled=True) as fs:
        assert fs is handle
    opened.alive = False
    fresh_cli, fresh_fs = _Cli(), _Fs()

    def _reopen(*_a, **_k):
        return fresh_cli, fresh_fs

    monkeypatch.setattr(hf, "_open_client", _reopen)
    with hf.sftp_session(server, pooled=True) as fs:
        assert fs is fresh_fs

    fresh_cli.alive = False

    def _reopen_fail(*_a, **_k):
        raise hf.FilesError("ssh", "again")

    monkeypatch.setattr(hf, "_open_client", _reopen_fail)
    with pytest.raises(hf.FilesError):
        with hf.sftp_session(server, pooled=True):
            pass

    hf.drop_sftp_pool()
    monkeypatch.setattr(hf, "_open_client", lambda *_a, **_k: (_Cli(), _Fs()))
    with pytest.raises(hf.FilesError):
        with hf.sftp_session(server, pooled=True) as _fs:
            raise hf.FilesError("ssh", "dropped")

    fs = _Fs()
    fs.fail_mkdir = True
    fs.after_fail = "file"
    with pytest.raises(hf.FilesError):
        hf.ensure_dir(server, "box", sftp=fs)
    fs.after_fail = "missing"
    with pytest.raises(hf.FilesError) as exc:
        hf.ensure_dir(server, "box", sftp=fs)
    assert exc.value.code == "ssh"

    targets = list(
        hf._walk_perm_targets(
            _Fs(),
            server,
            jail="/home/pi",
            abs_path="/home/pi/link",
            role="fleet",
            identity=None,
            recursive=False,
            depth=0,
            budget=[0],
        )
    )
    assert targets == ["/home/pi/notes.txt"]
    with pytest.raises(hf.FilesError):
        list(
            hf._walk_perm_targets(
                _Fs(),
                server,
                jail="/home/pi",
                abs_path="/home/pi/notes.txt",
                role="fleet",
                identity=None,
                recursive=False,
                depth=hf.WALK_DEPTH_MAX + 1,
                budget=[0],
            )
        )
    hf.drop_sftp_pool()


def test_ssh_pin_key_and_connect_helpers(tmp_path, monkeypatch):
    import app.database as dbmod
    from app.models import Server
    from app.services import ssh as ssh_mod

    engine = _engine(tmp_path / "ssh.db")
    monkeypatch.setattr(dbmod, "engine", engine)
    key = ssh_mod.paramiko.RSAKey.generate(1024)
    with Session(engine) as s:
        row = Server(name="pi", hostname="pi.local", ssh_username="pi", os_type="debian")
        s.add(row)
        s.commit()
        s.refresh(row)
        sid = row.id
    server = SimpleNamespace(id=sid, ssh_hostkey_b64="  ", ssh_hostkey_type=None, ssh_hostkey_fp=None)
    ssh_mod.persist_host_key_if_needed(server, None)
    ssh_mod.persist_host_key_if_needed(SimpleNamespace(id=None), key)
    already = SimpleNamespace(id=sid, ssh_hostkey_b64="abc", ssh_hostkey_type="ssh-ed25519", ssh_hostkey_fp="x")
    ssh_mod.persist_host_key_if_needed(already, key)
    bare = SimpleNamespace(id=sid, ssh_hostkey_b64="", ssh_hostkey_type=None, ssh_hostkey_fp=None)
    ssh_mod.persist_host_key_if_needed(bare, key)
    assert bare.ssh_hostkey_b64 == key.get_base64()

    def _boom_session(*_a, **_k):
        raise RuntimeError("db")

    monkeypatch.setattr(ssh_mod, "Session", _boom_session, raising=False)
    # persist imports Session inside the function
    import sqlmodel

    monkeypatch.setattr(sqlmodel, "Session", _boom_session)
    other = SimpleNamespace(id=sid, ssh_hostkey_b64="", ssh_hostkey_type=None, ssh_hostkey_fp=None)
    ssh_mod.persist_host_key_if_needed(other, key)
    assert not other.ssh_hostkey_b64

    ident = SimpleNamespace(private_key_encrypted="enc-ident")
    host = SimpleNamespace(ssh_private_key_encrypted="enc-host")
    monkeypatch.setattr(ssh_mod.encryption, "decrypt_str", lambda raw: f"plain:{raw}")
    assert ssh_mod.get_private_key_plain(host, ident) == "plain:enc-ident"
    assert ssh_mod.get_private_key_plain(host) == "plain:enc-host"
    with pytest.raises(RuntimeError):
        ssh_mod.get_private_key_plain(SimpleNamespace(ssh_private_key_encrypted=None))

    monkeypatch.setattr(ssh_mod, "get_ssh_client", lambda *a, **k: (_ for _ in ()).throw(OSError("no")))
    assert ssh_mod.test_connection(_server()) is False

    class _Out:
        def read(self):
            return b"ok"

        channel = SimpleNamespace(recv_exit_status=lambda: 0)

    class _Client:
        def exec_command(self, cmd, timeout=120):
            return None, _Out(), _Out()

    status, out, err = ssh_mod.run_command(_Client(), "true")
    assert status == 0 and "ok" in out and "ok" in err


def test_stack_health_du_fallbacks(tmp_path, monkeypatch):
    from app.services import stack_health as sh

    root = tmp_path / "tree"
    root.mkdir()
    (root / "a.txt").write_text("hi")
    (root / "sub").mkdir()
    calls = {"n": 0}

    def _run(cmd, **_k):
        calls["n"] += 1
        if calls["n"] == 1:
            return SimpleNamespace(returncode=1, stdout="")
        return SimpleNamespace(returncode=0, stdout=f"9\t{cmd[-1]}\n")

    monkeypatch.setattr(subprocess, "run", _run)
    total, how = sh._tree_used_bytes(root)
    assert how == "shallow" and total >= 9

    def _missing(*_a, **_k):
        raise FileNotFoundError("du")

    monkeypatch.setattr(subprocess, "run", _missing)
    total, how = sh._tree_used_bytes(root)
    assert how == "walk" and total >= 2

    def _timeout(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd=["du"], timeout=1)

    monkeypatch.setattr(subprocess, "run", _timeout)
    assert sh._tree_used_bytes(root)[1] == "timeout"

    engine = _engine(tmp_path / "health.db")
    monkeypatch.setattr(sh, "engine", engine)
    monkeypatch.setattr(sh, "collect_stack_health", lambda **_k: {"overall": "ok"})
    monkeypatch.setattr(sh, "save_report", lambda report: report)

    def _notify(*_a, **_k):
        raise RuntimeError("notify")

    monkeypatch.setattr(sh, "apply_stack_health_notifications", _notify)
    report = sh.run_stack_health_check(notify=True)
    assert report["overall"] == "ok"


def test_demo_tab_and_logo_discover(tmp_path, monkeypatch):
    from app.services.demo_console import DemoShellChannel
    from app.services import service_logos as logos

    shell = DemoShellChannel(host_label="lab")
    shell.send("cd .do\t")
    shell.send("\r")
    shell.send("ls \t\t")
    shell._apply_completion("zz", "docker/")
    assert shell._line

    monkeypatch.setattr(logos.settings, "DATA_ROOT", str(tmp_path))

    class _Resp:
        def __init__(self, url):
            self.url = url
            self.status_code = 200
            image = url.endswith(".png") or url.endswith(".ico")
            self.content = _PNG if image else b"<html>"
            self.text = '<link rel="icon" href="/favicon.png">'
            self.headers = {"content-type": "image/png" if image else "text/html"}

    class _Client:
        def __init__(self, **_k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def get(self, url):
            if "down" in url:
                raise OSError("down")
            return _Resp(url)

    monkeypatch.setattr(logos.httpx, "Client", _Client)
    hit = logos.discover_logo_from_url("http://svc.example/app")
    assert hit and hit[0][:4] == b"\x89PNG"
    assert logos.discover_logo_from_url("notaurl") is None
    saved = logos.try_discover_and_save(4, "http://svc.example/app")
    assert saved and saved.endswith(".png")
    assert logos._fetch_bytes(_Client(), "http://down/x") is None
    big = _Resp("http://svc.example/favicon.png")
    big.content = b"x" * (logos.MAX_BYTES + 1)

    class _Big(_Client):
        def get(self, url):
            return big

    assert logos._fetch_bytes(_Big(), "http://svc.example/favicon.png") is None


def test_small_service_branches(tmp_path, monkeypatch):
    from app.services.account_stepup import verify_stepup
    from app.services.compose_sets import list_extra_compose_filenames
    from app.services.integrations import uptime_kuma as kuma
    from app.services.nmap.device_classify import _open_port_set
    from app.services.service_templates.harden import rewrite_compose_for_docker_secrets
    from app.services.dns_fabric import stack_expand as se

    names, services = _open_port_set(
        [
            SimpleNamespace(state="open", port="22", service="ssh", product="OpenSSH"),
            SimpleNamespace(state="closed", port=80, service="http", product=""),
            SimpleNamespace(state="open", port="nope", service="", product=""),
        ]
    )
    assert 22 in names and "openssh" in services and 80 not in names

    extra = list_extra_compose_filenames(
        ["compose.yml", "compose.override.yml", "docker-compose.db.yml", "", "notes.txt", "compose.yml"]
    )
    assert "compose.yml" in extra

    text, messages = rewrite_compose_for_docker_secrets(
        "services:\n  web:\n    image: nginx\nsecrets:\n  old:\n    file: ./secrets/OLD\n",
        ["DB_PASSWORD"],
    )
    assert "db_password" in text and any("Extended" in m for m in messages)

    user = SimpleNamespace(id=3, totp_secret_encrypted="enc", hashed_password="h")
    import app.services.webauthn_svc as wa

    monkeypatch.setattr(wa, "user_has_2fa", lambda *_a, **_k: True)
    monkeypatch.setattr(wa, "has_passkeys", lambda *_a, **_k: True)
    monkeypatch.setattr(wa, "totp_active", lambda *_a, **_k: False)
    ok, code = verify_stepup(None, user, totp_code="")
    assert ok is False and code == "use_passkey"

    monkeypatch.setattr(wa, "has_passkeys", lambda *_a, **_k: False)
    monkeypatch.setattr(wa, "totp_active", lambda *_a, **_k: True)
    ok, code = verify_stepup(None, user, totp_code="")
    assert code == "2fa_required"

    import app.services.account_stepup as step

    monkeypatch.setattr(step, "factor_allowed", lambda _s, factor: factor == "passkey")
    ok, code = verify_stepup(None, user, totp_code="")
    assert code == "2fa_required"

    monkeypatch.setattr(step, "factor_allowed", lambda _s, factor: factor in ("totp", "backup"))
    monkeypatch.setattr(wa, "totp_active", lambda *_a, **_k: True)
    import app.security.auth as auth

    monkeypatch.setattr(auth, "decrypt_totp_secret", lambda *_a, **_k: "secret")
    monkeypatch.setattr(auth, "verify_totp_code", lambda *_a, **_k: True)
    ok, code = verify_stepup(None, user, totp_code="123456")
    assert ok is True and code == ""
    monkeypatch.setattr(auth, "decrypt_totp_secret", lambda *_a, **_k: (_ for _ in ()).throw(ValueError("bad")))
    monkeypatch.setattr(auth, "consume_backup_code", lambda *_a, **_k: True)
    ok, code = verify_stepup(None, user, totp_code="backup")
    assert ok is True

    import sys
    import types

    mod = types.ModuleType("uptime_kuma_api")

    class _Api:
        def __init__(self, url, timeout=20, ssl_verify=True):
            self.url = url

        def login(self, user_name, password):
            if password == "bad":
                raise RuntimeError("nope")

        def get_monitors(self):
            return [{"id": 3, "name": "web"}, "skip", {"name": ""}]

        def disconnect(self):
            raise RuntimeError("disc")

    mod.UptimeKumaApi = _Api
    monkeypatch.setitem(sys.modules, "uptime_kuma_api", mod)
    mapped = kuma.fetch_dashboard_id_map("http://kuma", "admin", "secret")
    assert mapped["web"] == "3" and mapped["3"] == "3"
    assert kuma.fetch_dashboard_id_map("http://kuma", "", "") == {}
    assert kuma.fetch_dashboard_id_map("http://kuma", "admin", "bad") == {}

    monkeypatch.setattr(
        se,
        "build_stack_panel",
        lambda *a, **k: {
            "ok": True,
            "containers": [
                {
                    "role": "app",
                    "ports": ["0.0.0.0:80->80/tcp", ""],
                    "ports_parsed": [
                        {"published": True, "host": "80", "proto": "tcp", "role": "http"},
                        {"published": False, "host": "0"},
                        "nope",
                        {"published": True, "host": "no"},
                    ],
                }
            ],
        },
    )
    out = se.build_stack_expand_payload(object(), service_id=1)
    assert out["containers"][0]["ports"] or out.get("ok") is not False
    chips = out["containers"][0].get("port_chips") or out["containers"][0].get("ports")
    assert chips


def test_restore_pg_data_files_and_generic_poll(tmp_path, monkeypatch):
    from app.models import Integration, IntegrationBinding, Server
    from app.services import herder_backup as hb
    from app.services.integrations import generic_url as gen
    from app.services.integrations import poll as poll
    from app.services.integrations import registry as reg
    from app.services.integrations.generic_url import GenericProbeResult

    archive = tmp_path / "full.tar.gz"
    data = tmp_path / "data-out"
    with tarfile.open(archive, "w:gz") as tar:
        dump = tmp_path / "database.dump"
        dump.write_bytes(b"PGDMP")
        tar.add(dump, arcname="database.dump")
        man = tmp_path / "manifest.json"
        man.write_text(json.dumps({"kind": "pg_dump_full", "version": 6}))
        tar.add(man, arcname="manifest.json")
        pic = tmp_path / "pic.bin"
        pic.write_bytes(b"avatar")
        tar.add(pic, arcname="data/pic.bin")
        skip = tmp_path / "skip.bin"
        skip.write_bytes(b"no")
        tar.add(skip, arcname="data/../skip.bin")
    monkeypatch.setattr(hb, "restore_pg_dump", lambda *_a, **_k: None)
    monkeypatch.setattr(hb.settings, "DATA_ROOT", str(data))
    result = hb.restore_herder_backup(archive, dry_run=False)
    assert result["restored_pg_dump"] is True
    assert (data / "pic.bin").read_bytes() == b"avatar"

    engine = _engine(tmp_path / "poll.db")
    with Session(engine) as s:
        integ = Integration(
            type=reg.TYPE_GENERIC_URL,
            name="HA",
            base_url="http://ha.local",
            enabled=True,
        )
        s.add(integ)
        s.commit()
        s.refresh(integ)
        binding = IntegrationBinding(
            integration_id=integ.id,
            server_id=1,
            role=reg.ROLE_SERVICE,
            external_id="status",
            external_meta_json="{}",
        )
        host = Server(name="pi", hostname="pi.local", ssh_username="pi", os_type="debian")
        s.add(host)
        s.commit()
        s.refresh(host)
        binding.server_id = host.id
        s.add(binding)
        s.commit()
        monkeypatch.setattr(
            gen,
            "probe",
            lambda *a, **k: GenericProbeResult(ok=True, status_code=200, product="home_assistant"),
        )
        monkeypatch.setattr(reg, "decrypt_api_key", lambda *_a, **_k: "")
        monkeypatch.setattr(reg, "maybe_discover_logo", lambda *_a, **_k: None)
        out = poll._poll_generic_url(s, integ, notify=False)
        assert out["ok"] is True
        monkeypatch.setattr(reg, "maybe_discover_logo", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("logo")))
        monkeypatch.setattr(
            gen,
            "probe",
            lambda *a, **k: GenericProbeResult(ok=False, error="down", product="custom"),
        )
        out = poll._poll_generic_url(s, integ, notify=False)
        assert out["ok"] is False


def test_kuma_inventory_and_stack_target(tmp_path, monkeypatch):
    from app.models import Integration, IntegrationBinding, Server, ServiceDnsRecord
    from app.services.dns_fabric import kuma_coverage as cov
    from app.services.dns_fabric.stack_panel import resolve_stack_target
    from app.services.integrations import registry as reg

    engine = _engine(tmp_path / "kuma.db")
    inventory = {
        "v": 1,
        "projects": [
            "skip",
            {"name": ""},
            {"name": "other", "containers": [{"placeholder": True}, "nope", {"name": ""}]},
            {"name": "web", "containers": [{"name": "web", "compose_service": "web"}]},
        ],
    }
    with Session(engine) as s:
        host = Server(
            name="pi",
            hostname="pi.local",
            ssh_username="pi",
            os_type="debian",
            container_patch_enabled=True,
            docker_inventory_json=json.dumps(inventory),
        )
        s.add(host)
        s.commit()
        s.refresh(host)
        kuma = Integration(type=reg.TYPE_UPTIME_KUMA, name="Kuma", base_url="http://kuma", enabled=True)
        s.add(kuma)
        s.commit()
        s.refresh(kuma)
        s.add(
            IntegrationBinding(
                integration_id=kuma.id,
                server_id=host.id,
                role=reg.ROLE_SERVICE,
                docker_project="web",
                docker_container="web",
                external_id="web",
            )
        )
        rec = ServiceDnsRecord(
            fqdn="web.lab",
            target_server_id=host.id,
            backend_server_id=host.id,
            label="web",
        )
        s.add(rec)
        s.commit()
        s.refresh(rec)
        audit = cov._attach_dependency_coverage_inner(s, {"services": []})
        assert isinstance(audit, dict)
        resolved = resolve_stack_target(s, service_id=rec.id)
        assert resolved.get("ok") is True
        assert resolved.get("project") in ("web", "other", None) or resolved.get("server_id") == host.id


def test_ssh_routes_with_stubs(tmp_path, monkeypatch):
    from app.services.ssh_onboarding import OnboardingResult
    import app.services.ssh_onboarding as onboard
    import app.services.host_deps as host_deps
    import app.services.ssh as ssh_mod

    engine = _engine(tmp_path / "routes.db")
    client, _uid, sid = _client(engine, monkeypatch)
    monkeypatch.setattr(
        onboard,
        "test_connection_detail",
        lambda *a, **k: OnboardingResult(ok=True, message="ok", details={}),
    )
    monkeypatch.setattr(host_deps, "check_and_persist", lambda *a, **k: {"overall": "ok"})
    monkeypatch.setattr(ssh_mod, "generate_keypair", lambda **k: ("ssh-ed25519 AAAA pi", "priv"))
    try:
        paths = [
            (f"/servers/{sid}/ssh/generate-key", {}),
            (f"/servers/{sid}/ssh/test", {}),
            (f"/servers/{sid}/host-deps/check", {}),
            (f"/servers/{sid}/ssh/reset-host-key", {"confirm_name": "nope"}),
            (f"/servers/{sid}/ssh/reset-host-key", {"confirm_name": "pi"}),
            (f"/servers/{sid}/ssh/set-username", {"ssh_username": "piherder"}),
            (
                f"/servers/{sid}/ssh/deploy-key",
                {"ssh_password": "secret", "store_password": "1", "clear_password_after": "1"},
            ),
        ]
        monkeypatch.setattr(
            onboard,
            "deploy_public_key",
            lambda *a, **k: OnboardingResult(
                ok=True,
                message="installed",
                details={"public_key": "ssh-ed25519 BBBB pi", "installed": True},
            ),
        )
        for path, data in paths:
            response = client.post(path, data=data, follow_redirects=False)
            assert response.status_code < 500
        monkeypatch.setattr(
            onboard,
            "test_connection_detail",
            lambda *a, **k: OnboardingResult(ok=False, message="refused", details={}),
        )
        monkeypatch.setattr(host_deps, "check_and_persist", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("probe")))
        failed = client.post(f"/servers/{sid}/ssh/test", follow_redirects=False)
        deps = client.post(f"/servers/{sid}/host-deps/check", follow_redirects=False)
        assert failed.status_code < 500 and deps.status_code < 500
    finally:
        from app.database import get_session
        from app.main import app

        app.dependency_overrides.pop(get_session, None)


def test_compose_tail_errors():
    from app.services.docker_management import validate_compose_content

    bad = "services:\n  web:\n    image: nginx\n    ports\n      - 80\nfoo: [\n"
    result = validate_compose_content(bad)
    assert result["valid"] is False
    assert result["errors"]
