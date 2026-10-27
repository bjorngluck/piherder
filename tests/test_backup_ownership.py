"""Ownership sidecar: record uid/gid/mode, strip setuid, jail restore paths."""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

from app.services.backup_ownership import (
    apply_manifest_tree,
    canonical_mode,
    collect_local_tree,
    load_manifest,
    parse_find_output,
    render_apply_script,
    safe_rel,
    write_manifest,
)


def test_mode_strips_setuid_and_setgid_and_keeps_sticky():
    assert canonical_mode("4755", strip_special=True) == "0755"
    assert canonical_mode("2755", strip_special=True) == "0755"
    assert canonical_mode("6755", strip_special=True) == "0755"
    assert canonical_mode("1777", strip_special=True) == "1777"
    assert canonical_mode(0o644) == "0644"


def test_safe_rel_rejects_escape():
    assert safe_rel("") == ""
    assert safe_rel("data/app.db") == "data/app.db"
    assert safe_rel("../outside") is None
    assert safe_rel("/etc/passwd") is None
    assert safe_rel("foo/../../etc") is None
    assert safe_rel("foo//bar") is None


def test_collect_and_roundtrip_manifest(tmp_path: Path):
    root = tmp_path / "src"
    nested = root / "data"
    nested.mkdir(parents=True)
    db = nested / "app.db"
    db.write_text("x", encoding="utf-8")
    os.chmod(db, 0o640)
    link = root / "data" / "alias"
    link.symlink_to("app.db")
    entries = collect_local_tree(root)
    by_path = {row["path"]: row for row in entries}
    assert by_path[""]["uid"] == os.getuid()
    assert by_path[""]["kind"] == "dir"
    assert "bytes" not in by_path[""]
    assert by_path["data/app.db"]["mode"] == "0640"
    assert by_path["data/app.db"]["gid"] == os.getgid()
    assert by_path["data/app.db"]["kind"] == "file"
    assert by_path["data/app.db"]["bytes"] == 1
    assert by_path["data/alias"]["kind"] == "link"
    assert "bytes" not in by_path["data/alias"]
    dest = tmp_path / "backup"
    dest.mkdir()
    write_manifest(dest, entries)
    loaded = load_manifest(dest)
    assert loaded is not None
    assert loaded["entries"][0]["path"] == ""
    assert loaded["file_count"] == 1
    assert loaded["byte_count"] == 1
    write_manifest(dest, [{"path": "data/app.db", "uid": 1, "gid": 1, "mode": "0644"}])
    plain = load_manifest(dest)
    assert plain is not None
    assert "file_count" not in plain
    assert load_manifest(tmp_path / "empty") is None
    (dest / ".piherder-ownership.json").write_text(
        '{"schema":1,"entries":[],"file_count":true,"byte_count":1}',
        encoding="utf-8",
    )
    assert "not usable" in load_manifest(dest)["error"]
    (dest / ".piherder-ownership.json").write_text("{", encoding="utf-8")
    broken = load_manifest(dest)
    assert broken is not None and "unreadable" in broken["error"]


def test_parse_find_output_skips_parent_segments():
    blob = b"\x00" + b"0\x00" + b"0\x00" + b"755\x00"
    blob += b"data/file\x00" + b"1000\x00" + b"1000\x00" + b"640\x00"
    blob += b"../x\x00" + b"1\x00" + b"1\x00" + b"644\x00"
    rows = parse_find_output(blob)
    assert [row["path"] for row in rows] == ["", "data/file"]
    assert rows[1]["mode"] == "0640"
    assert rows[1]["uid"] == 1000


