"""v1.3 slice 2 Deep — Settings-backed console timeouts / concurrency."""
from __future__ import annotations

import pytest

from app.services import app_settings as cfg
from app.services import ssh_console as cons
from app.services.audit_format import action_label


@pytest.fixture(autouse=True)
def _memory_settings(monkeypatch):
    store: dict = {}

    def fake_load():
        return dict(store)

    def fake_write(data: dict):
        store.clear()
        store.update(data)

    monkeypatch.setattr(cfg, "_load_raw_from_db", fake_load)
    monkeypatch.setattr(cfg, "_write_raw_to_db", fake_write)
    cfg.clear_cache()
    yield store
    cfg.clear_cache()


def test_defaults_match_v12():
    assert cons.idle_sec() == 900
    assert cons.max_session_sec() == 3600
    assert cons.max_per_user() == 4
    assert cons.max_global() == 20
    assert cons.ticket_ttl_sec() == 60
    assert cons.hold_sec() == 0
    assert cons.revalidate_sec() == 10
    assert cons.default_scrollback() == 2000
    assert cons.bind_ip_enabled() is True
    assert cons.bind_device_enabled() is True


def test_settings_used_when_env_unset(_memory_settings):
    cfg.save_settings(
        {
            "console_idle_sec": 1800,
            "console_max_sec": 7200,
            "console_max_per_user": 6,
            "console_max_global": 24,
        }
    )
    assert cons.idle_sec() == 1800
    assert cons.max_session_sec() == 7200
    assert cons.max_per_user() == 6
    assert cons.max_global() == 24


def test_env_wins_over_settings(monkeypatch, _memory_settings):
    cfg.save_settings({"console_idle_sec": 1800, "console_max_per_user": 8})
    monkeypatch.setenv("PIHERDER_SSH_CONSOLE_IDLE_SEC", "600")
    monkeypatch.setenv("PIHERDER_SSH_CONSOLE_MAX_PER_USER", "2")
    monkeypatch.setattr(cons.settings, "PIHERDER_SSH_CONSOLE_IDLE_SEC", 600)
    monkeypatch.setattr(cons.settings, "PIHERDER_SSH_CONSOLE_MAX_PER_USER", 2)
    assert cons.idle_sec() == 600
    assert cons.max_per_user() == 2


def test_blank_env_does_not_lock(monkeypatch, _memory_settings):
    monkeypatch.setenv("PIHERDER_SSH_CONSOLE_IDLE_SEC", "  ")
    cfg.save_settings({"console_idle_sec": 1200})
    assert cons.env_wins("PIHERDER_SSH_CONSOLE_IDLE_SEC") is False
    assert cons.idle_sec() == 1200


def test_clamp_floors_and_ceilings():
    p = cons.clamp_console_policy(
        {
            "console_idle_sec": 10,
            "console_max_sec": 999999,
            "console_max_per_user": 0,
            "console_max_global": 200,
            "console_ticket_sec": 1,
            "console_revalidate_sec": 1,
            "console_scrollback": 10,
        }
    )
    assert p["console_idle_sec"] == cons.IDLE_SEC_MIN
    assert p["console_max_sec"] == cons.MAX_SEC_MAX
    assert p["console_max_per_user"] == cons.PER_USER_MIN
    assert p["console_max_global"] == cons.GLOBAL_MAX
    assert p["console_ticket_sec"] == cons.TICKET_SEC_MIN
    assert p["console_revalidate_sec"] == cons.REVALIDATE_SEC_MIN
    assert p["console_scrollback"] == cons.SCROLLBACK_MIN


def test_max_session_at_least_idle():
    p = cons.clamp_console_policy({"console_idle_sec": 3600, "console_max_sec": 120})
    assert p["console_max_sec"] == 3600
    cfg.save_settings({"console_idle_sec": 2000, "console_max_sec": 200})
    assert cons.max_session_sec() >= cons.idle_sec()


def test_global_at_least_per_user():
    p = cons.clamp_console_policy(
        {"console_max_per_user": 8, "console_max_global": 2}
    )
    assert p["console_max_global"] == 8


def test_hold_zero_or_at_least_30():
    assert cons.clamp_console_policy({"console_hold_sec": 0})["console_hold_sec"] == 0
    assert cons.clamp_console_policy({"console_hold_sec": 29})["console_hold_sec"] == 30
    assert cons.clamp_console_policy({"console_hold_sec": 9000})["console_hold_sec"] == 3600


def test_lowering_cap_does_not_evict(monkeypatch, _memory_settings):
    monkeypatch.setenv("PIHERDER_SSH_CONSOLE_MAX_PER_USER", "2")
    monkeypatch.setenv("PIHERDER_SSH_CONSOLE_MAX_GLOBAL", "10")
    monkeypatch.setattr(cons.settings, "PIHERDER_SSH_CONSOLE_MAX_PER_USER", 2)
    monkeypatch.setattr(cons.settings, "PIHERDER_SSH_CONSOLE_MAX_GLOBAL", 10)
    cons.reset_runtime_state_for_tests()
    cons.try_acquire_slot(3)
    cons.try_acquire_slot(3)
    monkeypatch.setenv("PIHERDER_SSH_CONSOLE_MAX_PER_USER", "1")
    monkeypatch.setattr(cons.settings, "PIHERDER_SSH_CONSOLE_MAX_PER_USER", 1)
    g, by_u = cons.live_counts()
    assert by_u[3] == 2
    assert g == 2
    with pytest.raises(cons.ConsoleDenied):
        cons.try_acquire_slot(3)


def test_audit_label():
    assert "console" in action_label("console_policy_changed").lower()


def test_env_lock_omits_key_from_save(monkeypatch, _memory_settings):
    monkeypatch.setenv("PIHERDER_SSH_CONSOLE_IDLE_SEC", "600")
    monkeypatch.setattr(cons.settings, "PIHERDER_SSH_CONSOLE_IDLE_SEC", 600)
    locks = cons.console_env_locks()
    assert locks["console_idle_sec"] is True
    posted = cons.clamp_console_policy({"console_idle_sec": 1800})
    to_save = {k: v for k, v in posted.items() if not locks.get(k)}
    assert "console_idle_sec" not in to_save


def test_policy_summary_stable():
    s = cons.console_policy_summary(cons.clamp_console_policy({}))
    assert "idle=900" in s
    assert "bind_ip=1" in s
    assert "priv=admin" in s


def test_privileged_role_clamp_and_default():
    assert cons.clamp_console_policy({})["console_privileged_role"] == "admin"
    assert cons.clamp_console_policy({"console_privileged_role": "operator"})[
        "console_privileged_role"
    ] == "operator"
    assert cons.clamp_console_policy({"console_privileged_role": "nope"})[
        "console_privileged_role"
    ] == "admin"
