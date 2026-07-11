"""Grafana adapter helpers (no live Grafana)."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.services.integrations import grafana as gf
from app.services.integrations import registry as reg


def test_normalize_base_url():
    assert gf.normalize_base_url("https://g.example.com/") == "https://g.example.com"
    with pytest.raises(ValueError):
        gf.normalize_base_url("not-a-url")
    with pytest.raises(ValueError):
        gf.normalize_base_url("")


def test_dashboard_path_and_open_url():
    assert gf.dashboard_path("abc123", "node-exporter") == "/d/abc123/node-exporter"
    assert gf.open_grafana_url("https://g.example.com", "/d/x") == "https://g.example.com/d/x"
    assert gf.open_grafana_url("https://g.example.com/") == "https://g.example.com"


def test_hostname_short():
    assert gf.hostname_short("rpi5-1.hacknow.info") == "rpi5-1"
    assert gf.hostname_short("", "RPI5-2") == "rpi5-2"


def test_apply_query_template():
    q = gf.apply_query_template(
        "var-job={hostname_short}_exporter&var-instance={hostname}",
        hostname="rpi5-1.hacknow.info",
        name="RPI5-1",
    )
    assert q == "var-job=rpi5-1_exporter&var-instance=rpi5-1.hacknow.info"
    assert gf.apply_query_template("", hostname="x") == ""


def test_open_dashboard_url_with_query():
    url = gf.open_dashboard_url(
        "https://g.example.com",
        uid="uid1",
        slug="hosts",
        query_template="var-job={hostname_short}_exporter",
        hostname="rpi5-1.hacknow.info",
    )
    assert url.startswith("https://g.example.com/d/uid1/hosts")
    assert "var-job=rpi5-1_exporter" in url


def test_open_dashboard_url_relative():
    url = gf.open_dashboard_url(
        "https://g.example.com",
        uid="uid1",
        relative_url="/d/uid1/my-dash",
        query_template="var-ip={ip}",
        ip_address="10.0.0.5",
    )
    assert "/d/uid1/my-dash" in url
    assert "var-ip=10.0.0.5" in url


def test_fetch_health_ok(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/api/health")
        return httpx.Response(
            200,
            json={"database": "ok", "version": "10.4.0", "commit": "abc"},
        )

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    with patch("app.services.integrations.grafana.httpx.Client", side_effect=client_factory):
        result = gf.fetch_health("https://g.example.com", token="")
    assert result.ok
    assert result.version == "10.4.0"
    assert result.database == "ok"


def test_fetch_health_fail_db(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"database": "fail", "version": "10.0.0"})

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    with patch("app.services.integrations.grafana.httpx.Client", side_effect=client_factory):
        result = gf.fetch_health("https://g.example.com")
    assert not result.ok


def test_fetch_dashboards(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert "Bearer glsa_test" in request.headers.get("Authorization", "")
        return httpx.Response(
            200,
            json=[
                {
                    "uid": "abc",
                    "title": "Node Exporter",
                    "url": "/d/abc/node-exporter",
                    "tags": ["linux"],
                    "folderTitle": "Infra",
                },
                {
                    "uid": "zzz",
                    "title": "AAA First",
                    "url": "/d/zzz/aaa",
                    "tags": [],
                },
            ],
        )

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    with patch("app.services.integrations.grafana.httpx.Client", side_effect=client_factory):
        dashes = gf.fetch_dashboards("https://g.example.com", "glsa_test")
    assert len(dashes) == 2
    # Sorted by title
    assert dashes[0].title == "AAA First"
    assert dashes[1].uid == "abc"
    assert dashes[1].folder_title == "Infra"


def test_fetch_dashboards_no_token():
    assert gf.fetch_dashboards("https://g.example.com", "") == []


def test_poll_status_json():
    r = gf.GrafanaPollResult(
        ok=True,
        version="11.0.0",
        database="ok",
        dashboards=[
            gf.GrafanaDashboard(uid="u1", title="T1", url="/d/u1/t1"),
        ],
    )
    st = r.to_status_json()
    assert st["ok"] is True
    assert st["dashboard_count"] == 1
    assert st["monitor_count"] == 1
    assert st["dashboards"][0]["uid"] == "u1"


def test_binding_open_url_grafana():
    """registry.binding_open_url builds Grafana deep links with server vars."""
    integ = MagicMock()
    integ.type = reg.TYPE_GRAFANA
    integ.base_url = "https://g.example.com"
    integ.config_json = json.dumps(
        {"query_template": "var-job={hostname_short}_exporter"}
    )

    binding = MagicMock()
    binding.external_id = "dashuid"
    binding.external_meta_json = json.dumps(
        {"uid": "dashuid", "url": "/d/dashuid/hosts", "title": "Hosts"}
    )
    binding.server_id = 3

    server = MagicMock()
    server.hostname = "rpi5-2.hacknow.info"
    server.name = "RPI5-2"
    server.ip_address = "10.0.0.2"
    server.id = 3

    url = reg.binding_open_url(integ, binding, server=server)
    assert "https://g.example.com/d/dashuid/hosts" in url
    assert "var-job=rpi5-2_exporter" in url
