"""Generic URL (Int-gen) adapter + registry helpers."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.integrations import generic_url as gen
from app.services.integrations import registry as reg


def test_normalize_base_url():
    assert gen.normalize_base_url("https://ha.example.com/") == "https://ha.example.com"
    with pytest.raises(ValueError):
        gen.normalize_base_url("not-a-url")
    with pytest.raises(ValueError):
        gen.normalize_base_url("ftp://x")


def test_normalize_product_aliases():
    assert gen.normalize_product("ha") == "home_assistant"
    assert gen.normalize_product("Home Assistant") == "home_assistant"
    assert gen.normalize_product("frigate") == "frigate"
    assert gen.normalize_product("n8n") == "n8n"
    assert gen.normalize_product("something") == "custom"


def test_join_and_open_url():
    base = "https://n8n.example.com"
    assert gen.join_url(base, "") == base
    assert gen.join_url(base, "/") == base
    assert gen.join_url(base, "/healthz") == "https://n8n.example.com/healthz"
    assert gen.join_url(base, "healthz") == "https://n8n.example.com/healthz"
    assert gen.open_url(base, "https://other.example/") == "https://other.example/"


def test_probe_ok_2xx():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.url = "https://ha.local/"

    with patch("app.services.integrations.generic_url.httpx.Client") as Client:
        inst = Client.return_value.__enter__.return_value
        inst.get.return_value = mock_resp
        r = gen.probe("https://ha.local", product="home_assistant", health_path="/")
    assert r.ok is True
    assert r.status_code == 200
    assert r.product == "home_assistant"


def test_probe_401_counts_reachable():
    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.url = "https://frigate.local/api/version"

    with patch("app.services.integrations.generic_url.httpx.Client") as Client:
        inst = Client.return_value.__enter__.return_value
        inst.get.return_value = mock_resp
        r = gen.probe(
            "https://frigate.local",
            product="frigate",
            health_path="/api/version",
        )
    assert r.ok is True
    assert r.status_code == 401


def test_probe_500_not_ok():
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.url = "https://x/"

    with patch("app.services.integrations.generic_url.httpx.Client") as Client:
        inst = Client.return_value.__enter__.return_value
        inst.get.return_value = mock_resp
        r = gen.probe("https://x")
    assert r.ok is False
    assert "500" in (r.error or "")


def test_binding_open_url_path_join():
    """Unit-level open_url without full DB when fixtures missing."""
    from app.models import Integration, IntegrationBinding

    integ = Integration(
        id=1,
        type=reg.TYPE_GENERIC_URL,
        name="Frigate",
        base_url="https://frigate.local",
        enabled=True,
        config_json='{"product":"frigate","health_path":"/api/version"}',
    )
    binding = IntegrationBinding(
        id=1,
        integration_id=1,
        server_id=1,
        role=reg.ROLE_SERVICE,
        external_id="/events",
        external_meta_json='{"path":"/events"}',
    )
    assert reg.binding_open_url(integ, binding) == "https://frigate.local/events"

    binding2 = IntegrationBinding(
        id=2,
        integration_id=1,
        server_id=1,
        role=reg.ROLE_SERVICE,
        external_id="url",
        external_meta_json='{"url":"https://other.local/ui"}',
    )
    assert reg.binding_open_url(integ, binding2) == "https://other.local/ui"
