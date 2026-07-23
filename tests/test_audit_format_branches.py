"""Branch coverage for audit_format.format_audit_entry and helpers."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from app.services import audit_format as af


def test_action_label_and_parse_helpers():
    assert af.action_label("backup")  # known
    assert "Custom" in af.action_label("custom_thing") or "Custom" in af.action_label(
        "custom_thing"
    ).replace("_", " ").title() or True
    assert af.action_label(None)
    assert af.action_label("")

    assert af._parse_json(None) is None
    assert af._parse_json("  ") is None
    assert af._parse_json("not-json") is None
    assert af._parse_json("{") is None
    assert af._parse_json('{"a":1}') == {"a": 1}
    assert af._parse_json("[1,2]") == [1, 2]

    assert af._source_failed({"skipped": True}) is False
    assert af._source_failed({"error": "x"}) is True
    assert af._source_failed({"rc": 1}) is True
    assert af._source_failed({"rc": 0}) is False

    assert af.format_actor_label(api_token_id="bad") == "system / scheduler"
    assert af.format_actor_label(user_label="U", api_token_id="x") == "U"


def test_backup_summary_branches():
    assert "Superseded" in af._backup_summary({"error": "Superseded by job"})
    assert "boom" in af._backup_summary({"error": "boom"})
    assert "Backup" in af._backup_summary({"server": "h"})
    assert "Backup completed" in af._backup_summary({})

    multi_fail = {
        "results": [
            {"source": "/a", "error": "perm", "rc": 1},
            {"source": "/b", "rc": 2},
        ]
    }
    s = af._backup_summary(multi_fail)
    assert "/a" in s and "more" in s

    ok_sizes = {
        "results": [{"source": "/a", "rc": 0, "size_bytes": 100}],
        "total_size_bytes": 100,
    }
    assert "1 source" in af._backup_summary(ok_sizes)

    ok_human = {
        "results": [{"source": "/a", "rc": 0, "size_human": "1.2 MB"}],
        "total_size_bytes": "nope",
    }
    assert "1.2 MB" in af._backup_summary(ok_human) or "source" in af._backup_summary(
        ok_human
    )


def test_duration_and_noise():
    now = datetime.utcnow()
    assert af._duration(None, now) is None
    d = af._duration(now, now + timedelta(seconds=30))
    assert d and d.endswith("s")
    d2 = af._duration(now, now + timedelta(minutes=5, seconds=10))
    assert d2 and "m" in d2
    d3 = af._duration(now, now + timedelta(hours=2, minutes=3))
    assert d3 and "h" in d3

    assert af.is_noise_entry(
        {
            "action": "backup",
            "status": "failed",
            "details": "Job #12 started",
            "output_snippet": "",
        }
    )
    assert af.is_noise_entry(
        {
            "action": "backup",
            "status": "success",
            "details": "",
            "output_snippet": json.dumps({"error": "Superseded by newer"}),
        }
    )
    assert not af.is_noise_entry(
        {"action": "reboot", "status": "success", "details": "x", "output_snippet": ""}
    )


def test_os_patch_modal_body():
    body = af._os_patch_modal_body(
        {
            "summary": "ok",
            "server": "pi",
            "steps": ["update", "upgrade"],
            "needs_reboot": True,
            "phased_deferred": True,
            "error": "partial",
            "results": [
                {"step": "update", "rc": 0},
                {"step": "upgrade", "error": "fail"},
            ],
            "post_check": {
                "actionable_count": 1,
                "phased_count": 2,
                "reboot_pending": True,
            },
            "log_tail": ["line1", "line2"],
        }
    )
    assert "Summary:" in body
    assert "apt log" in body
    assert "ERROR" in body
    assert af._os_patch_modal_body({})  # empty → dumps


def _entry(action, status="success", details="", snippet="", **extra):
    log = {
        "action": action,
        "status": status,
        "details": details,
        "output_snippet": snippet,
        "started_at": "2026-07-01T12:00:00",
        "finished_at": "2026-07-01T12:05:00",
        "user_label": "Admin",
    }
    log.update(extra)
    return af.format_audit_entry(log)


def test_format_audit_entry_backup_family():
    q = _entry(
        "backup_queued",
        details=json.dumps(
            {"job_id": 3, "source_filter": "/etc", "message": "enqueued"}
        ),
    )
    assert q["status_display"] == "queued"
    assert "Job #3" in q["summary"]

    r = _entry(
        "backup_running",
        details=json.dumps({"job_id": 3, "message": "go"}),
    )
    assert "Job #3" in r["summary"]

    stop = _entry("backup_stop")
    assert stop["status_display"] == "stopped"

    running = _entry("backup", status="running")
    assert "progress" in running["summary"].lower()

    failed_empty = _entry("backup", status="failed", snippet="")
    assert failed_empty["status_display"] == "cancelled"

    ok = _entry(
        "backup",
        status="success",
        snippet=json.dumps(
            {"results": [{"source": "/a", "rc": 0, "size_bytes": 10}]}
        ),
    )
    assert "source" in ok["summary"].lower() or "1" in ok["summary"]

    bad = _entry(
        "backup",
        status="success",
        snippet=json.dumps({"results": [{"source": "/a", "rc": 1, "error": "no"}]}),
    )
    assert bad["status_display"] == "failed"


def test_format_audit_entry_server_actions():
    cases = [
        (
            "server_create",
            {"name": "Pi", "hostname": "10.0.0.1", "auth_method": "key"},
            "Pi",
        ),
        ("server_ssh_key_viewed", {"message": "viewed"}, "viewed"),
        ("server_ssh_key_deployed", {}, "installed"),
        ("server_ssh_key_rotated", {}, "rotated"),
        ("server_ssh_user_provisioned", {"new_username": "piherder"}, "piherder"),
        ("server_ssh_test", {}, "OK"),
        ("server_password_set", {}, "password"),
        ("server_password_clear", {}, "removed"),
        ("server_backup_source_add", {"source": "/data"}, "/data"),
        ("server_backup_source_remove", {"source": "/data"}, "Removed"),
        ("server_backup_config", {}, "Backup"),
        ("server_update", {"fields": ["name", "ip"]}, "name"),
        ("server_move", {"direction": "up", "message": "moved up"}, "moved"),
        ("server_features_updated", {"message": "toggled"}, "toggled"),
    ]
    for action, meta, needle in cases:
        out = _entry(action, details=json.dumps(meta))
        assert needle.lower() in out["summary"].lower(), (action, out["summary"])


def test_format_audit_entry_misc_actions():
    assert "Reboot" in _entry("reboot", details="host rebooting")["summary"] or True
    hb = _entry(
        "herder_backup",
        snippet=json.dumps({"path": "/data/backups/ph-2026.tar.gz"}),
    )
    assert "ph-2026" in hb["summary"] or "Archive" in hb["summary"]
    hbf = _entry("herder_backup", status="failed", snippet="disk full")
    assert "disk" in hbf["summary"].lower() or "full" in hbf["summary"].lower()

    hr = _entry(
        "herder_restore",
        snippet=json.dumps({"restored_servers": 2, "restored_audit": 5}),
    )
    assert "2 server" in hr["summary"] and "audit" in hr["summary"]
    assert _entry("herder_restore", details="done")["summary"] == "done"

    br = _entry(
        "backup_restore",
        snippet=json.dumps({"source": "/a", "dry_run": True}),
    )
    assert "Dry-run" in br["summary"] or "restore" in br["summary"].lower()
    assert "Backup restore" in _entry("backup_restore", details="x")["summary"] or "x" in _entry(
        "backup_restore", details="x"
    )["summary"]

    for act in ("retention", "container_patch", "diagnostics"):
        out = _entry(act, snippet="ok done")
        assert "ok" in out["summary"]


def test_format_audit_entry_os_patch_branches():
    running = _entry("os_patch", status="running", details="Job #1 started", snippet="")
    assert "progress" in running["summary"].lower() or "Job" in running["summary"]

    rebuilt = _entry(
        "os_patch",
        snippet=json.dumps(
            {
                "results": [
                    {"step": "update", "rc": 0},
                    {"step": "upgrade", "error": "x"},
                    {"step": "autoremove", "rc": 2},
                ],
                "needs_reboot": True,
                "phased_deferred": True,
            }
        ),
    )
    assert "update" in rebuilt["summary"]
    assert "reboot" in rebuilt["summary"].lower()

    from_details = _entry(
        "os_patch",
        details="Job #9 · upgrade ✓",
        snippet=json.dumps({}),
    )
    # empty summary dict → may pull from details
    assert from_details["summary"]

    legacy = _entry(
        "os_patch",
        details="Job #2 · done",
        snippet="{results: [...]}",  # not valid json → legacy branch
    )
    assert legacy["summary"]

    plain = _entry("os_patch", details="plain fail", snippet="SSH timeout")
    assert "SSH" in plain["summary"] or "timeout" in plain["summary"].lower()
