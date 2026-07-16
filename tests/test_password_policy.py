"""Password policy and generator tests."""
from __future__ import annotations

from app.services.password_policy import (
    validate_password,
    password_strength,
    generate_password,
    format_invite_text,
    policy_rules_text,
    MIN_LENGTH,
    MAX_PASSWORD_BYTES,
)


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
