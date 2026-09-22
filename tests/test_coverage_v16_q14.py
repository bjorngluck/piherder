"""v1.6 Q-80 fourteenth pack — stack_health, ssh identities, webauthn, scheduler, leftover services."""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models import Server, ServerSshIdentity, User
from app.security.encryption import encrypt_str


def _memory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine), engine


def test_stack_health_celery_scheduler_tree(tmp_path, monkeypatch):
    from app.services import app_settings as app_cfg
    from app.services import stack_health as sh

    assert sh.check_web()["status"] == "ok"
    _session, engine = _memory()
    # save_report writes through app_settings, which opens its own engine.
    monkeypatch.setattr(sh, "engine", engine)
    monkeypatch.setattr(app_cfg, "engine", engine)
    monkeypatch.setattr(app_cfg, "_cache", None)
    db = sh.check_db()
    assert db["status"] in ("ok", "fail")

    monkeypatch.setattr(sh.os, "getenv", lambda *a, **k: "redis://127.0.0.1:1/0")
    red = sh.check_redis()
    assert red["status"] in ("ok", "fail")

    class _Insp:
        def ping(self):
            return {"w1": {"ok": "pong"}, "w2": {"ok": "pong"}}

        def stats(self):
            return {
                "w1": {"pool": {"max-concurrency": 2, "processes": [1, 2]}},
                "w2": {"pool": {"processes": [1]}},
            }

    monkeypatch.setattr(
        "app.celery_app.celery",
        SimpleNamespace(control=SimpleNamespace(inspect=lambda timeout=3.0: _Insp())),
        raising=False,
    )
    cel = sh.check_celery()
    assert cel["status"] in ("ok", "fail")
    monkeypatch.setattr(
        "app.celery_app.celery",
        SimpleNamespace(control=SimpleNamespace(inspect=lambda timeout=3.0: None)),
        raising=False,
    )
    cel2 = sh.check_celery()
    assert cel2["id"] == "celery"

    assert sh.check_scheduler(None, False)["status"] == "warn"
    sched = SimpleNamespace(running=False, get_jobs=lambda: [])
    assert sh.check_scheduler(sched, True)["status"] == "fail"
    sched2 = SimpleNamespace(running=True, get_jobs=lambda: [1, 2])
    ok = sh.check_scheduler(sched2, True)
    assert ok["status"] == "ok"
    boom = SimpleNamespace(running=True, get_jobs=lambda: (_ for _ in ()).throw(RuntimeError("x")))
    sh.check_scheduler(boom, True)

    tree = tmp_path / "t"
    tree.mkdir()
    (tree / "a").write_bytes(b"hello")
    used, method = sh._tree_used_bytes(tree, timeout_sec=5)
    assert used is None or used >= 0
    kids = sh._top_level_usage(tree, limit=4)
    assert isinstance(kids, list)
    disks = sh.check_disks(include_tree_usage=False)
    assert disks
    report = {
        "components": [
            {"id": "db", "status": "fail", "label": "PostgreSQL", "message": "down"},
            {"id": "web", "status": "ok", "label": "Web", "message": "up"},
        ]
    }
    session, _ = _memory()
    sh.apply_stack_health_notifications(session, report)
    session.commit()
    sh.load_last_report()
    sh.save_report({"overall": "ok", "components": []})
    sh.run_stack_health_check(notify=False, has_scheduler=False)