def test_apply_strips_special_bits_and_refuses_symlink_escape(tmp_path: Path):
    root = tmp_path / "restore"
    (root / "data").mkdir(parents=True)
    db = root / "data" / "app.db"
    db.write_text("x", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret"
    secret.write_text("nope", encoding="utf-8")
    os.chmod(secret, 0o600)
    (root / "escape").symlink_to(outside)
    uid, gid = os.getuid(), os.getgid()
    result = apply_manifest_tree(
        root,
        [
            {"path": "", "uid": uid, "gid": gid, "mode": "0755"},
            {"path": "data/app.db", "uid": uid, "gid": gid, "mode": "4755"},
            {"path": "escape/secret", "uid": uid, "gid": gid, "mode": "0777"},
            {"path": "../outside/secret", "uid": uid, "gid": gid, "mode": "0777"},
        ],
    )
    assert result["errors"] == []
    assert result["skipped"] == 2
    assert stat.S_IMODE(db.stat().st_mode) == 0o755
    assert stat.S_IMODE(secret.stat().st_mode) == 0o600


def test_host_script_jails_symlink_and_strips_setuid(tmp_path: Path):
    root = tmp_path / "restore"
    (root / "data").mkdir(parents=True)
    db = root / "data" / "app.db"
    db.write_text("x", encoding="utf-8")
    os.chmod(db, 0o644)
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret"
    secret.write_text("nope", encoding="utf-8")
    os.chmod(secret, 0o600)
    (root / "escape").symlink_to(outside, target_is_directory=True)
    uid, gid = os.getuid(), os.getgid()
    script = render_apply_script(
        [
            {"path": "data/app.db", "uid": uid, "gid": gid, "mode": "4755"},
            {"path": "escape/secret", "uid": 0, "gid": 0, "mode": "0777"},
        ],
        use_sudo=False,
    )
    assert "4755" not in script
    assert "0755" in script
    emitted = [line for line in script.splitlines() if line.startswith("apply_rel ")]
    assert emitted
    assert all(".." not in line for line in emitted)
    proc = subprocess.run(
        ["sh", "-s", "--", str(root)],
        input=script,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "applied 1" in proc.stdout
    assert stat.S_IMODE(db.stat().st_mode) == 0o755
    assert stat.S_IMODE(secret.stat().st_mode) == 0o600


def test_sudo_script_checks_paths_as_root():
    script = render_apply_script(
        [{"path": "data/app.db", "uid": 1, "gid": 1, "mode": "0640"}],
        use_sudo=True,
    )
    assert "sudo -n /usr/bin/test -e" in script
    assert "sudo -n /usr/bin/test -d" in script
    assert "sudo -n /usr/bin/chown" in script
    assert "sudo -n /usr/bin/true" in script
    assert "[ -e " not in script


def test_size_listing_counts_regular_files_and_rejects_junk():
    from app.services.backup_ownership import collect_remote_counts, parse_size_output

    assert parse_size_output(b"") == (0, 0)
    assert parse_size_output(b"4\x001\x00") == (2, 5)
    with pytest.raises(ValueError, match="truncated"):
        parse_size_output(b"app.db\x004\x00")

    class Sizes:
        def exec_command(self, cmd, timeout=None):
            if cmd.startswith("sudo"):
                return None, _Bytes(b"", 1), _Bytes(b"no sudo", 1)
            return None, _Bytes(b"4\x00", 0), _Bytes(b"", 0)

    from app.services.backup_ownership import remote_content_command

    assert collect_remote_counts(Sizes(), "/srv/app") == (1, 4)
    command = remote_content_command("/srv/my app")
    assert "-type f" in command
    assert "'/srv/my app'" in command


class _Bytes:
    def __init__(self, payload, rc):
        self.payload = payload
        self.rc = rc
        self.channel = self

    def read(self):
        return self.payload

    def recv_exit_status(self):
        return self.rc


def test_ownership_remote_command_is_one_shell_word():
    from app.services.backup_restore import ownership_remote_command

    assert ownership_remote_command("/data/my files") == "sh -s -- '/data/my files'"
    assert ownership_remote_command("/data/a;id") == "sh -s -- '/data/a;id'"
