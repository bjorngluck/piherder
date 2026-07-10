import json

from app.services.audit_format import format_actor_label, format_audit_entry, is_noise_entry


def test_format_actor_label_user_session():
    assert format_actor_label(user_label="Alice (a@x.com)") == "Alice (a@x.com)"
    assert format_actor_label() == "system / scheduler"


def test_format_actor_label_api_token():
    assert format_actor_label(api_token_id=3, api_token_name="n8n") == "API token: n8n (#3)"
    assert (
        format_actor_label(
            user_label="Admin (admin@x.com)",
            api_token_id=3,
            api_token_name="n8n",
        )
        == "Admin (admin@x.com) · API token: n8n (#3)"
    )


def test_format_audit_entry_includes_actor_label():
    out = format_audit_entry(
        {
            "action": "backup_request",
            "status": "success",
            "details": '{"job_id": 1, "message": "ok"}',
            "user_label": "Admin",
            "api_token_id": 7,
            "api_token_name": "ha",
            "started_at": "2026-07-10T10:00:00",
        }
    )
    assert "API token: ha (#7)" in out["actor_label"]
    assert "Admin" in out["actor_label"]


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