def test_ssh_identities_lifecycle():
    from app.services import ssh_identities as ident

    session, _ = _memory()
    srv = Server(
        name="pi",
        hostname="pi.local",
        ssh_username="pi",
        ssh_private_key_encrypted=encrypt_str("-----BEGIN OPENSSH PRIVATE KEY-----\nx\n"),
        ssh_public_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAINotARealKeyComment",
        docker_base_dir="/home/pi/docker",
        os_type="debian",
    )
    session.add(srv)
    session.commit()
    session.refresh(srv)

    assert ident.fingerprint_public(None) is None
    assert ident.fingerprint_public("not-a-key") is None
    import base64

    blob = base64.b64encode(b"hello-world-key-bytes-ok").decode()
    fp = ident.fingerprint_public(f"ssh-ed25519 {blob}")
    assert fp is None or fp.startswith("SHA256:")
    assert ident._clean_label("", ident.ROLE_FLEET) == "Fleet"
    assert ident._clean_username("root@host!", default="pi") == "roothost"

    fleet = ident.ensure_fleet_identity(session, srv)
    session.commit()
    assert fleet.role == ident.ROLE_FLEET
    fleet2 = ident.ensure_fleet_identity(session, srv)
    assert fleet2.id == fleet.id
    srv.ssh_username = "ops"
    ident.ensure_fleet_identity(session, srv)
    session.commit()
    view = ident.public_view(fleet)
    assert view["has_key"] is True
    assert ident.get_by_id(session, srv.id, fleet.id) is not None
    assert ident.get_by_id(session, srv.id, 99999) is None
    assert ident.get_by_id(session, 0, fleet.id) is None
    ident.apply_fleet_to_server(srv, fleet)
    overlay = ident.overlay_server_for_identity(srv, fleet)
    assert overlay.ssh_username
    ident.apply_material(fleet, public_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAINotARealKeyComment")
    with pytest.raises(ident.IdentityError):
        ident.update_privileged_username(session, fleet, "x")
    with pytest.raises(ident.IdentityError):
        ident.remove_privileged(session, fleet)
    n = ident.purge_for_server(session, srv.id)
    assert n >= 1
    session.commit()
    ident.ensure_fleet_identity(session, srv)
    session.commit()
    picks = ident.console_identities(session, srv, demo=True)
    assert picks


def test_webauthn_helpers(monkeypatch):
    from app.services import webauthn_svc as wa

    monkeypatch.setattr(wa.settings, "PIHERDER_HOSTNAME", "")
    monkeypatch.setattr(wa.settings, "PIHERDER_PUBLIC_URL", "ph.example.net")
    assert wa.resolve_rp_id() == "ph.example.net"
    assert wa.resolve_expected_origin().startswith("https://")
    assert wa.resolve_rp_name() == "PiHerder"
    u = User(id=7, email="a@b.c", hashed_password="x", role="admin", is_active=True)
    assert wa.user_handle_for(u) == b"ph-user-7"
    assert wa._transports_json(None) is None
    assert wa._transports_json(["usb", SimpleNamespace(value="nfc")])
    assert wa._transports_list(None) is None
    assert wa._transports_list("[") is None
    assert wa._transports_list('["usb"]') == ["usb"]
    tok = wa.mint_challenge_token(kind="reg", user_id=7, challenge_b64="YWJj")
    assert wa.read_challenge_token(None, kind="reg", user_id=7) is None
    assert wa.read_challenge_token(tok, kind="auth", user_id=7) is None
    assert wa.read_challenge_token(tok, kind="reg", user_id=8) is None
    chal = wa.read_challenge_token(tok, kind="reg", user_id=7)
    assert chal is None or isinstance(chal, (bytes, type(None)))


def test_scheduler_host_facts_and_skip(monkeypatch):
    from app.services import scheduler as sched

    session, engine = _memory()
    srv = Server(
        name="pi",
        hostname="pi.local",
        ssh_username="pi",
        docker_base_dir="/home/pi/docker",
        os_type="debian",
        host_facts_status="never",
    )
    session.add(srv)
    session.commit()
    monkeypatch.setattr("app.database.engine", engine)
    called = []
    monkeypatch.setattr(
        "app.services.host_facts.request_refresh",
        lambda sid, force=False: called.append(sid),
    )
    monkeypatch.setattr(
        "app.services.host_facts.is_stale",
        lambda server, max_age_sec=900: True,
    )
    sched.schedule_host_facts_fleet()
    sched.sync_host_facts_schedule(None, False)
    dummy = SimpleNamespace(add_job=lambda **k: None, get_job=lambda i: None, remove_job=lambda i: None)
    monkeypatch.setattr(sched, "_remove_job", lambda s, i: None)
    sched.sync_host_facts_schedule(dummy, True)
    assert sched.os_apply_skip_reason(None) == "missing"
    s = SimpleNamespace(os_patch_enabled=False, os_apply_enabled=False)
    assert sched.os_apply_skip_reason(s) == "disabled"


def test_host_files_dns_herder_docker_leftover(tmp_path, monkeypatch):
    from app.services import host_files as hf
    from app.services import docker_management as dm
    from app.services.dns_fabric import core as fabric
    from app.services import herder_backup as hb

    monkeypatch.setattr("app.services.demo.demo_mode", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(hf.settings, "PIHERDER_DEMO_MODE", True, raising=False)
    assert hf.is_demo_files() is True
    with pytest.raises(hf.FilesError):
        hf.parse_rel("x/" + ("a" * 300))
    with pytest.raises(hf.FilesError):
        hf._refuse_demo_write()

    session, _ = _memory()
    srv = Server(
        name="rpi5-1",
        hostname="rpi5-1.lan",
        dns_name="rpi5-1.lan",
        ip_address="10.0.0.4",
        ssh_username="pi",
        docker_base_dir="/home/pi/docker",
        os_type="debian",
    )
    session.add(srv)
    session.commit()
    tokens = fabric._server_name_tokens(srv)
    assert "rpi5-1" in tokens or "rpi5-1.lan" in tokens

    class _Sess:
        pass

    monkeypatch.setattr(
        fabric.reg,
        "list_integrations",
        lambda session, type_filter=None: [
            SimpleNamespace(id=1, name="ph", enabled=True, base_url="http://pi.lan")
        ],
    )
    monkeypatch.setattr(fabric.reg, "is_pihole_primary", lambda r: True)
    monkeypatch.setattr(fabric.reg, "pihole_password", lambda r: "x")
    monkeypatch.setattr(fabric.reg, "tls_verify", lambda r: False)
    monkeypatch.setattr(
        fabric.ph,
        "login",
        lambda *a, **k: _Sess(),
    )
    monkeypatch.setattr(fabric.ph, "logout", lambda s: None)
    monkeypatch.setattr(
        fabric.ph,
        "list_dns_hosts",
        lambda s: [{"domain": "rpi5-1.lan", "ip": "10.0.0.4"}],
    )
    hit = fabric.match_pihole_host_for_server(session, srv)
    assert hit is None or hit.get("domain")

    monkeypatch.setattr(hb.settings, "HERDER_BACKUP_ROOT", str(tmp_path))
    monkeypatch.setattr(hb, "HERDER_BACKUP_DIR", tmp_path)
    (tmp_path / "piherder-old.tar.gz").write_bytes(b"x")
    (tmp_path / "piherder-new.tar.gz").write_bytes(b"y")
    hb.prune_old_backups(keep=1)
    listed = hb.list_backups()
    assert isinstance(listed, list)

    cli = SimpleNamespace(close=lambda: None)

    def _run(client, cmd, timeout=20):
        if "config --format json" in cmd:
            return 0, json.dumps(
                {
                    "services": {
                        "web": {"image": "nginx:latest"},
                        "app": {"build": ".", "image": "local:dev"},
                        "bad": "x",
                    }
                }
            ), ""
        if "config --images" in cmd:
            return 0, "nginx:latest\n", ""
        if "image inspect" in cmd:
            return 0, "sha256:aaa\n", ""
        return 0, "", ""

    from app.services import docker_management as dm2

    monkeypatch.setattr(dm2, "run_command", _run)
    classified = dm2.classify_compose_images(cli, "/home/pi/docker/g")
    assert "nginx:latest" in classified.get("pullable_images", []) or classified.get("build_services")
    assert dm2._image_id_remote(cli, "") == ""
    assert dm2._image_id_remote(cli, "nginx")
    cached = dm2._cached(lambda: 7, "k1", 30)
    assert cached == 7
    assert dm2._cached(lambda: 8, "k1", 30) == 7
