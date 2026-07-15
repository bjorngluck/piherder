"""NPM adapter unit tests (no live NPM)."""
from __future__ import annotations

import io
import zipfile

import pytest

from app.services.integrations import npm as npm_mod


def test_normalize_base_url():
    assert npm_mod.normalize_base_url("https://nginx.example.com/") == (
        "https://nginx.example.com"
    )
    assert npm_mod.normalize_base_url("https://nginx.example.com/api") == (
        "https://nginx.example.com"
    )
    with pytest.raises(ValueError):
        npm_mod.normalize_base_url("")


def test_parse_certificate_zip_named_like_npm():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "fullchain50.pem",
            "-----BEGIN CERTIFICATE-----\nAAA\n-----END CERTIFICATE-----\n",
        )
        zf.writestr(
            "privkey50.pem",
            "-----BEGIN PRIVATE KEY-----\nBBB\n-----END PRIVATE KEY-----\n",
        )
        zf.writestr("cert50.pem", "-----BEGIN CERTIFICATE-----\nCCC\n-----END CERTIFICATE-----\n")
        zf.writestr("chain50.pem", "-----BEGIN CERTIFICATE-----\nDDD\n-----END CERTIFICATE-----\n")
    parts = npm_mod.parse_certificate_zip(buf.getvalue())
    assert "BEGIN CERTIFICATE" in parts["fullchain"]
    assert "PRIVATE KEY" in parts["privkey"]


def test_parse_certificate_zip_missing_key():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "fullchain.pem",
            "-----BEGIN CERTIFICATE-----\nAAA\n-----END CERTIFICATE-----\n",
        )
    with pytest.raises(ValueError, match="private key"):
        npm_mod.parse_certificate_zip(buf.getvalue())


def test_open_npm_url():
    assert npm_mod.open_npm_url("https://nginx.example.com", "/nginx/proxy") == (
        "https://nginx.example.com/nginx/proxy"
    )


def test_npm_detail_router_exports_detail_deps():
    """Regression: render_npm_detail needs json + _can_mutate (split-out bug)."""
    from app.routers import integrations_npm as npm_routes

    assert hasattr(npm_routes, "json")
    assert callable(npm_routes._can_mutate)
    assert callable(npm_routes.render_npm_detail)
