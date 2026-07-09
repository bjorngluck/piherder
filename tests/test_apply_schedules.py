"""Unit tests for patch apply schedule helpers and RBAC roles."""
from __future__ import annotations

from app.services.jobs import _parse_os_apply_steps
from app.services.os_patching import normalize_os_patch_steps
from app.security.auth import normalize_role, ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER, VALID_ROLES


def test_parse_os_apply_steps_default():
    assert _parse_os_apply_steps(None) == ["update", "upgrade", "autoremove"]
    assert _parse_os_apply_steps("") == ["update", "upgrade", "autoremove"]


def test_parse_os_apply_steps_json():
    steps = _parse_os_apply_steps('["update", "full-upgrade", "autoremove"]')
    assert steps == ["update", "full-upgrade", "autoremove"]


def test_parse_os_apply_steps_xor_upgrade():
    steps = _parse_os_apply_steps('["update", "upgrade", "full-upgrade"]')
    assert "upgrade" in steps
    assert "full-upgrade" not in steps


def test_normalize_os_patch_steps_order():
    assert normalize_os_patch_steps(["autoremove", "update", "upgrade"]) == [
        "update",
        "upgrade",
        "autoremove",
    ]


def test_normalize_role():
    assert normalize_role(None) == ROLE_ADMIN
    assert normalize_role("viewer") == ROLE_VIEWER
    assert normalize_role("OPERATOR") == ROLE_OPERATOR
    assert normalize_role("nope") == ROLE_ADMIN
    assert VALID_ROLES == {ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER}


def test_job_type_labels():
    from app.services.jobs import job_type_label, JOB_TYPE_LABELS

    assert job_type_label("os_patch") == "OS patch"
    assert job_type_label("unknown_thing") == "Unknown Thing"
    assert "backup" in JOB_TYPE_LABELS
