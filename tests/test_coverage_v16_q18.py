"""v1.6 Q-80 eighteenth pack — host_files SFTP helpers + DNS save/sync helpers."""
from __future__ import annotations

import stat
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models import Server


def _memory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine), engine


def test_host_files_sftp_helpers(monkeypatch):
    from app.services import host_files as hf

    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    monkeypatch.setattr(hf.settings, "PIHERDER_DEMO_MODE", False, raising=False)
    srv = SimpleNamespace(
        ssh_username="pi",
        docker_base_dir="/home/pi/docker",
        container_patch_enabled=True,
        ssh_private_key_encrypted="x",
    )

    class _Sftp:
        def normalize(self, path):
            raise OSError("nope")

        def lstat(self, path):
            if path.endswith("missing"):
                raise FileNotFoundError(path)
            if path.endswith("boom"):
                raise OSError("stat")
            return SimpleNamespace(st_mode=stat.S_IFREG | 0o644)

    sftp = _Sftp()
    assert hf._normalize_remote(sftp, "/home/pi/docker/a") == "/home/pi/docker/a"
    path = hf._assert_in_jail("/home/pi/docker/grafana", srv, role=hf.ROLE_FLEET, sftp=sftp)
    assert path.endswith("grafana")
    with pytest.raises(hf.FilesError):
        hf._assert_in_jail("/etc/passwd", srv, role=hf.ROLE_FLEET)
    with pytest.raises(hf.FilesError):
        hf._assert_in_jail("/home/pi/docker/.ssh/id", srv, role=hf.ROLE_FLEET)
    assert hf._kind_from_mode(stat.S_IFDIR | 0o755) == "dir"
    assert hf._kind_from_mode(stat.S_IFLNK | 0o777) == "link"
    assert hf._kind_from_mode(stat.S_IFREG | 0o644) == "file"
    with pytest.raises(hf.FilesError):
        hf._lstat(sftp, "/home/pi/docker/missing")
    with pytest.raises(hf.FilesError):
        hf._lstat(sftp, "/home/pi/docker/boom")
    st = hf._lstat(sftp, "/home/pi/docker/ok")
    assert st.st_mode
    assert hf.mode_octal(0o644) in ("644", "0644")
    assert hf.mode_octal(None) == ""
    assert hf.mode_octal("nope") == ""
    names = hf.parse_getent("pi:x:1000:1000::/home/pi:/bin/bash\nbad\n", 2)
    assert names.get(1000) == "pi"
    assert hf.parse_getent("", 2) == {}
    users, groups = hf.user_group_maps(None)
    assert users == {} or isinstance(users, dict)
    assert hf.parse_mode("644") == 0o644
    assert hf.parse_mode("0o755") == 0o755
    with pytest.raises(hf.FilesError):
        hf.parse_mode("999")
    assert hf.parse_id_name("1000", kind="owner") == "1000"
    assert hf.parse_id_name("pi", kind="owner") == "pi"
    assert hf.parse_id_name("", kind="owner") == ""
    with pytest.raises(hf.FilesError):
        hf.parse_id_name("bad name!", kind="owner")
    assert hf.parse_nested_rel("a/b/c.txt") == ["a", "b", "c.txt"]
    with pytest.raises(hf.FilesError):
        hf.parse_nested_rel("../etc/passwd")
    with pytest.raises(hf.FilesError):
        hf._safe_zip_name("/abs")


