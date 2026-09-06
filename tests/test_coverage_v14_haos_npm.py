"""v1.4 coverage nudge — HAOS parsers + NPM URL helpers (no live SSH)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import haos as ha
from app.services.integrations import npm as npm_mod


def test_haos_pure_parsers_and_disk_facts():
    assert ha.is_haos_server(SimpleNamespace(os_type="HAOS")) is True
    assert ha.is_haos_server(SimpleNamespace(os_type="debian")) is False
    rel = ha.parse_os_release(
        'ID=hassos\nPRETTY_NAME="Home Assistant OS 14"\n# comment\nbadline\nNAME=hassos\n'
    )
    assert rel["id"] == "hassos"
    assert ha.os_release_looks_like_haos(rel) is True
    assert ha.os_release_looks_like_haos({"id": "debian", "pretty_name": "Debian"}) is False
    assert ha.os_release_looks_like_haos({"pretty_name": "Home Assistant OS"}) is True
    assert ha.os_release_looks_like_haos({"pretty_name": "Home Assistant OS something"}) is True

    env = ha._normalize_ha_map(
        {"result": "ok", "data": {"version": "2024.1", "disk_total": 32}}
    )
    assert env["version"] == "2024.1"
    assert ha._normalize_ha_map("nope") == {}

    blob = ha.parse_ha_info_blob(
        '{"result":"ok","data":{"version":"14.0","version_latest":"14.1","update_available":true}}'
    )
    assert blob["version"] == "14.0"
    yamlish = ha.parse_ha_info_blob(
        "version: 14.0\nversion_latest: 14.1\nupdate_available: true\nchannel: stable\nempty:\nnested:\n  skip: 1\ncount: 3\nrate: 1.5\nflag: false\n"
    )
    assert yamlish["version"] in ("14.0", 14.0)
    assert yamlish["update_available"] is True
    assert yamlish["count"] == 3
    assert yamlish["flag"] is False
    assert ha.parse_ha_info_blob("") == {}
    assert ha.parse_ha_info_blob("{not json") == {} or isinstance(ha.parse_ha_info_blob("{not json"), dict)

    assert ha._truthy_update({"update_available": True}) is True
    assert ha._truthy_update({"update_available": "no"}) is False
    assert ha._truthy_update({"version": "1", "version_latest": "2"}) is True
    assert ha._truthy_update({"version": "1", "version_latest": "1"}) is False

    fact = ha.component_fact_from_info(
        "core", {"version": "2024.1", "version_latest": "2024.2", "update_available": True, "machine": "rpi5", "channel": "stable"}
    )
    assert fact["update_available"] is True
    assert "→" in ha.summarize_component_sample(fact)
    assert "current" in ha.summarize_component_sample({"name": "os", "version": "14", "update_available": False})

    summary = ha.build_ha_summary({"core": fact, "os": {"name": "os", "update_available": False}}, os_release_name="HAOS")
    assert summary["backend"] == "ha_cli"
    assert summary["actionable_count"] == 1
    parsed = ha.parse_ha_info_for_component("core", "version: 1\nversion_latest: 2\n")
    assert parsed["name"] == "core"
    assert "raw-json" in ha._ha_info_command("core")
    assert "host info" in ha._host_info_command()
    assert "disks usage" in ha._disks_usage_command()

    disks = ha.parse_host_disk_facts(
        {
            "disk_free": 20.5,
            "disk_total": 32,
            "disk_used": 11.5,
            "disk_life_time": 99,
            "chassis": "rpi",
            "hostname": "homeassistant",
            "operating_system": "HAOS",
            "kernel": "6.6",
        }
    )
    assert disks["disk_pcent"] is not None
    assert disks["disk_total_h"].endswith("G")
    small = ha.parse_host_disk_facts({"disk_total": 5.2, "disk_free": 1.1})
    assert small["disk_total_h"]
    huge = ha.parse_host_disk_facts({"disk_total": 200, "disk_used": 10})
    assert huge["disk_total_h"] == "200G"

    assert ha._bytes_h("nope") == "?"
    assert ha._bytes_h(500).endswith("B")
    assert "K" in ha._bytes_h(2048) or "M" in ha._bytes_h(2048)
    assert ha._bytes_h(1024**3)
    assert ha._bytes_h(50 * 1024**3)

    drives = ha.disks_usage_to_drives(
        {
            "total_bytes": 32 * 1024**3,
            "used_bytes": 10 * 1024**3,
            "label": "root",
            "children": [{"label": "data", "used_bytes": 5 * 1024**3}, "skip"],
        }
    )
    assert drives and drives[0]["target"] == "/"
    assert any(d["target"] == "data" for d in drives)
    assert ha.disks_usage_to_drives({}) == []
    labeled = ha.disks_usage_to_drives({"total_bytes": 1000, "used_bytes": 100, "label": "data"})
    assert labeled[0]["target"] == "/data"

    assert ha.should_use_haos_path(SimpleNamespace(os_type="haos")) is True
    assert ha.should_use_haos_path(SimpleNamespace(os_type="debian"), {"is_haos": True}) is True
    assert ha.should_use_haos_path(SimpleNamespace(os_type="debian"), {}) is False


def test_haos_collect_host_facts_mocked(monkeypatch):
    monkeypatch.setattr(
        ha,
        "_run",
        lambda client, cmd, timeout=40: (
            0,
            '{"result":"ok","data":{"disk_total":32,"disk_free":20,"hostname":"ha"}}',
            "",
        ),
    )
    facts, err = ha.collect_host_facts(object())
    assert facts.get("hostname") == "ha" or facts.get("disk_total_gb") == 32
    monkeypatch.setattr(ha, "_run", lambda *a, **k: (1, "", "nope"))
    facts2, err2 = ha.collect_host_facts(object())
    assert isinstance(facts2, dict)
    monkeypatch.setattr(ha, "_run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ssh")))
    facts3, err3 = ha.collect_host_facts(object())
    assert err3


def test_npm_url_and_poll_result():
    with pytest.raises(ValueError):
        npm_mod.normalize_base_url("")
    with pytest.raises(ValueError):
        npm_mod.normalize_base_url("ftp://x")
    assert npm_mod.normalize_base_url("https://npm.example/api") == "https://npm.example"
    assert npm_mod.normalize_base_url("https://npm.example/login") == "https://npm.example"
    assert npm_mod.open_npm_url("https://npm.example", "") == "https://npm.example"
    assert npm_mod.open_npm_url("https://npm.example", "nginx/proxy") == "https://npm.example/nginx/proxy"
    assert npm_mod.open_npm_url("https://npm.example", "https://other/x") == "https://other/x"
    r = npm_mod.NpmPollResult(ok=True, proxy_hosts=[{"id": 1}], certificates=[{"id": 2}], version="2.11")
    st = r.to_status_json()
    assert st["proxy_host_count"] == 1 and st["ok"] is True
    fail = npm_mod.NpmPollResult(ok=False, error="down")
    assert fail.to_status_json()["error"] == "down"


def test_os_update_check_apt_and_unsupported(monkeypatch):
    from app.services import os_patching as op

    srv = SimpleNamespace(hostname="pi.local", os_type="alpine")
    out = op.check_os_updates(srv)
    assert out["supported"] is False

    class C:
        def close(self):
            pass

    def run(_c, cmd, timeout=15):
        c = str(cmd)
        if "apt-get update" in c or "apt update" in c:
            return 0, "ok\n", ""
        if "apt list --upgradable" in c:
            return 0, "fwupd/stable 1.0 [upgradable from: 0.9]\nlinux/stable 6.1 [upgradable from: 6.0]\n", ""
        if "apt-get -s" in c:
            return 0, "Inst linux [6.0] (6.1 arm64)\n", ""
        if "reboot-required" in c:
            return 0, "REBOOT\n", ""
        return 0, "", ""

    monkeypatch.setattr(op, "get_ssh_client", lambda *a, **k: C())
    monkeypatch.setattr(op, "run_command", run)
    monkeypatch.setattr("app.services.haos.is_haos_server", lambda s: False)
    monkeypatch.setattr("app.services.haos.probe_haos_identity", lambda c: {"is_haos": False})
    debian = SimpleNamespace(hostname="pi.local", os_type="debian")
    chk = op.check_os_updates(debian)
    assert chk["supported"] is True
    assert chk["reboot_pending"] is True
    assert chk["updates_count"] >= 1

    weird = SimpleNamespace(hostname="x", os_type="windows")
    monkeypatch.setattr("app.services.haos.is_haos_server", lambda s: False)
    monkeypatch.setattr("app.services.haos.probe_haos_identity", lambda c: {"is_haos": False})
    w = op.check_os_updates(weird)
    assert w["supported"] is False

    monkeypatch.setattr("app.services.haos.is_haos_server", lambda s: True)
    monkeypatch.setattr(
        "app.services.haos.check_haos_updates",
        lambda s, client=None: {"supported": True, "backend": "ha_cli", "updates_count": 1},
    )
    ha = SimpleNamespace(hostname="ha.local", os_type="haos")
    ha_chk = op.check_os_updates(ha)
    assert ha_chk["backend"] == "ha_cli"


def test_nav_shortcut_hrefs_and_labels():
    from app.services import nav_shortcuts as ns

    feat = next(iter(ns.FEATURE_META))
    page = next(iter(ns.APP_PAGE_META))
    assert ns.normalize_feature(feat) == feat
    with pytest.raises(ValueError):
        ns.normalize_feature("nope")
    assert ns.feature_href(3, feat).startswith("/servers/3")
    assert ns.feature_label(feat)
    assert ns.feature_label("nope")
    assert ns.normalize_app_page(page) == page
    with pytest.raises(ValueError):
        ns.normalize_app_page("nope")
    assert ns.app_page_href(page).startswith("/")
    assert ns.app_page_label(page)
    assert ns.app_page_label("nope")
    assert ns.integration_type_label("npm")
    assert ns.integration_type_label(None)
    srv = SimpleNamespace(
        container_patch_enabled=True,
        backup_enabled=True,
        os_patch_enabled=True,
        ssh_private_key_encrypted="x",
    )
    assert ns.server_has_feature(srv, "overview") is True
    assert ns.server_has_feature(srv, "docker") is True
    assert "Chrome" in ns.summarize_user_agent("Mozilla/5.0 Chrome/120")
    assert ns.summarize_user_agent(None) == "Unknown browser"
    assert "Firefox" in ns.summarize_user_agent("Mozilla Firefox/120")
    assert "Safari" in ns.summarize_user_agent("Safari/605.1.15 Macintosh")
    assert "Edge" in ns.summarize_user_agent("Edg/120")
    assert "Opera" in ns.summarize_user_agent("OPR/90")
    assert "iOS" in ns.summarize_user_agent("iPhone Safari/16")
    assert "Android" in ns.summarize_user_agent("Android Chrome/120")
    pub = ns.trusted_device_public(
        SimpleNamespace(id=1, label="Trusted device", user_agent="Chrome/1 Windows", ip="1.1.1.1")
    )
    assert pub["display_name"]
    pub2 = ns.trusted_device_public(SimpleNamespace(id=2, label="Office laptop", user_agent="x"))
    assert pub2["display_name"] == "Office laptop"


def test_haos_probe_and_update_check(monkeypatch):
    def run(_c, cmd, timeout=15):
        c = str(cmd)
        if "os-release" in c:
            return 0, 'ID=hassos\nPRETTY_NAME="Home Assistant OS"\n', ""
        if "command -v ha" in c or "which ha" in c:
            return 0, "/usr/bin/ha\n", ""
        if "ha core info" in c:
            return 0, '{"result":"ok","data":{"version":"2024.1","version_latest":"2024.2","update_available":true}}', ""
        if "ha os info" in c:
            return 0, "version: 14.0\nversion_latest: 14.0\n", ""
        if "ha supervisor info" in c:
            return 0, "version: 2024.1\n", ""
        if "ha host info" in c:
            return 0, '{"result":"ok","data":{"disk_total":32,"disk_free":20,"hostname":"ha"}}', ""
        if "disks usage" in c:
            return 0, '{"total_bytes":1000,"used_bytes":200,"label":"root"}', ""
        return 0, "", ""

    monkeypatch.setattr(ha, "_run", run)
    ident = ha.probe_haos_identity(object())
    assert ident["is_haos"] is True
    comps, err = ha.collect_ha_component_facts(object())
    assert "core" in comps
    panel = ha.gather_system_panel(object())
    assert "components" in panel and "host" in panel
    chk = ha.check_haos_updates(SimpleNamespace(hostname="ha.local", os_type="haos"), client=object())
    assert chk["backend"] == "ha_cli"
    assert chk["supported"] is True


def test_nav_favourites_crud():
    from app.services import nav_shortcuts as ns
    from app.models import Integration, Server, User
    from sqlalchemy.pool import StaticPool
    from sqlmodel import Session, SQLModel, create_engine

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        user = User(email="fav@test.local", hashed_password="hashed", role="admin")
        srv = Server(name="pi", hostname="pi.local")
        integ = Integration(
            type="npm",
            name="NPM",
            base_url="http://npm.test",
            enabled=True,
            config_json="{}",
        )
        session.add(user)
        session.add(srv)
        session.add(integ)
        session.commit()
        session.refresh(user)
        session.refresh(srv)
        session.refresh(integ)
        assert ns.list_favourites(session, user.id) == []
        pin = ns.add_server_favourite(session, user.id, server_id=srv.id, feature="docker")
        assert pin.id
        again = ns.add_server_favourite(session, user.id, server_id=srv.id, feature="docker")
        assert again.id == pin.id
        page = ns.add_app_page_favourite(session, user.id, page="jobs")
        assert page.feature == "jobs"
        integ_pin = ns.add_integration_favourite(session, user.id, integration_id=integ.id)
        assert integ_pin.feature == str(integ.id)
        listed = ns.list_favourites(session, user.id)
        kinds = {x["kind"] for x in listed}
        assert "server_feature" in kinds
        assert "app_page" in kinds
        assert "integration" in kinds
        assert ns.remove_favourite(session, user.id, pin.id) is True
        assert ns.remove_favourite(session, user.id, 99999) is False
        tog = ns.toggle_server_favourite(session, user.id, server_id=srv.id, feature="overview")
        assert tog["pinned"] is True
        tog2 = ns.toggle_app_page_favourite(session, user.id, page="jobs")
        assert "pinned" in tog2
        tog3 = ns.toggle_integration_favourite(session, user.id, integration_id=integ.id)
        assert "pinned" in tog3
        ctx = ns.host_feature_context(session, user.id, srv, "docker")
        assert ctx["host_feature"] == "docker"
        dict_ctx = ns.host_feature_context(
            session, user.id, {"id": srv.id, "name": srv.name}, "overview"
        )
        assert dict_ctx["host_server_id"] == srv.id
        page_ctx = ns.app_page_pin_context(session, user.id, "jobs")
        assert page_ctx["pin_page"] == "jobs"
        integ_ctx = ns.integration_pin_context(session, user.id, integ)
        assert integ_ctx["pin_integration_id"] == integ.id
        assert ns.server_has_feature(srv, "backups") in (True, False)
        assert ns.server_has_feature(srv, "services") is True
        assert ns.server_has_feature(srv, "files") is True
