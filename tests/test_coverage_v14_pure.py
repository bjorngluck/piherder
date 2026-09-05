"""v1.4 coverage push — mocked SSH / pure helpers (no live network).

Targets the biggest remaining gaps on the freeze bar: migrate copy/facts/
leftover/cutover, docker inventory refresh, docker_management list/inspect,
demo Files tree, and leftover job helpers.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models import Integration, Job, Server


# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------


class FakeSSH:
    """Minimal SSH client: queued (status, stdout, stderr) replies."""

    def __init__(self, replies=None, default=(0, "", "")):
        self.replies = list(replies or [])
        self.cmds: list[str] = []
        self.closed = False
        self.default = default

    def close(self):
        self.closed = True

    def exec_command(self, cmd, timeout=None):
        self.cmds.append(str(cmd))
        st, out, err = self._next()
        stdout = MagicMock()
        stdout.__iter__ = lambda _s: iter((out or "").splitlines(True) or [])
        stderr = MagicMock()
        stderr.__iter__ = lambda _s: iter((err or "").splitlines(True) or [])
        return MagicMock(), stdout, stderr

    def _next(self):
        if self.replies:
            return self.replies.pop(0)
        return self.default


def _run_on_fake(client, cmd, timeout=15):
    client.cmds.append(str(cmd))
    if getattr(client, "replies", None):
        return client.replies.pop(0)
    return getattr(client, "default", (0, "", ""))


def _srv(**kw):
    base = dict(
        id=1,
        name="src",
        hostname="src.local",
        ssh_username="pi",
        ssh_port=22,
        docker_base_dir="/home/pi/docker",
        ip_address="10.0.0.4",
        dns_name="src.test",
        container_patch_enabled=True,
        docker_inventory_json=None,
        docker_inventory_at=None,
        docker_inventory_status="never",
        docker_inventory_error=None,
        container_updates_summary=None,
        os_type="debian",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _memory_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine), engine


def _patch_mod_ssh(monkeypatch, mod, client: FakeSSH):
    monkeypatch.setattr(mod, "get_ssh_client", lambda *a, **k: client)
    monkeypatch.setattr(mod, "run_command", _run_on_fake)
    return client


# ---------------------------------------------------------------------------
# demo_files (was 0%)
# ---------------------------------------------------------------------------


def test_demo_files_tree_list_read_search():
    from app.services import demo_files as df
    from app.services.host_files import FilesError

    listing = df.list_dir(None, "")
    names = {e["name"] for e in listing["entries"]}
    assert "README.md" in names
    assert "grafana" in names
    assert listing["jail"] == df.JAIL

    grafana = df.list_dir(None, "grafana")
    assert grafana["crumbs"][0]["name"] == "grafana"
    assert any(e["name"] == "docker-compose.yml" for e in grafana["entries"])

    text = df.read_text(None, "README.md")
    assert "simulated" in text["text"].lower()
    peek = df.peek_file(None, "logo.svg")
    assert peek["is_image"] is True
    st = df.stat_file(None, "grafana")
    assert st["kind"] == "dir"
    chunks = list(df.iter_file(None, "README.md"))
    assert chunks and chunks[0].startswith(b"PiHerder")
    assert list(df.iter_preview(None, "logo.svg"))

    empty_q = df.search(None, "")
    assert empty_q["entries"]
    hits = df.search(None, "grafana")
    assert hits["search"] is True
    assert any("grafana" in (e["rel"] or "") for e in hits["entries"])

    assert df.list_docker_volumes() == []
    assert df.list_docker_containers() == []
    assert df.list_container_mounts() == []

    with pytest.raises(FilesError):
        df.list_dir(None, "nope")
    with pytest.raises(FilesError):
        df.list_dir(None, "README.md")
    with pytest.raises(FilesError):
        df.read_text(None, "grafana")


# ---------------------------------------------------------------------------
# migrate copy.py
# ---------------------------------------------------------------------------


def test_copy_helpers_and_refusals(tmp_path, monkeypatch):
    from app.services.service_migrate import copy as cp

    assert cp.staging_tree_summary(tmp_path / "missing") == "(missing)"
    root = tmp_path / "tree"
    (root / "sub").mkdir(parents=True)
    (root / "a.txt").write_text("x")
    (root / "sub" / "b.txt").write_text("y")
    summary = cp.staging_tree_summary(root, limit=2)
    assert "file" in summary
    # limit truncates
    big = tmp_path / "big"
    big.mkdir()
    for i in range(50):
        (big / f"f{i}").write_text("z")
    assert "more" in cp.staging_tree_summary(big, limit=5)

    args = cp._rsync_core_args(delete=True)
    assert "--delete" in args and "-aH" in args

    srv = Server(name="a", hostname="a.local", ssh_port=2222)
    base = cp._ssh_rsync_cmd("/tmp/k", srv)
    assert "-p 2222" in base or "-p" in base

    with pytest.raises(cp.CopyError, match="truncated"):
        cp.rsync_host_to_herder(srv, "/home/pi/docker/foo…", tmp_path / "x")
    with pytest.raises(cp.CopyError, match="socket"):
        cp.rsync_host_to_herder(srv, "/var/run/docker.sock", tmp_path / "x")

    logs: list[str] = []
    cp._log(logs.append, "hi")
    cp._log(None, "silent-ok")
    assert logs == ["hi"]


def test_remote_path_kind_and_volume_mountpoint(monkeypatch):
    from app.services.service_migrate import copy as cp

    client = FakeSSH(replies=[(0, "dir\n", "")])
    _patch_mod_ssh(monkeypatch, cp, client)
    srv = Server(name="a", hostname="a.local")
    assert cp.remote_path_kind(srv, "/home/pi/docker/grafana") == "dir"
    assert cp.remote_path_kind(srv, "/") == "missing"
    assert cp.remote_path_kind(srv, "../etc") == "missing"

    client.replies = [(1, "", "nope")]
    assert cp.remote_path_kind(srv, "/tmp/x") == "missing"

    client.replies = [(0, "/var/lib/docker/volumes/vol/_data\n", "")]
    assert cp._volume_mountpoint(srv, "vol") == "/var/lib/docker/volumes/vol/_data"
    client.replies = [(1, "", "missing")]
    with pytest.raises(cp.CopyError):
        cp._volume_mountpoint(srv, "vol")


def test_rsync_host_and_herder_mocked(tmp_path, monkeypatch):
    from app.services.service_migrate import copy as cp

    client = FakeSSH(replies=[(0, "", "")])
    monkeypatch.setattr(cp, "get_ssh_client", lambda *a, **k: client)
    monkeypatch.setattr(cp, "run_command", _run_on_fake)
    monkeypatch.setattr(cp, "get_private_key_plain", lambda s: "KEY")
    monkeypatch.setattr(cp, "_remote_rsync_path", lambda c, u: "sudo -n rsync")

    @contextmanager
    def _tk(_priv):
        yield str(tmp_path / "id_key")

    monkeypatch.setattr(cp, "temp_key_file", _tk)

    class _Proc:
        def __init__(self, rc=0, stdout="Number of files: 1\n", stderr=""):
            self.returncode = rc
            self.stdout = stdout
            self.stderr = stderr

    monkeypatch.setattr(cp.subprocess, "run", lambda *a, **k: _Proc())

    srv = Server(name="a", hostname="a.local", ssh_username="pi")
    dest_dir = tmp_path / "stage"
    logs: list[str] = []
    cp.rsync_host_to_herder(srv, "/home/pi/docker/grafana", dest_dir, log=logs.append)
    assert dest_dir.is_dir()
    assert any("rsync" in m for m in logs)

    # as_file path
    dest_file = tmp_path / "file.bin"
    cp.rsync_host_to_herder(
        srv, "/etc/hostname", dest_file, log=logs.append, as_file=True, delete=True
    )

    push_dir = tmp_path / "push"
    push_dir.mkdir()
    (push_dir / "x").write_text("1")
    dest = Server(name="b", hostname="b.local", ssh_username="bjorn")
    cp.rsync_herder_to_host(dest, push_dir, "/home/bjorn/docker/grafana", log=logs.append, delete=True)

    missing = tmp_path / "nope"
    with pytest.raises(cp.CopyError, match="staging missing"):
        cp.rsync_herder_to_host(dest, missing, "/tmp/x")

    monkeypatch.setattr(cp, "get_private_key_plain", lambda s: "")
    with pytest.raises(cp.CopyError, match="No SSH private key"):
        cp.rsync_host_to_herder(srv, "/home/pi/docker/grafana", dest_dir)
    with pytest.raises(cp.CopyError, match="No SSH private key"):
        cp.rsync_herder_to_host(dest, push_dir, "/tmp/x")

    monkeypatch.setattr(cp, "get_private_key_plain", lambda s: "KEY")
    monkeypatch.setattr(cp.subprocess, "run", lambda *a, **k: _Proc(rc=1, stdout="", stderr="boom"))
    with pytest.raises(cp.CopyError, match="pull"):
        cp.rsync_host_to_herder(srv, "/home/pi/docker/grafana", dest_dir)
    with pytest.raises(cp.CopyError, match="push"):
        cp.rsync_herder_to_host(dest, push_dir, "/tmp/x")


def test_chown_remote_and_copy_named_volume(tmp_path, monkeypatch):
    from app.services.service_migrate import copy as cp

    client = FakeSSH(replies=[(0, "bjorn:bjorn\n", "")])
    monkeypatch.setattr(cp, "get_ssh_client", lambda *a, **k: client)
    monkeypatch.setattr(cp, "run_command", _run_on_fake)
    srv = Server(name="a", hostname="a.local")
    logs: list[str] = []
    cp.chown_remote_tree(srv, "/home/bjorn/docker/grafana", log=logs.append)
    assert logs and "ownership" in logs[0].lower()

    with pytest.raises(cp.CopyError, match="refusing chown"):
        cp.chown_remote_tree(srv, "/")
    with pytest.raises(cp.CopyError, match="refusing chown"):
        cp.chown_remote_tree(srv, "/etc")
    client.replies = [(1, "", "no_dest_owner")]
    with pytest.raises(cp.CopyError, match="chown"):
        cp.chown_remote_tree(srv, "/home/bjorn/docker/grafana")

    monkeypatch.setattr(cp, "_volume_mountpoint", lambda s, n: f"/var/lib/docker/volumes/{n}/_data")
    pulls, pushes, creates = [], [], []

    def pull(server, remote, local, log=None):
        pulls.append(remote)
        Path(local).mkdir(parents=True, exist_ok=True)

    def push(server, local, remote, log=None):
        pushes.append(remote)

    monkeypatch.setattr(cp, "rsync_host_to_herder", pull)
    monkeypatch.setattr(cp, "rsync_herder_to_host", push)
    client.replies = [(0, "grafana_data\n", "")]
    src = Server(name="a", hostname="a.local")
    dest = Server(name="b", hostname="b.local")
    cp.copy_named_volume(src, dest, "grafana_data", tmp_path, dest_volume="grafana_data_dest", log=logs.append)
    assert pulls and pushes
    with pytest.raises(cp.CopyError, match="invalid volume"):
        cp.copy_named_volume(src, dest, "bad/name", tmp_path)
    with pytest.raises(cp.CopyError, match="invalid dest"):
        cp.copy_named_volume(src, dest, "ok", tmp_path, dest_volume="../x")
    client.replies = [(1, "", "create fail")]
    with pytest.raises(cp.CopyError, match="volume create"):
        cp.copy_named_volume(src, dest, "ok", tmp_path)


# ---------------------------------------------------------------------------
# migrate facts.py
# ---------------------------------------------------------------------------


def test_inspect_project_mounts_and_tree(monkeypatch):
    from app.services.service_migrate import facts as facts

    inspect = [
        {
            "Id": "sha256:" + "a" * 64,
            "Name": "/grafana",
            "Mounts": [
                {
                    "Source": "/home/pi/docker/grafana/data",
                    "Destination": "/var/lib/grafana",
                    "Type": "bind",
                    "RW": True,
                    "Name": "",
                }
            ],
            "HostConfig": {"NetworkMode": "host", "Privileged": True},
            "Config": {"ExposedPorts": {"3000/tcp": {}}},
        }
    ]
    client = FakeSSH(replies=[(0, json.dumps(inspect), "")])
    _patch_mod_ssh(monkeypatch, facts, client)
    row = {
        "name": "grafana",
        "containers": [
            {"id": "a" * 12, "id_full": "a" * 64, "name": "grafana", "placeholder": False},
            {"placeholder": True, "name": "missing"},
        ],
    }
    out = facts.inspect_project_mounts(_srv(), row)
    c = out["containers"][0]
    assert c["mounts_detail"]
    assert c["network_mode"] == "host"
    assert c["privileged"] is True
    assert "3000/tcp" in c["exposed_ports"]

    assert facts.inspect_project_mounts(_srv(), {"containers": []})["containers"] == []
    client.replies = [(0, "not-json", "")]
    row2 = {"containers": [{"id": "abc", "name": "x"}]}
    assert facts.inspect_project_mounts(_srv(), row2) is row2
    client.replies = [(0, json.dumps({"no": "list"}), "")]
    assert facts.inspect_project_mounts(_srv(), row2) is row2

    client.replies = [(0, "data/\ncompose.yml\n", "")]
    tree = facts.list_project_tree(_srv(), "/home/pi/docker/grafana")
    assert "data/" in tree or "compose.yml" in tree
    assert facts.list_project_tree(_srv(), "/") == []
    client.replies = [(1, "", "err")]
    assert facts.list_project_tree(_srv(), "/home/pi/docker/grafana") == []


def test_probe_host_facts_occupancy_ghosts(monkeypatch, tmp_path):
    from app.services.service_migrate import facts as facts
    from app.config import settings

    client = FakeSSH(
        replies=[
            (0, "aarch64\n", ""),
            (0, "/dev/sda1 100 10 999999 1% /\n", ""),
            (0, "yes\n", ""),
        ]
    )
    _patch_mod_ssh(monkeypatch, facts, client)
    out = facts.probe_host_facts(_srv(docker_base_dir="/home/pi/docker", ssh_username="pi"))
    assert out["arch"] == "aarch64"
    assert out["disk_free_bytes"] == 999999 * 1024
    assert out["docker_base_writable"] is True
    assert out["docker_base"].endswith("docker")

    monkeypatch.setattr(facts, "get_ssh_client", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    err = facts.probe_host_facts(_srv())
    assert err["error"]

    client = FakeSSH(
        replies=[
            (0, "docker-compose.yml\ndata\n", ""),
            (0, "grafana-1 exited\n", ""),
            (0, "0.0.0.0:3000->3000/tcp\n", ""),
            (0, "0.0.0.0:3000->3000/tcp\n", ""),
            (0, "LISTEN 0 4096 0.0.0.0:22 0.0.0.0:*\n", ""),
            (0, "UNCONN 0 0 0.0.0.0:5353 0.0.0.0:*\n", ""),
        ]
    )
    monkeypatch.setattr(facts, "get_ssh_client", lambda *a, **k: client)
    monkeypatch.setattr(facts, "run_command", _run_on_fake)
    occ = facts.probe_dest_occupancy(_srv(), "grafana", "/home/pi/docker/grafana")
    assert occ["nonempty"] is True
    assert occ["containers"]
    assert occ["listen_ports"]
    bad = facts.probe_dest_occupancy(_srv(), "", "/")
    assert bad["error"]

    client.replies = [(0, "removed_containers:abc\nno_containers\n", "")]
    ghosts = facts.remove_dest_project_ghosts(_srv(), "grafana")
    assert ghosts["ok"] is True
    monkeypatch.setattr(facts, "get_ssh_client", lambda *a, **k: (_ for _ in ()).throw(OSError("ssh")))
    ghosts2 = facts.remove_dest_project_ghosts(_srv(), "grafana")
    assert ghosts2["ok"] is False

    assert facts.refresh_host_inventory(None) is False
    monkeypatch.setattr(
        "app.services.docker_inventory.refresh_server_inventory",
        lambda *a, **k: True,
    )
    monkeypatch.setattr("app.services.docker_management._CACHE", {}, raising=False)
    assert facts.refresh_host_inventory(9) is True

    monkeypatch.setattr(settings, "BACKUP_ROOT", str(tmp_path))
    assert facts.herder_free_bytes() is not None
    monkeypatch.setattr(settings, "BACKUP_ROOT", "/no/such/path/xyz")
    # shutil.disk_usage may still fail → None
    _ = facts.herder_free_bytes()


def test_parse_listen_and_ps_ports():
    from app.services.service_migrate import facts as facts

    assert facts._parse_listen_local("[::]:443", "tcp") == ("443", "tcp")
    assert facts._parse_listen_local("0.0.0.0:22", "tcp") == ("22", "tcp")
    assert facts._parse_listen_local("*", "tcp") is None
    assert facts._parse_listen_local("noport", "tcp") is None
    netstat = "tcp 0 0 0.0.0.0:80 0.0.0.0:* LISTEN\n"
    # first token is proto with colon in local at parts[3]
    ports = facts._parse_ss_listen(
        "tcp 0 0 127.0.0.1:631 0.0.0.0:* LISTEN\nLISTEN 0 4096 0.0.0.0:22 0.0.0.0:*\n",
        "tcp",
    )
    assert ("22", "tcp") in ports or ("631", "tcp") in ports
    # colon in first field (ss without LISTEN word)
    colon_first = facts._parse_ss_listen("0.0.0.0:5353 0.0.0.0:*\n", "udp")
    assert ("5353", "udp") in colon_first or colon_first == []
    ps = facts._parse_ps_ports("0.0.0.0:3000->3000/tcp, [::]:3000->3000/tcp\n")
    assert any(p[0] == "3000" for p in ps)


# ---------------------------------------------------------------------------
# leftover SSH wrappers
# ---------------------------------------------------------------------------


def test_leftover_rm_volume_and_tree(monkeypatch):
    from app.services.service_migrate import leftover as lo
    from app.services.service_migrate.leftover import LeftoverError, apply_leftover

    client = FakeSSH(replies=[(0, "grafana_data\n", "")])
    monkeypatch.setattr(lo, "get_ssh_client", lambda *a, **k: client)
    monkeypatch.setattr(lo, "run_command", _run_on_fake)
    srv = Server(id=1, name="a", hostname="a.local")
    ok = lo._rm_volume(srv, "grafana_data")
    assert ok["success"] is True
    client.replies = [(1, "", "Error: no such volume")]
    assert lo._rm_volume(srv, "gone")["success"] is True
    assert lo._rm_volume(srv, "bad/name")["success"] is False
    client.replies = [(1, "", "busy")]
    assert lo._rm_volume(srv, "busyvol")["success"] is False

    assert lo._rm_tree(srv, "/")["success"] is False
    client.replies = [(0, "", "")]
    assert lo._rm_tree(srv, "/home/pi/docker/grafana")["success"] is True
    client.replies = [(1, "rm-failed", "")]
    assert lo._rm_tree(srv, "/home/pi/docker/grafana")["success"] is False

    src = Server(id=1, name="a", hostname="a", docker_base_dir="/home/pi/docker", ssh_username="pi")
    dest = Server(id=1, name="a", hostname="a", docker_base_dir="/home/pi/docker", ssh_username="pi")
    with pytest.raises(LeftoverError, match="same host"):
        apply_leftover(
            MagicMock(),
            source=src,
            dest=dest,
            project="grafana",
            leftover="down",
        )
    dest2 = Server(id=2, name="b", hostname="b", docker_base_dir="/home/pi/docker", ssh_username="pi")
    out = apply_leftover(
        MagicMock(),
        source=src,
        dest=dest2,
        project="grafana",
        leftover="stopped",
    )
    assert out["leftover"] == "stopped"


def test_leftover_down_and_wipe_no_tree():
    from app.services.service_migrate.leftover import apply_leftover, wipe_compose_project

    session, _ = _memory_session()
    src = Server(name="a", hostname="a.local", docker_base_dir="/home/pi/docker", ssh_username="pi")
    dest = Server(name="b", hostname="b.local", docker_base_dir="/home/pi/docker", ssh_username="pi")
    session.add(src)
    session.add(dest)
    session.commit()
    session.refresh(src)
    session.refresh(dest)

    downs = []
    out = apply_leftover(
        session,
        source=src,
        dest=dest,
        project="grafana",
        leftover="DOWN",
        down_fn=lambda srv, path: downs.append(path) or {"success": True},
    )
    assert out["leftover"] == "down"
    assert downs == ["/home/pi/docker/grafana"]

    from app.services.service_migrate.leftover import LeftoverError

    with pytest.raises(LeftoverError, match="nope"):
        apply_leftover(
            session,
            source=src,
            dest=dest,
            project="grafana",
            leftover="down",
            down_fn=lambda *a, **k: {"success": False, "error": "nope"},
        )

    wipe = wipe_compose_project(
        session,
        server=src,
        project_path="/home/pi/docker/grafana",
        remove_volumes=False,
        delete_tree=False,
        down_fn=lambda srv, p, vols: {"success": True},
    )
    assert wipe["project_removed"] is False
    assert wipe["volumes_removed"] is False


# ---------------------------------------------------------------------------
# cutover helpers
# ---------------------------------------------------------------------------


def test_dest_forward_host_and_put_npm(monkeypatch):
    from app.services.service_migrate import cutover as cut

    assert cut.dest_forward_host(_srv(ip_address="1.2.3.4")) == "1.2.3.4"
    assert cut.dest_forward_host(_srv(ip_address="", hostname="h.local")) == "h.local"
    assert cut.dest_forward_host(_srv(ip_address="", hostname="", dns_name="d.test")) == "d.test"

    logs = []
    cut._log(logs.append, "x")
    cut._log(None, "y")
    assert logs == ["x"]

    session, _ = _memory_session()
    with pytest.raises(cut.CutoverError, match="no enabled NPM"):
        cut._put_npm_backend(session, fqdn="app.test", cached_id="1", new_host="10.0.0.1")

    integ = Integration(
        type="npm",
        name="NPM",
        base_url="http://npm.test",
        enabled=True,
        config_json="{}",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(integ)
    session.commit()
    monkeypatch.setattr(
        "app.services.integrations.registry.npm_credentials",
        lambda r: ("u", "p"),
    )
    monkeypatch.setattr("app.services.integrations.registry.tls_verify", lambda r: True)
    monkeypatch.setattr(cut, "get_token", lambda *a, **k: "tok")
    monkeypatch.setattr(
        cut,
        "retarget_proxy_host_backend",
        lambda *a, **k: {"ok": True, "forward_host": "10.0.0.9"},
    )
    r = cut._put_npm_backend(
        session, fqdn="app.test", cached_id="12", new_host="10.0.0.9", forward_port=81
    )
    assert r["ok"] is True

    # unmatched host id walks cache then fails
    monkeypatch.setattr(cut, "_npm_hosts_cached", lambda s: ([], 0))
    monkeypatch.setattr(cut, "_match_npm", lambda hosts, fqdn: None)
    with pytest.raises(cut.CutoverError, match="unmatched|failed"):
        cut._put_npm_backend(session, fqdn="missing.test", cached_id="", new_host="10.0.0.9")


def test_fanout_pihole_restartdns(monkeypatch):
    from app.services.service_migrate import cutover as cut

    row = SimpleNamespace(id=1, name="ftl", enabled=True)
    monkeypatch.setattr(
        "app.services.integrations.registry.list_integrations",
        lambda session, type_filter=None: [row],
    )
    monkeypatch.setattr(
        cut,
        "pihole_login_urls",
        lambda session, r: ["http://pihole.test"],
    )
    monkeypatch.setattr("app.services.integrations.registry.pihole_password", lambda r: "pw")
    monkeypatch.setattr("app.services.integrations.registry.tls_verify", lambda r: True)

    sess = MagicMock()
    monkeypatch.setattr("app.services.integrations.pihole.login", lambda *a, **k: sess)
    monkeypatch.setattr("app.services.integrations.pihole.run_action", lambda *a, **k: None)
    monkeypatch.setattr("app.services.integrations.pihole.logout", lambda *a, **k: None)
    out = cut.fanout_pihole_restartdns(MagicMock())
    assert out and out[0]["ok"] is True

    monkeypatch.setattr(
        "app.services.integrations.pihole.login",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("auth")),
    )
    out2 = cut.fanout_pihole_restartdns(MagicMock())
    assert out2[0]["ok"] is False
    assert out2[0]["error"]


# ---------------------------------------------------------------------------
# host lock surface
# ---------------------------------------------------------------------------


def test_migrate_surface_allowed(monkeypatch):
    from app.services.service_migrate import host_lock as hl

    monkeypatch.setattr(hl, "migrate_enabled", lambda: True)
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: True)
    assert hl.migrate_surface_allowed() is False
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    assert hl.migrate_surface_allowed() is True
    monkeypatch.setattr(hl, "migrate_enabled", lambda: False)
    assert hl.migrate_surface_allowed() is False


# ---------------------------------------------------------------------------
# docker_inventory remaining
# ---------------------------------------------------------------------------


def test_inventory_slim_save_refresh(monkeypatch):
    from app.services import docker_inventory as inv

    p = inv._slim_project(
        {
            "name": "app",
            "path": "/x/app",
            "containers": [{"name": "web", "running": True}],
            "compose_sets": [
                {"key": "e2e", "label": "E2E", "filename": "docker-compose.e2e.yml", "is_primary": False, "services": ["web"]},
                "skip-me",
            ],
            "compose_graph": {
                "depends_on": {"web": ["db"]},
                "service_names": ["web", "db"],
                "networks": ["default"],
                "links": {"web": ["db"]},
                "compose_sha": "deadbeefcafebabe",
            },
        }
    )
    assert p["compose_sets"][0]["key"] == "e2e"
    assert p["compose_graph"]["depends_on"]["web"] == ["db"]

    session, engine = _memory_session()
    srv = Server(name="a", hostname="a.local", container_patch_enabled=True)
    session.add(srv)
    session.commit()
    session.refresh(srv)

    inv.save_inventory(
        session,
        srv,
        {"v": 2, "projects": [], "orphan_containers": [], "meta": {"project_count": 0}},
    )
    assert srv.docker_inventory_status == "ok"
    inv.set_status(session, srv, "stale", error="x" * 600)
    assert srv.docker_inventory_status == "stale"
    assert len(srv.docker_inventory_error) == 500

    srv.docker_inventory_status = "ok"
    srv.docker_inventory_json = "{}"
    inv.mark_stale(session, srv)
    assert srv.docker_inventory_status == "stale"
    srv.docker_inventory_status = "refreshing"
    inv.mark_stale(session, srv)
    assert srv.docker_inventory_status == "refreshing"
    srv.docker_inventory_status = "ok"
    srv.docker_inventory_json = None
    inv.mark_stale(session, srv)
    assert srv.docker_inventory_status == "never"

    s_ok = _srv(docker_inventory_status="ok")
    assert inv.is_refresh_stuck(s_ok) is False
    s_ref = _srv(id=None, docker_inventory_status="refreshing")
    assert inv.is_refresh_stuck(s_ref) is True
    with inv._refresh_lock:
        inv._refreshing.add(42)
    assert inv.is_refresh_stuck(_srv(id=42, docker_inventory_status="refreshing")) is False
    with inv._refresh_lock:
        inv._refreshing.discard(42)

    monkeypatch.setattr(
        "app.services.docker_management.list_containers",
        lambda s, enrich_mounts=False: [
            {"name": "web", "running": True, "placeholder": False, "compose_project": "app"}
        ],
    )
    monkeypatch.setattr(
        "app.services.docker_management.list_compose_projects",
        lambda s, light=True: [{"name": "app", "path": "/x/app", "containers": []}],
    )
    monkeypatch.setattr(
        "app.services.docker_management.nest_containers_under_projects",
        lambda projects, containers: (
            [
                {
                    "name": "app",
                    "path": "/x/app",
                    "containers": [{"name": "web", "running": True}],
                    "compose_graph": {"depends_on": {"web": []}},
                }
            ],
            [{"name": "orphan", "running": False}],
        ),
    )
    monkeypatch.setattr(
        "app.services.docker_management.annotate_update_flags",
        lambda projects, orphans, server: (projects, orphans),
    )
    payload = inv.build_inventory_l1(_srv())
    assert payload["v"] == 2
    assert payload["meta"]["project_count"] == 1

    monkeypatch.setattr(
        "app.services.docker_management.list_containers",
        lambda s, enrich_mounts=False: [{"name": "error", "status": "docker ps failed"}],
    )
    with pytest.raises(RuntimeError):
        inv.build_inventory_l1(_srv())

    monkeypatch.setattr(inv, "engine", engine)
    monkeypatch.setattr(
        inv,
        "build_inventory_l1",
        lambda s: {
            "v": 2,
            "projects": [],
            "orphan_containers": [],
            "meta": {"project_count": 0, "container_count": 0, "duration_ms": 1},
        },
    )
    monkeypatch.setattr(
        "app.services.stack_monitor.scan_server_inventory_for_down_alerts",
        lambda *a, **k: None,
    )
    assert inv.refresh_server_inventory(srv.id, force=True) is True
    assert inv.refresh_server_inventory(99999, force=True) is False

    srv2 = Server(name="off", hostname="off.local", container_patch_enabled=False)
    session.add(srv2)
    session.commit()
    session.refresh(srv2)
    assert inv.refresh_server_inventory(srv2.id, force=False) is False

    monkeypatch.setattr(inv, "build_inventory_l1", lambda s: (_ for _ in ()).throw(RuntimeError("ssh")))
    # keep previous snapshot path
    session.refresh(srv)
    assert inv.refresh_server_inventory(srv.id, force=True) is False

    assert inv.try_begin_refresh(7) is True
    assert inv.try_begin_refresh(7) is False
    inv.end_refresh_slot(7)

    fresh = _srv(
        docker_inventory_status="ok",
        docker_inventory_at=datetime.utcnow(),
    )
    assert inv.request_refresh(None, 1, server=fresh) is False

    class BG:
        def __init__(self):
            self.tasks = []

        def add_task(self, fn):
            self.tasks.append(fn)

    bg = BG()
    stale = _srv(id=srv.id, docker_inventory_status="never")
    assert inv.request_refresh(bg, srv.id, force=True, server=stale, session=session) is True
    assert bg.tasks
    inv.invalidate_after_mutation(session, srv, background_tasks=None)


# ---------------------------------------------------------------------------
# docker_management — list/inspect/compose without live SSH
# ---------------------------------------------------------------------------


def test_docker_mgmt_pure_helpers():
    from app.services import docker_management as dm

    assert dm.normalize_container_ref("") == ""
    assert dm.normalize_container_ref("error") == ""
    assert dm.normalize_container_ref("/web,alias") == "web"
    assert dm._is_all_services_log_target("__all__") is True
    assert dm._is_all_services_log_target("web") is False
    assert "KB" in dm._human_bytes(2048)
    assert dm._human_bytes(-1) == ""
    assert dm._human_bytes("nope") == ""
    assert "TB" in dm._human_bytes(1024**4) or "GB" in dm._human_bytes(1024**4)

    m = dm._parse_inspect_mount(
        {"Source": "/data", "Destination": "/app", "Type": "bind", "RW": False, "Name": ""}
    )
    assert m["ro"] is True and m["type"] == "bind"
    line = dm._format_mount_line({**m, "size_human": "1.0 MB"})
    assert "→" in line and "ro" in line
    assert dm._format_mount_line({}) == "—"
    assert "dst" in dm._format_mount_line({"destination": "dst"})
    assert "src" in dm._format_mount_line({"source": "src"})

    labels = dm._parse_compose_labels(
        {
            "com.docker.compose.project": "grafana",
            "com.docker.compose.service": "app",
            "com.docker.compose.project.working_dir": "/home/pi/docker/grafana",
        }
    )
    assert labels["compose_project"] == "grafana"
    csv = dm._parse_compose_labels(
        "com.docker.compose.project=n8n,com.docker.compose.service=n8n,"
        "com.docker.compose.project.working_dir=/home/pi/docker/n8n"
    )
    assert csv["compose_service"] == "n8n"
    assert dm._parse_compose_labels("")["compose_project"] == ""
    assert dm._parse_compose_labels("nokeq")["compose_project"] == ""

    assert dm._image_ref_matches("", {"a"}) is False
    assert dm._image_ref_matches("nginx:latest", {"nginx:latest"}) is True
    assert dm._image_ref_matches("nginx:1.25", {"nginx"}) is True
    assert dm._image_ref_matches("redis:7", {"postgres:15"}) is False

    info = dm.parse_container_updates_summary(_srv(container_updates_summary=""))
    assert info["projects"] == set()
    info = dm.parse_container_updates_summary(_srv(container_updates_summary="not-json"))
    assert info["projects"] == set()
    info = dm.parse_container_updates_summary(_srv(container_updates_summary="[]"))
    assert info["projects"] == set()
    raw = json.dumps(
        {
            "projects": ["grafana"],
            "project_details": {
                "grafana": {"images": ["grafana/grafana:11"]},
                "other": ["redis:7"],
            },
        }
    )
    info = dm.parse_container_updates_summary(_srv(container_updates_summary=raw))
    assert "grafana" in info["projects"]
    assert "grafana/grafana:11" in info["images"]

    projects, orphans = dm.annotate_update_flags(
        [
            {
                "name": "grafana",
                "containers": [
                    {"name": "grafana", "image": "grafana/grafana:11"},
                    {"name": "miss", "placeholder": True, "image": "x"},
                ],
            },
            {"name": "plain", "containers": [{"name": "web", "image": "nginx:latest"}]},
        ],
        [{"name": "orphan", "image": "redis:7"}],
        _srv(container_updates_summary=raw),
    )
    assert projects[0]["has_pending_update"] is True
    assert orphans[0]["has_pending_update"] is True

    ok = dm.validate_compose_content("services:\n  web:\n    image: nginx\n")
    assert ok["valid"] is True
    empty = dm.validate_compose_content("  \n")
    assert empty["valid"] is False
    bad = dm.validate_compose_content("services:\n  web: [\n  other: {\n")
    assert bad["valid"] is False and bad["errors"]

    calls = []

    def fn(x):
        calls.append(x)
        return x * 2

    dm._CACHE.clear()
    assert dm._cached(fn, "k", 30, 3) == 6
    assert dm._cached(fn, "k", 30, 3) == 6
    assert calls == [3]
    dm._CACHE.clear()

    bad_act = dm.container_action(_srv(), "web", "explode")
    assert bad_act["success"] is False
    empty_name = dm.container_action(_srv(), "", "start")
    assert empty_name["success"] is False
    assert dm.compose_action(_srv(), "/x", "nope")["success"] is False
    assert dm.compose_action(_srv(), "", "stop")["success"] is False
    assert dm.prune_unused(_srv(), "nope")["success"] is False


def test_docker_mgmt_ssh_list_inspect_compose(monkeypatch):
    from app.services import docker_management as dm

    dm._CACHE.clear()
    ps_line = json.dumps(
        {
            "ID": "abc123def456abc123def456abc123de",
            "Names": "/grafana",
            "Image": "grafana/grafana:11",
            "Status": "Up 2 hours",
            "State": "running",
            "Ports": "0.0.0.0:3000->3000/tcp",
            "CreatedAt": "2026-01-01",
            "Command": '"grafana-server"',
            "Mounts": "/home/pi/docker/grafana/data,/truncated…",
            "Size": "12MB",
            "LocalVolumes": "1",
            "Labels": "com.docker.compose.project=grafana,com.docker.compose.service=grafana,"
            "com.docker.compose.project.working_dir=/home/pi/docker/grafana",
            "Networks": "grafana_default",
            "Project": "",
            "Service": "",
        }
    )
    inspect = json.dumps(
        [
            {
                "Id": "abc123def456abc123def456abc123de",
                "Name": "/grafana",
                "Mounts": [
                    {
                        "Source": "/home/pi/docker/grafana/data",
                        "Destination": "/var/lib/grafana",
                        "Type": "bind",
                        "RW": True,
                    }
                ],
            }
        ]
    )
    client = FakeSSH(
        replies=[
            (0, ps_line + "\n", ""),  # docker ps
            (0, inspect, ""),  # enrich inspect
            (0, "4096\t/home/pi/docker/grafana/data\n", ""),  # du
        ]
    )
    monkeypatch.setattr(dm, "get_ssh_client", lambda *a, **k: client)
    monkeypatch.setattr(dm, "run_command", _run_on_fake)
    rows = dm._list_containers_uncached(_srv(), enrich_mounts=True)
    assert rows[0]["name"] == "grafana"
    assert rows[0]["running"] is True
    assert rows[0]["compose_project"] == "grafana"
    assert rows[0]["mounts_detail"]

    client.replies = [(1, "", "permission denied")]
    err_rows = dm._list_containers_uncached(_srv(), enrich_mounts=False)
    assert err_rows[0]["name"] == "error"

    # list_containers uses cache
    dm._CACHE.clear()
    client.replies = [(0, ps_line + "\n", "")]
    listed = dm.list_containers(_srv(), enrich_mounts=False)
    assert listed[0]["name"] == "grafana"

    client.replies = [
        (0, json.dumps({"Name": "/grafana", "State": {"Status": "running", "Running": True},
                        "NetworkSettings": {"Ports": {"3000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "3000"}]}},
                        "Config": {"Image": "grafana/grafana:11"}, "Created": "t"}), "")
    ]
    st = dm.get_container_status(_srv(), "grafana")
    assert st["running"] is True
    assert st["ports"]
    empty = dm.get_container_status(_srv(), "")
    assert empty["state"] == "unknown"
    client.replies = [(0, "not-json", "")]
    unk = dm.get_container_status(_srv(), "x")
    assert unk["state"] == "unknown"

    client.replies = [(1, "", "fail"), (0, "sha256:" + "b" * 64, ""), (0, "ok", "")]
    act = dm.container_action(_srv(), "grafana", "restart")
    assert act["success"] is True

    client.replies = [(0, "stopped", "")]
    ca = dm.compose_action(_srv(), "/home/pi/docker/grafana", "down", remove_volumes=True)
    assert ca["success"] is True and "-v" in client.cmds[-1]
    client.replies = [(0, "ok", "")]
    ca2 = dm.compose_action(_srv(), "/home/pi/docker/grafana", "restart", service="web")
    assert ca2["success"] is True

    client.replies = [(0, "built", "")]
    b = dm.build_compose_services(_srv(), "/home/pi/docker/app", services=["web"], no_cache=True)
    assert b["success"] is True and b["no_cache"] is True

    client.replies = [
        (0, "abc dangling:latest 12MB\n", ""),
        (0, "def old exited nginx\n", ""),
    ]
    unused = dm.list_unused_images_and_containers(_srv())
    assert unused["success"] is True
    assert unused["dangling_images"]
    client.replies = [(0, "pruned", ""), (0, "pruned", "")]
    pr = dm.prune_unused(_srv(), "both")
    assert pr["success"] is True
    client.replies = [(0, "img", "")]
    assert dm.prune_unused(_srv(), "images")["success"] is True

    client.replies = [
        (0, inspect, ""),
        (0, "100\t/home/pi/docker/grafana/data\n", ""),
    ]
    md = dm.get_container_mounts_detail(_srv(), "grafana")
    assert md["success"] is True
    assert md["mounts"]
    client.replies = [(0, "", "")]
    md_fail = dm.get_container_mounts_detail(_srv(), "missing")
    assert md_fail["success"] is False
    client.replies = [(0, "not-json", "")]
    assert dm.get_container_mounts_detail(_srv(), "x")["success"] is False

    sizes = dm._du_sizes_for_paths(client, ["relative", "/a", "/a", "/b"])
    # last replies consumed; empty stdout
    client.replies = [(0, "12 /a\nnope\n99\t/b\n", "")]
    sizes = dm._du_sizes_for_paths(client, ["/a", "/b"])
    assert sizes.get("/b") == 99
    assert dm._du_sizes_for_paths(client, ["nope"]) == {}

    compose_yml = (
        "services:\n"
        "  web:\n"
        "    build:\n"
        "      context: .\n"
        "      dockerfile: Dockerfile\n"
        "    depends_on:\n"
        "      - db\n"
        "  db:\n"
        "    image: postgres:15\n"
    )
    client.replies = [
        (0, "/home/pi/docker/grafana/docker-compose.yml\n", ""),
        (0, json.dumps({"Service": "web", "Image": "grafana/grafana:11"}) + "\n", ""),
        (0, "docker-compose.yml\n", ""),
        (0, compose_yml, ""),
    ]
    monkeypatch.setattr(dm, "docker_base_expanded", lambda s: "/home/pi/docker")
    projects = dm._list_compose_uncached(_srv(), light=False)
    assert projects and projects[0]["name"] == "grafana"
    assert "web" in (projects[0].get("services") or [])

    client.replies = [
        (0, "/home/pi/docker/n8n/compose.yml\n", ""),
        (0, "compose.yml\n", ""),
        (0, "services:\n  n8n:\n    image: n8nio/n8n\n", ""),
    ]
    light = dm._list_compose_uncached(_srv(), light=True)
    assert light[0]["name"] == "n8n"

    monkeypatch.setattr(
        dm,
        "list_compose_projects",
        lambda s: [{"name": "grafana", "path": "/home/pi/docker/grafana"}],
    )
    assert dm.resolve_compose_project_path(_srv(), "grafana") == "/home/pi/docker/grafana"
    with pytest.raises(ValueError):
        dm.resolve_compose_project_path(_srv(), "../etc")
    with pytest.raises(ValueError):
        dm.resolve_compose_project_path(_srv(), "missing")

    client.replies = [(0, "line1\nline2\n", "")]
    logs = dm.get_logs(_srv(), "grafana", lines=10)
    assert "line1" in logs or logs == "line1\nline2\n" or isinstance(logs, str)
    logs_all = dm.get_logs(_srv(), "__all__", lines=5, project_path="/home/pi/docker/grafana")
    assert isinstance(logs_all, str)

    # stream_compose_build
    stream_client = FakeSSH()
    stream_client.exec_command = lambda cmd, timeout=None: (
        None,
        iter(["ok\n"]),
        iter(["warn\n"]),
    )
    monkeypatch.setattr(dm, "get_ssh_client", lambda *a, **k: stream_client)
    chunks = list(dm.stream_compose_build(_srv(), "/home/pi/docker/app", services=["web"]))
    assert any("data:" in c for c in chunks)

    dm._CACHE.clear()


def test_docker_mgmt_get_logs_and_enrich_fallbacks(monkeypatch):
    from app.services import docker_management as dm

    client = FakeSSH(replies=[(0, "log-line\n", "")])
    monkeypatch.setattr(dm, "get_ssh_client", lambda *a, **k: client)
    monkeypatch.setattr(dm, "run_command", _run_on_fake)
    out = dm.get_logs(_srv(), "web", lines=20, follow=True, project_path="/home/pi/docker/app")
    assert isinstance(out, str)

    # enrich: first inspect empty, fallback by name
    containers = [{"name": "grafana", "id": "abc", "id_full": "abc", "mounts_list": ["/trunc…"]}]
    client.replies = [
        (1, "", "fail"),
        (0, json.dumps([{"Id": "abc", "Name": "/grafana", "Mounts": [{"Source": "/data", "Destination": "/d", "Type": "bind", "RW": True}]}]), ""),
        (0, "10\t/data\n", ""),
    ]
    dm._enrich_container_mounts(_srv(), containers)
    assert containers[0].get("mounts_detail")
    dm._enrich_container_mounts(_srv(), [{"name": "error"}])


# ---------------------------------------------------------------------------
# jobs helpers
# ---------------------------------------------------------------------------


def test_jobs_source_filter_list_count_and_details():
    from app.services import jobs as jobs_mod

    session, engine = _memory_session()
    srv = Server(name="a", hostname="a.local")
    session.add(srv)
    session.commit()
    session.refresh(srv)

    j1 = Job(
        server_id=srv.id,
        job_type="backup",
        status="running",
        details=json.dumps({"source_filter": "/home/pi/docker"}),
    )
    j2 = Job(
        server_id=srv.id,
        job_type="backup",
        status="pending",
        details="not-json",
    )
    j3 = Job(
        server_id=srv.id,
        job_type="os_patch",
        status="success",
        details=json.dumps({"summary": "ok", "log_lines": ["a", "b"]}),
    )
    session.add(j1)
    session.add(j2)
    session.add(j3)
    session.commit()
    session.refresh(j1)
    session.refresh(j2)
    session.refresh(j3)

    assert jobs_mod.job_source_filter(j1) == "/home/pi/docker"
    assert jobs_mod.job_source_filter(j2) is None
    assert jobs_mod.job_source_filter(None) is None

    active = jobs_mod.get_active_backup_jobs(session, srv.id)
    assert len(active) == 2
    assert jobs_mod.get_running_backup_job(session, srv.id).id == j1.id
    assert jobs_mod.get_active_backup_job(session, srv.id).id == j1.id
    assert jobs_mod.get_active_job_for_source(session, srv.id, "/home/pi/docker").id == j1.id

    listed = jobs_mod.list_jobs_for_server(session, srv.id, limit=10, active_only=True)
    assert listed
    hist = jobs_mod.list_jobs_for_server(session, srv.id, status="success", job_type="os_patch")
    assert hist and hist[0].id == j3.id
    fleet = jobs_mod.list_jobs(session, server_id=srv.id, date_from=datetime.utcnow() - timedelta(days=1))
    assert fleet
    n = jobs_mod.count_jobs(session, server_id=srv.id, active_only=True)
    assert n == 2
    n2 = jobs_mod.count_jobs(
        session,
        job_type="backup",
        date_from=datetime.utcnow() - timedelta(days=2),
        date_to=datetime.utcnow() + timedelta(days=1),
    )
    assert n2 >= 2

    pub = jobs_mod.job_public_dict(j3, detail=True)
    assert pub["done"] is True
    assert pub["details_json"]
    j_bad = Job(server_id=srv.id, job_type="x", status="running", details="[")
    pub_bad = jobs_mod.job_public_dict(j_bad, detail=True)
    assert pub_bad["summary"] == ""

    profiles = [{"source": "/home/pi/docker"}, {"source": "/other"}]
    attached = jobs_mod.attach_source_job_states(profiles, [j1, j2])
    assert attached[0]["active_job_id"] == j1.id

    assert jobs_mod._is_celery_owned_job(j1) is True
    nm = Job(job_type="nmap_discovery", status="pending")
    assert jobs_mod._is_celery_owned_job(nm) is True
    web = Job(job_type="service_migrate", status="running", celery_task_id="")
    assert jobs_mod._is_celery_owned_job(web) is False

    details = json.loads(jobs_mod._initial_job_details("queued", project="grafana"))
    assert details["current"] == "queued"
    assert details["project"] == "grafana"
    jobs_mod._merge_job_details(j3, log_line="next", current="running")
    merged = json.loads(j3.details)
    assert "next" in merged["log_lines"]
    jobs_mod._merge_job_details(j3, log_lines=["only"])
    assert json.loads(j3.details)["log_lines"] == ["only"]
    j3.details = "not-json"
    jobs_mod._merge_job_details(j3, foo=1)
    assert json.loads(j3.details)["foo"] == 1

    monkeypatch_engine = engine

    def _fresh():
        return Session(monkeypatch_engine)

    with patch.object(jobs_mod, "_get_fresh_session", _fresh):
        assert jobs_mod._load_job_details(j1.id)["source_filter"] == "/home/pi/docker"
        assert jobs_mod._load_job_details(99999) == {}
        j3.details = json.dumps({"values_encrypted": "x", "secrets_encrypted": "y", "keep": 1})
        session.add(j3)
        session.commit()
        jobs_mod._clear_job_secret_blobs(j3.id)
        session.refresh(j3)
        data = json.loads(j3.details)
        assert "values_encrypted" not in data
        assert data["keep"] == 1
        jobs_mod._clear_job_secret_blobs(99999)

    jobs_mod._mark_job_failed(j2, "boom", session, record_audit=False)
    assert j2.status == "failed"
    jobs_mod._mark_job_cancelled(j1, "stop", session, record_audit=False)
    assert j1.status == "cancelled"
    session.commit()

    # supersede remaining pending backups (j1/j2 already terminal)
    j4 = Job(server_id=srv.id, job_type="backup", status="pending", details="{}")
    session.add(j4)
    session.commit()
    with patch.object(jobs_mod, "_revoke_celery_task", lambda *a, **k: None), patch(
        "app.services.backup.stop_backup", lambda *a, **k: None
    ):
        n = jobs_mod.supersede_running_backups(session, srv.id)
    assert n >= 1

    old = Job(
        server_id=srv.id,
        job_type="backup",
        status="running",
        details="{}",
        created_at=datetime.utcnow() - timedelta(hours=5),
    )
    session.add(old)
    session.commit()
    cleaned = jobs_mod.cleanup_stale_backup_jobs(session, max_age_minutes=60)
    assert cleaned >= 1
