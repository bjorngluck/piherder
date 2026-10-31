"""Headroom above the 80% line gate. Straight-out copy, ownership edges, NIC interval."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlmodel import Session, select

from app.models import BackupDestination, Integration, NmapDevice, NmapScanSchedule, Server
from app.services import backup_direct
from app.services.backup_direct import (
    NO_DEST_MSG,
    _cleanup_remote,
    _push_unlocked,
    _stamp_direct_ownership,
    cpu_family,
    direct_where_label,
    herder_rclone_bin,
    load_direct_index,
    merge_directory,
    parse_direct_targets,
    path_is_direct,
    push_groups,
    push_server,
    rclone_bin_for_host,
    rels_for_push,
    remote_sync_command,
    run_direct_backup,
)
from app.services.backup_ownership import (
    apply_manifest_tree,
    canonical_mode,
    collect_local_tree,
    collect_remote_tree,
    load_manifest,
    manifest_path,
    parse_find_output,
    render_apply_script,
)
from app.services.nmap.nic_change import (
    nic_pairs,
    note_inserted_mac,
    replace_stale_mac,
    scan_interval,
)
from app.services.nmap.parse import ParsedHost
from app.services.nmap.upsert import upsert_hosts_from_parse
from tests.test_coverage_v17_q1 import _engine
from tests.test_nic_change import _pair


def _server(**kwargs) -> Server:
    data = dict(
        id=4,
        name="Host A",
        hostname="host-a.example.com",
        backup_enabled=True,
        backup_direct=True,
        backup_direct_targets='["drive"]',
        backup_paths='[{"source":"/srv/app","enabled":true}]',
        ssh_username="root",
        arch="aarch64",
    )
    data.update(kwargs)
    return Server(**data)


def _dest() -> BackupDestination:
    return BackupDestination(
        provider="drive",
        enabled=True,
        credentials_encrypted="tok",
        name="Drive",
        config_json='{"remote_dir":"PiHerder"}',
    )


class _Read:
    def __init__(self, data, rc: int = 0):
        self._data = data
        self.channel = SimpleNamespace(recv_exit_status=lambda: rc)

    def read(self):
        return self._data


class _Channel:
    def __init__(self, stderr: bytes, rc: int, fail: bool = False):
        self.stderr = stderr
        self.rc = rc
        self.fail = fail
        self.loops = 0
        self._sent = False

    def exit_status_ready(self):
        if self.fail and self.loops == 0:
            self.loops += 1
            raise RuntimeError("channel closed")
        self.loops += 1
        return self.loops > 1

    def recv_stderr_ready(self):
        if self._sent:
            return False
        self._sent = True
        return bool(self.stderr)

    def recv_ready(self):
        return False

    def recv_stderr(self, _size):
        return self.stderr

    def recv(self, _size):
        return b""

    def recv_exit_status(self):
        return self.rc


class _Remote:
    def __init__(self, channel: _Channel):
        self.channel = channel


class _SFTPFile:
    def __init__(self, store: dict, name: str):
        self.store = store
        self.name = name

    def write(self, data):
        self.store[self.name] = data

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _SFTP:
    def __init__(self, fail_open: bool = False):
        self.files: dict = {}
        self.puts: list = []
        self.modes: dict = {}
        self.fail_open = fail_open

    def put(self, src, dst):
        self.puts.append((src, dst))

    def chmod(self, path, mode):
        self.modes[path] = mode

    def open(self, path, _mode="w"):
        if self.fail_open:
            raise OSError("sekret refresh-1 obscured-pass ya29.abcdefghij")
        return _SFTPFile(self.files, path)

    def close(self):
        return None


class _Client:
    def __init__(self, *, find_rc: int = 0, sync_rc: int = 0, fail_open: bool = False, fail_channel: bool = False):
        self.sftp = _SFTP(fail_open=fail_open)
        self.commands: list[str] = []
        self.closed = False
        self.close_error = False
        self.find_rc = find_rc
        self.sync_rc = sync_rc
        self.fail_channel = fail_channel

    def open_sftp(self):
        return self.sftp

    def exec_command(self, cmd, timeout=None):
        self.commands.append(cmd)
        if "find " in cmd:
            if "%s\\0" in cmd:
                payload = b"3\x00" if self.find_rc == 0 else b""
            else:
                payload = b"app.db\x001000\x001000\x00644\x00" if self.find_rc == 0 else b""
            err = b"" if self.find_rc == 0 else b"find denied"
            return None, _Read(payload, self.find_rc), _Read(err, self.find_rc)
        channel = _Channel(b"sekret ya29.abcdefghij\n", self.sync_rc, fail=self.fail_channel)
        return None, _Remote(channel), _Remote(channel)

    def close(self):
        self.closed = True
        if self.close_error:
            raise OSError("close")


def _arm_binary(tmp_path, monkeypatch):
    monkeypatch.setattr(backup_direct, "BUNDLED_RCLONE_DIR", str(tmp_path))
    binary = tmp_path / "rclone-arm64"
    binary.write_bytes(b"arm")
    return binary


def _config(tmp_path, monkeypatch):
    sa = tmp_path / "sa.json"
    sa.write_text("{}\n", encoding="utf-8")
    cfg = tmp_path / "rclone.conf"
    cfg.write_text(
        "pass = obscured-pass\nservice_account_file = " + str(sa) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        backup_direct,
        "_open_destination_rclone",
        lambda _dest: (str(cfg), "sekret", "refresh-1", True),
    )
    monkeypatch.setattr(backup_direct.time, "sleep", lambda _seconds: None)
    clock = {"now": 1_000.0}

    def tick():
        clock["now"] += 40
        return clock["now"]

    monkeypatch.setattr(backup_direct.time, "time", tick)
    monkeypatch.setattr(
        "app.services.server_job_lock.refresh_server_lock",
        lambda *_args, **_kwargs: None,
    )
    return cfg


def _commands(script):
    seen = {"calls": []}

    def run(_client, command, timeout=30):
        seen["calls"].append(command)
        if "echo left" in command:
            return 0, script.get("probe", "gone"), ""
        if "copyto" in command:
            return script.get("copyto", (0, "", ""))
        if "--chown=root:root" in command:
            return script.get("install", (0, "", ""))
        if command.startswith("sudo -n ") and "version" in command:
            return script.get("sudo_version", (0, "rclone v1", ""))
        if "version" in command:
            return script.get("version", (0, "rclone v1", ""))
        if script.get("cleanup_boom") and command.startswith("rm -f"):
            raise OSError("cleanup")
        return 0, "", ""

    return seen, run


def _push(tmp_path, monkeypatch, client, *, user="root", exists=True, script=None, sources=None, rels=None):
    _arm_binary(tmp_path, monkeypatch)
    _config(tmp_path, monkeypatch)
    seen, run = _commands(script or {})
    monkeypatch.setattr(backup_direct, "get_ssh_client", lambda _server: client)
    monkeypatch.setattr(backup_direct, "run_command", run)
    monkeypatch.setattr(
        "app.services.backup._folder_exists_via_ssh",
        lambda _client, folder, _user: exists(folder) if callable(exists) else exists,
    )
    rows = _push_unlocked(
        _server(ssh_username=user),
        _dest(),
        rels or ["host-a.example.com/app"],
        [],
        sources=sources
        or [{"source": "/srv/app", "enabled": True, "dest_name": "app"}],
        lock_token="tok",
    )
    return rows, seen, client


def test_cpu_and_exclude_edges(tmp_path, monkeypatch):
    assert cpu_family("arm-custom-board") == "arm-custom-board"
    monkeypatch.setattr(backup_direct.shutil, "which", lambda _name: None)
    monkeypatch.setattr(backup_direct.os.path, "isfile", lambda _path: False)
    with pytest.raises(FileNotFoundError):
        herder_rclone_bin()
    monkeypatch.setattr(backup_direct.os.path, "isfile", lambda path: str(path).endswith("rclone"))
    monkeypatch.setattr(backup_direct, "BUNDLED_RCLONE_DIR", str(tmp_path / "missing"))
    monkeypatch.setattr(backup_direct.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(backup_direct, "herder_rclone_bin", lambda: "/usr/bin/rclone")
    assert rclone_bin_for_host("aarch64") == "/usr/bin/rclone"
    command = remote_sync_command(
        "/usr/bin/rclone",
        config_path="/tmp/ph.conf",
        source="/srv/app",
        remote_path="PiHerder/app",
        excludes=["../secret", "/abs", "", "logs", "notes.txt"],
        use_trash=False,
        use_sudo=False,
    )
    assert "--exclude notes.txt" in command
    assert "logs/**" in command
    assert "../secret" not in command
    assert parse_direct_targets("{") == []
    assert parse_direct_targets('{"drive": true}') == []
    assert direct_where_label("not-json", []) == "no destination chosen"
    assert "Drive" in direct_where_label('["drive","drive"]', [])


def test_direct_push_as_root_uploads_the_sidecar(tmp_path, monkeypatch):
    client = _Client()
    rows, seen, client = _push(tmp_path, monkeypatch, client)
    assert rows[0]["rc"] == 0
    assert rows[0]["ownership_entries"] == 1
    assert rows[0]["direct"] is True
    uploaded = next(
        raw
        for raw in client.sftp.files.values()
        if b'"schema"' in (raw if isinstance(raw, bytes) else str(raw).encode())
    )
    body = json.loads(uploaded)
    assert body["file_count"] == 1
    assert body["byte_count"] == 3
    assert any("copyto" in cmd for cmd in seen["calls"])
    assert any("--drive-use-trash" in cmd for cmd in client.commands)
    assert client.sftp.puts
    assert client.closed


def test_direct_push_as_backup_user_installs_rclone(tmp_path, monkeypatch):
    rows, seen, _client = _push(tmp_path, monkeypatch, _Client(), user="backup")
    assert rows[0]["rc"] == 0
    assert any("--chown=root:root" in cmd for cmd in seen["calls"])
    assert any(cmd.startswith("sudo -n ") and "version" in cmd for cmd in seen["calls"])


def test_direct_push_reports_start_and_sudo_failures(tmp_path, monkeypatch):
    rows, _seen, _client = _push(
        tmp_path,
        monkeypatch,
        _Client(),
        script={"version": (1, "", "permission denied")},
    )
    assert "did not start" in rows[0]["error"]

    rows, _seen, _client = _push(
        tmp_path,
        monkeypatch,
        _Client(),
        user="backup",
        script={"install": (1, "", "sudoers denied")},
    )
    assert "sudoers denied" in rows[0]["error"]

    rows, _seen, _client = _push(
        tmp_path,
        monkeypatch,
        _Client(),
        user="backup",
        script={"sudo_version": (1, "", "not allowed")},
    )
    assert "least-privilege" in rows[0]["error"]


def test_direct_push_skips_a_missing_folder_and_keeps_a_sync_error(tmp_path, monkeypatch):
    rows, _seen, _client = _push(
        tmp_path,
        monkeypatch,
        _Client(sync_rc=2),
        exists=lambda folder: folder.endswith("keep"),
        sources=[
            {"source": "/srv/missing", "enabled": True, "dest_name": "missing"},
            {"source": "/srv/keep", "enabled": True, "dest_name": "keep"},
        ],
        rels=["host-a.example.com/missing", "host-a.example.com/keep"],
    )
    assert rows[0]["skipped"] is True
    assert rows[0]["reason"] == "missing"
    assert rows[1]["rc"] == 2
    assert "error" in rows[1]


def test_direct_push_redacts_a_setup_error_and_survives_cleanup(tmp_path, monkeypatch):
    client = _Client(fail_open=True)
    client.close_error = True
    rows, _seen, client = _push(
        tmp_path,
        monkeypatch,
        client,
        script={"cleanup_boom": True},
    )
    text = rows[0]["error"]
    assert "sekret" not in text
    assert "refresh-1" not in text
    assert "obscured-pass" not in text
    assert "ya29" not in text
    assert "[redacted]" in text
    assert client.closed


def test_sidecar_failure_is_a_warning_and_a_left_config_is_blanked(tmp_path, monkeypatch):
    rows, seen, _client = _push(
        tmp_path,
        monkeypatch,
        _Client(),
        script={"copyto": (1, "", "upload no"), "probe": "left"},
    )
    assert rows[0]["rc"] == 0
    assert "Ownership manifest not written" in rows[0]["warning"]
    assert any("echo left" in cmd for cmd in seen["calls"])
    assert any("--chmod=600" in cmd for cmd in seen["calls"])

    warned = {"source": "/srv/app", "warning": "already noted"}
    client = _Client(find_rc=1)
    monkeypatch.setattr(backup_direct, "run_command", lambda *_a, **_k: (0, "", ""))
    _stamp_direct_ownership(
        client,
        warned,
        source="/srv/app",
        remote_path="PiHerder/app",
        binary="/usr/bin/rclone",
        config_path="/tmp/ph.conf",
        use_sudo=False,
    )
    assert "already noted" in warned["warning"]
    assert "Ownership manifest not written" in warned["warning"]


def test_cleanup_of_a_root_binary_and_push_server_lock_edges(monkeypatch):
    client = _Client()
    calls = []

    def run(_client, command, timeout=20):
        calls.append(command)
        if "echo left" in command:
            return 0, "gone", ""
        return 0, "", ""

    monkeypatch.setattr(backup_direct, "run_command", run)
    _cleanup_remote(
        client,
        user_bin="/tmp/piherder-rclone-abc",
        config_remote="/tmp/ph-rclone-abc.conf",
        sa_remote="/tmp/ph-rclone-abc.sa.json",
        empty_dir="/tmp/ph-empty-abc",
        sudo_installed=True,
    )
    assert any("--delete" in cmd for cmd in calls)

    dest = _dest()
    server = _server()
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: True)
    assert "Demo" in push_server(server, dest, ["host-a.example.com"], [])[0]["error"]
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    bare = _server(id=None)
    assert "missing an id" in push_server(bare, dest, ["host-a.example.com"], [])[0]["error"]
    monkeypatch.setattr(
        "app.services.server_job_lock.try_acquire_server_lock",
        lambda *_args, **_kwargs: None,
    )
    assert "Another backup" in push_server(server, dest, ["host-a.example.com"], [])[0]["error"]
    released = []
    monkeypatch.setattr(
        "app.services.server_job_lock.try_acquire_server_lock",
        lambda *_args, **_kwargs: "tok",
    )
    monkeypatch.setattr(
        "app.services.server_job_lock.release_server_lock",
        lambda *args, **_kwargs: released.append(args),
    )
    monkeypatch.setattr(
        backup_direct,
        "_push_unlocked",
        lambda *_args, **_kwargs: [{"rel": "app", "rc": 0}],
    )
    assert push_server(server, dest, ["host-a.example.com/app"], [])[0]["rc"] == 0
    assert released

    def grouped(host, _dest, _rels, _skipped, own_lock=True):
        if host.id == 1:
            return [{"rel": "app", "error": "nope"}]
        if host.id == 2:
            return [{"rel": "app", "skipped": True}]
        return [{"rel": "app", "rc": 0}]

    monkeypatch.setattr(backup_direct, "push_server", grouped)
    copied, errors = push_groups(
        dest,
        [(_server(id=1), ["a"]), (_server(id=2), ["b"]), (_server(id=3), ["c"])],
        [],
    )
    assert errors == ["app: nope"]
    assert copied == ["app"]


def test_run_direct_backup_policy_empty_and_send(tmp_path, monkeypatch):
    engine = _engine(tmp_path / "direct.db")
    monkeypatch.setattr("app.database.get_engine", lambda: engine)
    server = _server()
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: True)
    demo = run_direct_backup(server, job_id=9)
    assert demo["error"] == "Demo does not upload"

    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    monkeypatch.setattr(
        "app.services.backup_path_policy.filter_allowed_sources",
        lambda _sources, _rules: ([], []),
    )
    empty = run_direct_backup(server, sources_override=[])
    assert "No backup sources" in empty["error"]

    monkeypatch.setattr(
        "app.services.backup_path_policy.filter_allowed_sources",
        lambda _sources, _rules: ([], [{"source": "/srv/secret", "error": "outside"}]),
    )
    denied = run_direct_backup(
        server,
        sources_override=[{"source": "/srv/secret", "enabled": True}],
    )
    assert denied["results"][0]["skipped"] is True
    assert denied["error"] == NO_DEST_MSG

    with Session(engine) as session:
        session.add(_dest())
        session.commit()
    monkeypatch.setattr(
        "app.services.backup_path_policy.filter_allowed_sources",
        lambda sources, _rules: (list(sources), []),
    )
    sent = {}

    def push(_server, _dest, rels, _skipped, sources=None, own_lock=True):
        sent["own_lock"] = own_lock
        sent["rels"] = list(rels)
        return [{"source": "/srv/app", "rel": rels[0], "rc": 0}]

    monkeypatch.setattr(backup_direct, "push_server", push)
    sent_out = run_direct_backup(
        server,
        sources_override=[{"source": "/srv/app", "enabled": True, "dest_name": "app"}],
        job_id=4,
    )
    assert sent["own_lock"] is False
    assert sent_out["results"][0]["rc"] == 0
    assert "Drive" in sent_out["where"]


def test_direct_index_lists_the_host_and_a_failed_lookup_is_empty(tmp_path, monkeypatch):
    engine = _engine(tmp_path / "index.db")
    monkeypatch.setattr("app.database.get_engine", lambda: engine)
    with Session(engine) as session:
        session.add(_server(id=None, backup_folder_name="host-a"))
        session.add(
            Server(
                name="Host B",
                hostname="host-b.example.com",
                backup_enabled=False,
                backup_direct=True,
            )
        )
        session.commit()
    index = load_direct_index()
    assert list(index) == ["host-a"]
    assert index["host-a"].hostname == "host-a.example.com"

    def down():
        raise RuntimeError("down")

    monkeypatch.setattr("app.database.get_engine", down)
    assert load_direct_index() == {}

    monkeypatch.setattr("app.database.get_engine", lambda: engine)
    host = _server()
    host.backup_folder_name = "host-a"
    dest = _dest()
    dest.selection_json = json.dumps(
        {"checked": [], "skipped": ["host-a/app/logs", "other/skip"]}
    )
    rels, skipped = rels_for_push(dest, host)
    assert rels == ["host-a"]
    assert skipped == []

    with Session(engine) as session:
        assert path_is_direct(session, "host-a/app") is True
        assert path_is_direct(session, "") is False
        assert path_is_direct(session, "other/app") is False
        listed = merge_directory(session, "host-a", None)
        assert any(row["name"] == "app" and row["direct"] for row in listed)
        assert merge_directory(session, "host-a/app", None) == []
        assert merge_directory(session, "../x", None) is None
        assert merge_directory(session, "missing.example.com", None) is None
        root = merge_directory(
            session,
            "",
            [{"name": "host-a", "path": "host-a"}],
        )
        assert root[0]["direct"] is True


def test_ownership_listing_and_manifest_edges(tmp_path, monkeypatch):
    assert canonical_mode("nope") is None
    assert canonical_mode(0o10000) is None
    assert canonical_mode(-1) is None
    with pytest.raises(FileNotFoundError):
        collect_local_tree(tmp_path / "missing")
    root = tmp_path / "tree"
    nested = root / "sub"
    nested.mkdir(parents=True)
    (nested / "ok").write_text("x", encoding="utf-8")
    (nested / "a\nb").write_text("y", encoding="utf-8")
    found = collect_local_tree(root)
    assert any(row["path"] == "sub/ok" for row in found)
    assert all("\n" not in row["path"] for row in found)

    with pytest.raises(ValueError):
        parse_find_output(b"only\x001\x001")
    parsed = parse_find_output(
        b"../x\x001\x001\x00644\x00"
        b"bad\x001\x001\x00zzzz\x00"
        b"huge\x00-1\x001\x00644\x00"
        b"ok\x001\x001\x00644\x00"
    )
    assert [row["path"] for row in parsed] == ["ok"]

    class Denied:
        def exec_command(self, _cmd, timeout=None):
            return None, _Read(b"", 1), _Read(b"denied", 1)

    with pytest.raises(RuntimeError):
        collect_remote_tree(Denied(), "/srv/app")

    class Plain:
        def exec_command(self, cmd, timeout=None):
            if cmd.startswith("sudo"):
                return None, _Read("", 1), _Read("no sudo", 1)
            return None, _Read("app.db\x0010\x0010\x00640\x00", 0), _Read("", 0)

    listed = collect_remote_tree(Plain(), "/srv/app")
    assert listed[0]["mode"] == "0640"
    assert listed[0]["uid"] == 10

    archive = tmp_path / "archive"
    archive.mkdir()
    assert load_manifest(archive) is None
    manifest_path(archive).write_text("{", encoding="utf-8")
    assert "unreadable" in load_manifest(archive)["error"]
    manifest_path(archive).write_text('{"schema":9}', encoding="utf-8")
    assert "schema" in load_manifest(archive)["error"]
    manifest_path(archive).write_text('{"schema":1,"entries":{}}', encoding="utf-8")
    assert "entries" in load_manifest(archive)["error"]

    target = archive / "file"
    target.write_text("z", encoding="utf-8")
    skipped = apply_manifest_tree(
        archive,
        [
            "nope",
            {"path": "file", "uid": "nope", "gid": 1, "mode": "0644"},
            {"path": "../z", "uid": 1, "gid": 1, "mode": "0644"},
        ],
    )
    assert skipped["applied"] == 0
    assert skipped["skipped"] == 3

    def boom(*_args, **_kwargs):
        raise OSError(1, "denied")

    monkeypatch.setattr("app.services.backup_ownership.os.lchown", boom)
    failed = apply_manifest_tree(
        archive,
        [{"path": "file", "uid": 1, "gid": 1, "mode": "0644"}],
    )
    assert failed["errors"]
    assert failed["applied"] == 0

    script = render_apply_script(
        ["bad", {"path": "ok", "uid": "no", "gid": 1, "mode": "0644"}],
        use_sudo=False,
    )
    assert "apply_rel 'ok'" not in script


def test_scan_interval_and_replace_edges(tmp_path, monkeypatch):
    engine = _engine(tmp_path / "nic.db")
    with Session(engine) as session:
        integ = Integration(type="nmap", name="LAN", base_url="https://example.com")
        session.add(integ)
        session.commit()
        session.refresh(integ)
        assert scan_interval(session, int(integ.id)) == timedelta(hours=24)
        session.add(
            NmapScanSchedule(
                integration_id=int(integ.id),
                name="bad",
                intensity="discovery",
                interval_hours=0,
                cron="not-a-cron",
                enabled=True,
            )
        )
        session.add(
            NmapScanSchedule(
                integration_id=int(integ.id),
                name="six",
                intensity="discovery",
                interval_hours=6,
                enabled=True,
            )
        )
        session.add(
            NmapScanSchedule(
                integration_id=int(integ.id),
                name="hourly",
                intensity="discovery",
                interval_hours=0,
                cron="0 * * * *",
                enabled=True,
            )
        )
        session.add(
            NmapScanSchedule(
                integration_id=int(integ.id),
                name="off",
                intensity="discovery",
                interval_hours=1,
                enabled=False,
            )
        )
        session.commit()
        gap = scan_interval(session, int(integ.id))
        assert timedelta(minutes=50) < gap < timedelta(hours=2)

    with Session(engine) as session:
        integ, old = _pair(session, seen_ago=timedelta(hours=3), interval_hours=1)
        session.add(
            NmapDevice(
                integration_id=int(integ.id),
                identity_key="mac:blank",
                ip_address="192.0.2.10",
                hostname="camera.example.com",
                mac_address="  ",
                state="up",
                last_seen_at=datetime.utcnow(),
            )
        )
        session.commit()
        assert nic_pairs(session, int(integ.id)) == []
        upsert_hosts_from_parse(
            session,
            integration_id=int(integ.id),
            hosts=[
                ParsedHost(
                    ip_address="192.0.2.10",
                    hostname="camera.example.com",
                    mac_address="AA:BB:CC:DD:EE:02",
                    status="up",
                )
            ],
        )
        fresh = session.exec(
            select(NmapDevice).where(NmapDevice.mac_address == "AA:BB:CC:DD:EE:02")
        ).first()
        assert fresh is not None
        assert note_inserted_mac(session, int(old.id), int(integ.id)) == 0
        assert note_inserted_mac(session, int(fresh.id), int(integ.id)) == 1
        with pytest.raises(ValueError, match="missing"):
            replace_stale_mac(
                session,
                integration_id=int(integ.id),
                new_device_id=99999,
                stale_device_id=99998,
            )

        def explode(*_args, **_kwargs):
            raise RuntimeError("resolve down")

        monkeypatch.setattr(
            "app.services.notifications.resolve_by_fingerprint",
            explode,
        )
        replaced = replace_stale_mac(
            session,
            integration_id=int(integ.id),
            new_device_id=int(fresh.id),
            stale_device_id=int(old.id),
        )
        assert replaced["state"] == "ignored"
