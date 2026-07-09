from cryptography.fernet import Fernet

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
