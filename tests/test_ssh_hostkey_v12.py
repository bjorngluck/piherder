"""SSH host-key TOFU / mismatch (no live network)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import ssh as ssh_svc


class _FakeKey:
    def __init__(self, name: str, b64: str, blob: bytes = b"blob-a"):
        self._name = name
        self._b64 = b64
        self._blob = blob

    def get_name(self):
        return self._name

    def get_base64(self):
        return self._b64

    def asbytes(self):
        return self._blob


def test_tofu_allows_first_key():
    policy = ssh_svc.HostKeyPinPolicy(None, None)
    policy.missing_host_key(None, "lab.example", _FakeKey("ssh-ed25519", "AAAAfirst"))
    assert policy.seen_key.get_base64() == "AAAAfirst"


def test_pin_accepts_matching_key():
    policy = ssh_svc.HostKeyPinPolicy("ssh-ed25519", "AAAAfirst", "SHA256:abc")
    policy.missing_host_key(None, "lab.example", _FakeKey("ssh-ed25519", "AAAAfirst"))


def test_pin_rejects_mismatch():
    policy = ssh_svc.HostKeyPinPolicy("ssh-ed25519", "AAAAfirst", "SHA256:old")
    with pytest.raises(ssh_svc.HostKeyMismatch) as ei:
        policy.missing_host_key(
            None, "lab.example", _FakeKey("ssh-ed25519", "AAAAevil", b"blob-b")
        )
    assert "mismatch" in str(ei.value).lower()
    assert ei.value.expected_fp == "SHA256:old"


def test_clear_host_key_pin():
    server = SimpleNamespace(
        ssh_hostkey_type="ssh-ed25519",
        ssh_hostkey_b64="AAA",
        ssh_hostkey_fp="SHA256:x",
    )
    ssh_svc.clear_host_key_pin(server)
    assert server.ssh_hostkey_b64 is None
    assert server.ssh_hostkey_fp is None


def test_fingerprint_stable():
    k = _FakeKey("ssh-ed25519", "AAA", b"same-bytes")
    assert ssh_svc.host_key_fingerprint(k) == ssh_svc.host_key_fingerprint(k)
    assert ssh_svc.host_key_fingerprint(k).startswith("SHA256:")
