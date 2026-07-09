import json

from app.services.audit_format import format_audit_entry, is_noise_entry


def test_os_patch_noise_stuck_running():
    assert is_noise_entry(
        {
            "action": "os_patch",
            "status": "running",
            "details": "Job #1 started",
            "output_snippet": "",
        }
    )


def test_os_patch_summary_and_modal_tail():
    payload = {
        "server": "host",
        "steps": ["upgrade"],
        "results": [{"step": "upgrade", "rc": 0}],
        "needs_reboot": False,
        "summary": "upgrade ✓ · 0 ready after",
        "post_check": {"actionable_count": 0, "phased_count": 2, "reboot_pending": False},
        "log_tail": ["[upgrade] $ apt upgrade -y", "Done."],
    }
    out = format_audit_entry(
        {
            "action": "os_patch",
            "status": "success",
            "details": f"Job #9 · {payload['summary']}",
            "output_snippet": json.dumps(payload),
            "started_at": "2026-07-09T10:00:00",
            "finished_at": "2026-07-09T10:01:00",
        }
    )
    assert "upgrade" in out["summary"]
    assert "apt log" in out["modal_body"]
    assert "Done." in out["modal_body"]
    assert out["duration"]


def test_backup_running_is_noise():
    assert is_noise_entry({"action": "backup", "status": "running", "details": "", "output_snippet": ""})
