"""Ownership sidecar: record uid/gid/mode, strip setuid, jail restore paths."""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

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
    entries = collect_local_tree(root)
    by_path = {row["path"]: row for row in entries}
    assert by_path[""]["uid"] == os.getuid()
    assert by_path["data/app.db"]["mode"] == "0640"
    assert by_path["data/app.db"]["gid"] == os.getgid()
    dest = tmp_path / "backup"
    dest.mkdir()
    write_manifest(dest, entries)
    loaded = load_manifest(dest)
    assert loaded is not None
    assert loaded["entries"][0]["path"] == ""
    assert load_manifest(tmp_path / "empty") is None
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
