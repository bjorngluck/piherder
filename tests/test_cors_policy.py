"""CORS allowlist parsing — opt-in only, no wildcards."""
from app.services.cors_policy import parse_cors_origins


def test_parse_empty():
    assert parse_cors_origins(None) == []
    assert parse_cors_origins("") == []
    assert parse_cors_origins("  ") == []


def test_parse_list():
    assert parse_cors_origins(
        "https://n8n.example.com, https://ha.local:8123/"
    ) == ["https://n8n.example.com", "https://ha.local:8123"]


def test_reject_wildcard_and_relative():
    assert parse_cors_origins("*") == []
    assert parse_cors_origins("*,https://ok.example") == ["https://ok.example"]
    assert parse_cors_origins("evil.com") == []  # no scheme
