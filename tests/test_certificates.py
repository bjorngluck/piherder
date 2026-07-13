"""Managed certificate helpers (parse, fingerprint, combined PEM)."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.services import certificates as cert_svc


def _make_self_signed_pem(cn: str = "test.example.com", days: int = 30) -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = datetime.utcnow()
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=days))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(cn), x509.DNSName(f"*.{cn}")]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    fullchain = cert.public_bytes(serialization.Encoding.PEM).decode()
    priv = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    return fullchain, priv


def test_parse_pem_metadata():
    full, key = _make_self_signed_pem("app.example.com", days=40)
    meta = cert_svc.parse_pem_metadata(full)
    assert "app.example.com" in meta["domains"]
    assert meta["fingerprint_sha256"]
    assert meta["not_after"] is not None
    days = cert_svc.days_until_expiry(meta["not_after"])
    assert days is not None and 30 <= days <= 40


def test_build_combined_pem_order():
    full = "-----BEGIN CERTIFICATE-----\nC\n-----END CERTIFICATE-----\n"
    key = "-----BEGIN PRIVATE KEY-----\nK\n-----END PRIVATE KEY-----\n"
    combined = cert_svc.build_combined_pem(key, full)
    assert combined.index("PRIVATE KEY") < combined.index("CERTIFICATE")


def test_fingerprint_of_pems_stable():
    full, key = _make_self_signed_pem()
    a = cert_svc.fingerprint_of_pems(full, key)
    b = cert_svc.fingerprint_of_pems(full, key)
    assert a == b
    assert a != cert_svc.fingerprint_of_pems(full, key + "x")


def test_parse_pem_empty():
    with pytest.raises(ValueError):
        cert_svc.parse_pem_metadata("")
