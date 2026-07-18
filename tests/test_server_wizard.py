"""Unit tests for add-host wizard helpers (no live HTTP)."""
from __future__ import annotations

from types import SimpleNamespace

from app.routers import server_wizard as wiz


def test_wizard_steps_order():
    assert wiz.STEP_KEYS[0] == "identity"
    assert wiz.STEP_KEYS[-1] == "done"
    assert len(wiz.STEP_KEYS) == 8


def test_step_index_and_nav():
    assert wiz.step_index("identity") == 0
    assert wiz.step_index("done") == 7
    assert wiz.step_index("nope") == 0
    assert wiz.next_step_key("identity") == "trust"
    assert wiz.next_step_key("done") is None
    assert wiz.prev_step_key("trust") == "identity"
    assert wiz.prev_step_key("identity") is None


def test_wizard_path_query():
    assert wiz.wizard_path("identity") == "/servers/new?step=identity"
    assert wiz.wizard_path("connect", 42) == "/servers/new?step=connect&server_id=42"
    assert wiz.wizard_path("bogus") == "/servers/new?step=identity"


def test_infer_resume_step():
    bare = SimpleNamespace(
        ssh_private_key_encrypted=None,
        ssh_password_encrypted=None,
    )
    assert wiz.infer_resume_step(bare) == "trust"
    keyed = SimpleNamespace(
        ssh_private_key_encrypted="enc",
        ssh_password_encrypted=None,
    )
    assert wiz.infer_resume_step(keyed) == "connect"
    pw_only = SimpleNamespace(
        ssh_private_key_encrypted=None,
        ssh_password_encrypted="enc",
    )
    assert wiz.infer_resume_step(pw_only) == "connect"
