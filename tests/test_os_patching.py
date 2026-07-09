from app.services.os_patching import (
    normalize_os_patch_steps,
    summarize_os_patch_result,
    os_patch_succeeded,
    attach_audit_fields,
    init_os_patch_progress,
    _append_os_log,
    clear_os_patch_progress,
)


def test_normalize_default_steps():
    assert normalize_os_patch_steps(None) == ["update", "upgrade", "autoremove"]


def test_normalize_upgrade_xor_full_upgrade():
    steps = normalize_os_patch_steps(["update", "upgrade", "full-upgrade", "autoremove"])
    assert "upgrade" in steps
    assert "full-upgrade" not in steps
    assert steps == ["update", "upgrade", "autoremove"]


def test_normalize_full_upgrade_only():
    assert normalize_os_patch_steps(["full-upgrade"]) == ["full-upgrade"]


def test_normalize_unknown_filtered():
    assert normalize_os_patch_steps(["update", "hack", "autoremove"]) == ["update", "autoremove"]


def test_os_patch_succeeded():
    ok = {
        "results": [{"step": "update", "rc": 0}, {"step": "upgrade", "rc": 0}],
    }
    assert os_patch_succeeded(ok)
    assert not os_patch_succeeded({"results": [{"step": "upgrade", "rc": 1}]})
    assert not os_patch_succeeded({"error": "ssh fail", "results": []})
    assert not os_patch_succeeded({})


def test_summarize_os_patch_result():
    s = summarize_os_patch_result(
        {
            "results": [{"step": "upgrade", "rc": 0}, {"step": "autoremove", "rc": 0}],
            "needs_reboot": True,
        }
    )
    assert "upgrade" in s
    assert "reboot" in s.lower()


def test_attach_audit_fields_log_tail_and_post_check():
    host = "test-host-audit"
    clear_os_patch_progress(host)
    init_os_patch_progress(host, "start")
    _append_os_log(host, "[upgrade] Setting up foo")
    res = {
        "server": host,
        "results": [{"step": "upgrade", "rc": 0}],
        "summary": "upgrade ✓",
    }
    out = attach_audit_fields(
        res,
        host,
        post_check={"actionable_count": 0, "phased_count": 3, "reboot_pending": False},
    )
    assert out["log_tail"]
    assert any("Setting up foo" in ln for ln in out["log_tail"])
    assert out["post_check"]["phased_count"] == 3
    assert "0 ready after" in out["summary"]
    clear_os_patch_progress(host)
