"""
Encryption at rest for secrets (SSH keys, passwords).

Uses Fernet (symmetric) + PIHERDER_MASTER_KEY from environment.
Private keys are NEVER returned to the browser after initial generation/upload.
"""
from cryptography.fernet import Fernet
from ..config import settings
import base64


def _get_fernet() -> Fernet:
    key = settings.PIHERDER_MASTER_KEY
    if not key:
        raise RuntimeError("PIHERDER_MASTER_KEY is required for encryption")

    # Accept either raw base64 or ensure it's valid
    try:
        # Fernet keys must be 32 bytes url-safe base64
        if isinstance(key, str):
            key = key.encode()
        f = Fernet(key)
        return f
    except Exception as e:
        raise RuntimeError(f"Invalid PIHERDER_MASTER_KEY (must be 32-byte base64 Fernet key): {e}")


def encrypt_str(plaintext: str) -> str:
    """Return Fernet ciphertext (str) for storage in DB."""
    if not plaintext:
        return ""
    f = _get_fernet()
    token = f.encrypt(plaintext.encode("utf-8"))
    return token.decode("utf-8")


def decrypt_str(ciphertext: str) -> str:
    """Decrypt to plaintext. Only call in job execution context (in memory only)."""
    if not ciphertext:
        return ""
    f = _get_fernet()
    return f.decrypt(ciphertext.encode("utf-8")).decode("utf-8")


def generate_master_key() -> str:
    """Helper for .env generation."""
    return Fernet.generate_key().decode()
