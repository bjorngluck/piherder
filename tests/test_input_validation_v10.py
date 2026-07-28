"""v1.0 AV — pure unit tests for risk-based input validators."""
from __future__ import annotations

import pytest

from app.services.input_validation import (
    CERT_LAYOUTS,
    DOCKER_CONTAINER_ACTIONS,
    ValidationError,
    allowlist,
    clamp_str,
    clamp_text_blob,
    safe_cron,
    safe_hostname,
    safe_path,
    safe_ssh_user,
)


def test_clamp_str_rejects_nul_and_oversize():
    with pytest.raises(ValidationError):
        clamp_str("a\x00b", field="x")
    with pytest.raises(ValidationError):
        clamp_str("x" * 10, max_len=5, field="x")
    assert clamp_str("  hi  ", max_len=10) == "hi"


def test_safe_path_blocks_traversal():
    assert safe_path("~/certs") == "~/certs"
    assert safe_path("/etc/ssl/certs") == "/etc/ssl/certs"
    with pytest.raises(ValidationError):
        safe_path("../etc/passwd")
    with pytest.raises(ValidationError):
        safe_path("/tmp/../etc/passwd")
    with pytest.raises(ValidationError):
        safe_path("foo;rm -rf")
    with pytest.raises(ValidationError):
        safe_path(r"C:\windows")


def test_safe_hostname_and_user():
    assert safe_hostname("rpi5-1.local") == "rpi5-1.local"
    assert safe_hostname("192.168.1.10") == "192.168.1.10"
    with pytest.raises(ValidationError):
        safe_hostname("host:22")
    with pytest.raises(ValidationError):
        safe_hostname("bad host")
    assert safe_ssh_user("piherder") == "piherder"
    with pytest.raises(ValidationError):
        safe_ssh_user("root;id")
    with pytest.raises(ValidationError):
        safe_ssh_user("-evil")


def test_safe_cron():
    assert safe_cron("0 6 * * *") == "0 6 * * *"
    assert safe_cron("") is None
    with pytest.raises(ValidationError):
        safe_cron("not a cron")
    with pytest.raises(ValidationError):
        safe_cron("0 6 * * * *")  # 6 fields


def test_allowlist_actions():
    assert allowlist("restart", DOCKER_CONTAINER_ACTIONS, field="action") == "restart"
    with pytest.raises(ValidationError):
        allowlist("delete", DOCKER_CONTAINER_ACTIONS, field="action")
    assert allowlist("pair", CERT_LAYOUTS, field="layout") == "pair"


def test_clamp_text_blob():
    assert clamp_text_blob("pem", max_chars=10) == "pem"
    with pytest.raises(ValidationError):
        clamp_text_blob("x" * 20, max_chars=10, field="pem")
