"""Session JWT encode/decode via PyJWT (HS256) — no python-jose/ecdsa."""
from __future__ import annotations

from datetime import timedelta

import jwt
import pytest
from jwt.exceptions import InvalidTokenError

from app.config import settings
from app.security.auth import (
    create_access_token,
    create_pending_2fa_token,
    create_secrets_unlock_token,
    decode_token_payload,
)


def test_access_token_roundtrip():
    token = create_access_token({"sub": "42"})
    assert isinstance(token, str)
    assert token.count(".") == 2  # header.payload.sig
    payload = decode_token_payload(token)
    assert payload is not None
    assert payload["sub"] == "42"
    assert "exp" in payload


def test_pending_2fa_claim():
    token = create_pending_2fa_token(7)
    payload = decode_token_payload(token)
    assert payload is not None
    assert payload["sub"] == "7"
    assert payload.get("2fa_pending") is True


def test_secrets_unlock_claim():
    token = create_secrets_unlock_token(3)
    payload = decode_token_payload(token)
    assert payload is not None
    assert payload["sub"] == "3"
    assert payload.get("secrets_unlock") is True


def test_expired_token_rejected():
    token = create_access_token(
        {"sub": "1"},
        expires_delta=timedelta(seconds=-10),
    )
    assert decode_token_payload(token) is None


def test_tampered_token_rejected():
    token = create_access_token({"sub": "1"})
    parts = token.split(".")
    # Flip last char of signature
    sig = parts[2]
    flipped = ("A" if not sig.endswith("A") else "B") + sig[1:]
    bad = ".".join([parts[0], parts[1], flipped])
    assert decode_token_payload(bad) is None


def test_wrong_secret_rejected():
    token = create_access_token({"sub": "1"})
    with pytest.raises(InvalidTokenError):
        jwt.decode(token, "not-the-secret", algorithms=[settings.ALGORITHM])


def test_algorithm_is_hs256():
    assert settings.ALGORITHM == "HS256"
    token = create_access_token({"sub": "1"})
    header = jwt.get_unverified_header(token)
    assert header.get("alg") == "HS256"
