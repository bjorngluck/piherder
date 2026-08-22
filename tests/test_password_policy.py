"""Password policy and generator tests."""
from __future__ import annotations

import pytest

from app.services import app_settings as cfg
from app.services.password_policy import (
    validate_password,
    password_strength,
    generate_password,
    format_invite_text,
    policy_rules_text,
    policy_missing,
    template_vars,
    clamp_policy,
    get_policy,
    policy_summary,
    settings_from_policy,
    MIN_LENGTH,
    FLOOR_MIN_LENGTH,
    MAX_PASSWORD_BYTES,
)


@pytest.fixture(autouse=True)
def _memory_settings(monkeypatch):
    """Keep policy reads off live Postgres."""
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


def test_reject_short():
    ok, err = validate_password("Ab1")
    assert not ok
    assert "at least" in err.lower() or str(MIN_LENGTH) in err


def test_reject_no_upper():
    ok, err = validate_password("abcdefghij1")
    assert not ok


def test_accept_good():
    ok, err = validate_password("GoodPass1x")
    assert ok, err


def test_generate_meets_policy():
    for _ in range(5):
        p = generate_password(16)
        ok, err = validate_password(p)
        assert ok, err
        assert len(p) >= MIN_LENGTH


def test_strength_increases():
    weak = password_strength("a")
    strong = password_strength(generate_password(18))
    assert strong["score"] >= weak["score"]
    assert strong["ok"]


def test_invite_text():
    t = format_invite_text(
        email="a@b.com",
        password="Secret1abc",
        role="operator",
        login_url="https://example/auth/login",
    )
    assert "a@b.com" in t
    assert "Secret1abc" in t
    assert "https://example/auth/login" in t


def test_reject_too_long_bytes():
    # 73 ASCII chars → 73 bytes
    ok, err = validate_password("A" * 71 + "b1")  # 73
    assert not ok
    assert "long" in err.lower() or "72" in err


def test_accept_max_ascii_length():
    # Exactly 72 Latin characters that meet policy
    base = "Aa1" + ("x" * 69)
    assert len(base) == 72
    ok, err = validate_password(base)
    assert ok, err


def test_reject_multibyte_over_byte_cap():
    # Many emoji can exceed 72 UTF-8 bytes while character count is lower
    # "Aa1" (3) + 24 emoji (often 4 bytes each = 96) 
    pwd = "Aa1" + ("😀" * 24)
    assert len(pwd.encode("utf-8")) > MAX_PASSWORD_BYTES
    ok, err = validate_password(pwd)
    assert not ok


def test_policy_rules_text_mentions_classes():
    t = policy_rules_text()
    assert "10" in t
    assert "uppercase" in t.lower()
    assert str(MAX_PASSWORD_BYTES) in t


def test_clamp_policy_floor_and_ceiling():
    p = clamp_policy({"password_min_length": 3, "password_max_length": 200})
    assert p["min_length"] == FLOOR_MIN_LENGTH
    assert p["max_length"] == MAX_PASSWORD_BYTES


def test_clamp_policy_max_not_below_min():
    p = clamp_policy({"password_min_length": 20, "password_max_length": 12})
    assert p["min_length"] == 20
    assert p["max_length"] == 20


def test_custom_policy_requires_special(_memory_settings):
    cfg.save_settings(settings_from_policy({"password_require_special": True}))
    ok, err = validate_password("GoodPass1x")
    assert not ok
    assert "special" in err.lower()
    ok2, err2 = validate_password("GoodPass1x!")
    assert ok2, err2


def test_custom_min_length_from_settings(_memory_settings):
    cfg.save_settings(settings_from_policy({"password_min_length": 14}))
    p = get_policy()
    assert p["min_length"] == 14
    ok, err = validate_password("GoodPass1x")  # 10 chars
    assert not ok
    assert "14" in err
    assert "14" in policy_rules_text()


def test_settings_roundtrip_keys(_memory_settings):
    cfg.save_settings(
        settings_from_policy(
            {
                "password_min_length": 12,
                "password_require_special": True,
                "password_require_upper": False,
            }
        )
    )
    loaded = cfg.load_settings()
    assert loaded["password_min_length"] == 12
    assert loaded["password_require_special"] is True
    assert loaded["password_require_upper"] is False
    assert "min=12" in policy_summary()


def test_policy_missing_hints_follow_settings(_memory_settings):
    cfg.save_settings(
        settings_from_policy(
            {"password_min_length": 12, "password_require_special": True}
        )
    )
    miss = policy_missing("Short1A")
    assert any("12" in m for m in miss)
    assert any("special" in m for m in miss)
    assert policy_missing("LongEnough1!") == []
    vars_ = template_vars()
    assert vars_["password_min_length"] == 12
    assert vars_["password_policy"]["require_special"] is True
    assert "12" in vars_["password_policy_text"]
    assert "special" in vars_["password_policy_text"].lower()
    st = password_strength("Short1A")
    assert st["ok"] is False
    assert st["missing"]


def test_defaults_match_legacy_constants():
    p = clamp_policy({})
    assert p["min_length"] == MIN_LENGTH
    assert p["max_length"] == MAX_PASSWORD_BYTES
    assert p["require_upper"] is True
    assert p["require_lower"] is True
    assert p["require_digit"] is True
    assert p["require_special"] is False
