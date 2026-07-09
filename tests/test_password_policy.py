"""Password policy and generator tests."""
from __future__ import annotations

from app.services.password_policy import (
    validate_password,
    password_strength,
    generate_password,
    format_invite_text,
    MIN_LENGTH,
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
