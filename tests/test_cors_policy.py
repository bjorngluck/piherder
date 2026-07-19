"""CORS allowlist parsing — opt-in only, no wildcards."""
from fastapi import FastAPI

from app.services.cors_policy import apply_cors_middleware, parse_cors_origins


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


def test_parse_dedupes_and_newlines():
    assert parse_cors_origins(
        "https://a.example\nhttps://a.example;https://b.example/"
    ) == ["https://a.example", "https://b.example"]


def test_apply_cors_middleware_noop_and_attach():
    app = FastAPI()
    before = len(app.user_middleware)
    apply_cors_middleware(app, [])
    assert len(app.user_middleware) == before
    apply_cors_middleware(app, ["https://n8n.example.com"])
    assert len(app.user_middleware) == before + 1