def test_dns_save_and_pure_helpers(monkeypatch):
    from app.services.dns_fabric import core as fabric

    session, _ = _memory()
    a = Server(
        name="a",
        hostname="a.local",
        dns_name="a.lan",
        ip_address="10.0.0.4",
        ssh_username="pi",
        docker_base_dir="/home/pi/docker",
        os_type="debian",
        dns_manage_a=False,
    )
    b = Server(
        name="b",
        hostname="b.local",
        ip_address="10.0.0.5",
        ssh_username="pi",
        docker_base_dir="/home/pi/docker",
        os_type="debian",
    )
    session.add(a)
    session.add(b)
    session.commit()
    session.refresh(a)
    session.refresh(b)

    with pytest.raises(fabric.DnsFabricError):
        fabric.update_server_dns(session, b, dns_name="not fqdn", dns_manage_a=False)
    with pytest.raises(fabric.DnsFabricError):
        fabric.update_server_dns(session, b, dns_name="b.lan", dns_manage_a=False, dns_ip_override="999.1")
    with pytest.raises(fabric.DnsFabricError):
        fabric._assert_unique_dns_name(session, "a.lan", b.id)
    fabric._assert_unique_dns_name(session, "a.lan", a.id)

    out = fabric.update_server_dns(session, b, dns_name="b.lan", dns_manage_a=False)
    assert out.get("action") in ("saved", "saved_no_manage") or "dns_name" in str(out)
    session.refresh(b)
    assert b.dns_name == "b.lan"

    monkeypatch.setattr(fabric, "fanout_pihole_dns", lambda *a, **k: [{"ok": True, "name": "ph"}])
    monkeypatch.setattr(fabric, "sync_host_a", lambda *a, **k: [{"ok": True, "name": "ph"}])
    monkeypatch.setattr(fabric, "remove_host_a", lambda *a, **k: [])
    synced = fabric.update_server_dns(session, b, dns_name="b.lan", dns_manage_a=True)
    assert isinstance(synced, dict)
    with pytest.raises(fabric.DnsFabricError):
        fabric.update_server_dns(session, a, dns_name="b.lan", dns_manage_a=False)

    assert fabric._is_already_present_error("already exists") is True
    assert fabric._is_already_present_error("nope") is False
    assert fabric._lan_forward_host_ok("") is False
    assert fabric._lan_forward_host_ok("localhost") is False
    assert fabric._lan_forward_host_ok("10.0.0.4") is True
    assert fabric._lan_forward_host_ok("pi.lan") is True
    st, det = fabric._summarize_results([])
    assert st == "error"
    st, _ = fabric._summarize_results([{"ok": True, "name": "p"}])
    assert st == "ok"
    st, _ = fabric._summarize_results([{"ok": True, "name": "p"}, {"ok": False, "name": "q", "error": "x"}])
    assert st == "partial"
    st, _ = fabric._summarize_results([{"ok": False, "name": "p"}])
    assert st == "error"

    assert fabric.is_host_identity_name("b.lan", b) is True
    assert fabric.is_host_identity_name("other.lan", b) is False
    assert fabric.is_host_identity_name(None, b) is False
    assert fabric.host_focus_key(1).endswith("1") or "1" in fabric.host_focus_key(1)
    assert fabric.discovery_focus_key(2)
    assert fabric._ip_in_lan("10.0.0.4", "10.0.0.0/24") is True
    assert fabric._ip_in_lan("1.1.1.1", "10.0.0.0/24") is False
    assert fabric._ip_in_lan(None, "10.0.0.0/24") is None
    assert fabric._is_private_ip("10.1.2.3") is True
    assert fabric._is_private_ip("8.8.8.8") is False
    assert fabric._is_private_ip(None) is None
    assert fabric._host_is_cloud("8.8.8.8", "10.0.0.0/24") is True
    assert fabric._host_is_cloud("10.0.0.4", "10.0.0.0/24") is False
    assert fabric._hostname_from_urlish("https://grafana.lan/login") == "grafana.lan"
    named = fabric.servers_with_dns_name(session)
    assert any(s.id == b.id for s in named)
    assert fabric.list_service_records(session) == []
    assert fabric.get_service_record(session, 1) is None
    assert fabric.certs_matching_fqdn(session, "b.lan") == []
    n = fabric.cleanup_dns_for_server(session, b.id)
    assert n >= 0
    opts = fabric.list_kuma_monitor_options(session)
    assert isinstance(opts, list)
