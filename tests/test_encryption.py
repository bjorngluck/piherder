import pytest
from cryptography.fernet import Fernet, InvalidToken

from app.security import encryption as enc
from app.security.encryption import encrypt_str, decrypt_str, generate_master_key


def test_generate_master_key_is_valid_fernet():
    key = generate_master_key()
    Fernet(key.encode())  # must not raise


def test_encrypt_decrypt_roundtrip():
    plain = "-----BEGIN OPENSSH PRIVATE KEY-----\ntest-secret\n-----END-----"
    token = encrypt_str(plain)
    assert token
    assert token != plain
    assert decrypt_str(token) == plain


def test_empty_encrypt():
    assert encrypt_str("") == ""
    assert decrypt_str("") == ""


def test_invalid_master_key_raises(monkeypatch):
    monkeypatch.setattr(enc.settings, "PIHERDER_MASTER_KEY", "not-a-valid-fernet-key")
    with pytest.raises(RuntimeError, match="Invalid PIHERDER_MASTER_KEY"):
        enc.encrypt_str("secret")


def test_missing_master_key_raises(monkeypatch):
    monkeypatch.setattr(enc.settings, "PIHERDER_MASTER_KEY", "")
    with pytest.raises(RuntimeError, match="required"):
        enc.encrypt_str("secret")


def test_tampered_ciphertext_raises():
    token = encrypt_str("hello-world")
    with pytest.raises(InvalidToken):
        decrypt_str(token[:-4] + "XXXX")
