"""v1.2 Stream W — SSH console tickets / limits (no live SSH)."""
from __future__ import annotations

import pytest

from app.services import ssh_console as cons


@pytest.fixture(autouse=True)
def _reset():
    cons.reset_runtime_state_for_tests()
    yield
    cons.reset_runtime_state_for_tests()


def test_console_disabled_by_default(monkeypatch):
    monkeypatch.setattr(cons.settings, "PIHERDER_SSH_CONSOLE", False)
    assert cons.console_enabled() is False
    with pytest.raises(cons.ConsoleDisabled):
        cons.require_enabled()


def test_mint_and_consume_ticket(monkeypatch):
    monkeypatch.setattr(cons.settings, "PIHERDER_SSH_CONSOLE", True)
    monkeypatch.setattr(cons.settings, "PIHERDER_SSH_CONSOLE_TICKET_SEC", 60)
    tok = cons.mint_ticket(user_id=3, server_id=9)
    assert tok
    payload = cons.consume_ticket(tok, user_id=3, server_id=9)
    assert payload.get("console") is True
    assert int(payload["sid"]) == 9
    # single use
    with pytest.raises(cons.ConsoleDenied, match="already used"):
        cons.consume_ticket(tok, user_id=3, server_id=9)


def test_consume_wrong_user(monkeypatch):
    monkeypatch.setattr(cons.settings, "PIHERDER_SSH_CONSOLE", True)
    tok = cons.mint_ticket(user_id=1, server_id=2)
    with pytest.raises(cons.ConsoleDenied, match="does not match"):
        cons.consume_ticket(tok, user_id=99, server_id=2)


def test_slot_limits(monkeypatch):
    monkeypatch.setattr(cons.settings, "PIHERDER_SSH_CONSOLE", True)
    monkeypatch.setattr(cons.settings, "PIHERDER_SSH_CONSOLE_MAX_PER_USER", 1)
    monkeypatch.setattr(cons.settings, "PIHERDER_SSH_CONSOLE_MAX_GLOBAL", 2)
    cons.try_acquire_slot(7)
    with pytest.raises(cons.ConsoleDenied, match="your account"):
        cons.try_acquire_slot(7)
    cons.try_acquire_slot(8)
    with pytest.raises(cons.ConsoleDenied, match="instance"):
        cons.try_acquire_slot(9)
    cons.release_slot(7)
    cons.try_acquire_slot(9)  # ok after release
    cons.release_slot(8)
    cons.release_slot(9)


def test_audit_labels():
    from app.services import audit_format as af

    assert "console" in af.action_label("ssh_console_open").lower()
    assert af.action_label("ssh_console_close")
