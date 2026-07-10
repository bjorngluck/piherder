"""Uptime Kuma adapter + binding helpers (mocked /metrics, no live Kuma)."""
from __future__ import annotations

import json

import httpx
import pytest

from app.services.integrations import uptime_kuma as kuma
from app.services.integrations import registry as reg
from app.security.encryption import encrypt_str, decrypt_str


# With monitor_id (some exporters / older docs)
SAMPLE_METRICS = """
# HELP monitor_status Monitor Status (1 = UP, 0= DOWN, 2= PENDING, 3= MAINTENANCE)
# TYPE monitor_status gauge
monitor_status{monitor_id="1",monitor_name="rpi5-1 SSH",monitor_type="port",monitor_url="",monitor_hostname="rpi5-1",monitor_port="22"} 1
monitor_status{monitor_id="2",monitor_name="rpi5-2 SSH",monitor_type="port",monitor_url="",monitor_hostname="10.0.0.2",monitor_port="22"} 0
monitor_status{monitor_id="3",monitor_name="Grafana",monitor_type="http",monitor_url="https://g.example",monitor_hostname="",monitor_port=""} 1
monitor_response_time{monitor_id="1",monitor_name="rpi5-1 SSH",monitor_type="port",monitor_url="",monitor_hostname="rpi5-1",monitor_port="22"} 12
monitor_response_time{monitor_id="2",monitor_name="rpi5-2 SSH",monitor_type="port",monitor_url="",monitor_hostname="10.0.0.2",monitor_port="22"} 45
"""

# Real Uptime Kuma export: NO monitor_id, hostname/port may be the string "null"
SAMPLE_METRICS_NO_ID = """
monitor_status{monitor_name="RPI5-1 SSH",monitor_type="port",monitor_url="https://",monitor_hostname="rpi5-1.hacknow.info",monitor_port="22"} 1
monitor_status{monitor_name="RPI5-2 SSH",monitor_type="port",monitor_url="https://",monitor_hostname="rpi5-2.hacknow.info",monitor_port="22"} 0
monitor_status{monitor_name="Home Assistant",monitor_type="http",monitor_url="https://homeassistant.example/",monitor_hostname="null",monitor_port="null"} 1
monitor_response_time{monitor_name="RPI5-1 SSH",monitor_type="port",monitor_url="https://",monitor_hostname="rpi5-1.hacknow.info",monitor_port="22"} 2
"""


def test_parse_prometheus_metrics():
    mons = kuma.parse_prometheus_metrics(SAMPLE_METRICS)
    assert len(mons) == 3
    by_id = {m.id: m for m in mons}
    assert by_id["1"].status == "up"
    assert by_id["1"].hostname == "rpi5-1"
    assert by_id["1"].port == "22"
    assert by_id["1"].response_time_ms == 12
    assert by_id["2"].status == "down"
    assert by_id["3"].type == "http"
    assert by_id["3"].status == "up"


def test_parse_metrics_without_monitor_id():
    """Production Kuma labels use monitor_name only (no monitor_id)."""
    mons = kuma.parse_prometheus_metrics(SAMPLE_METRICS_NO_ID)
    assert len(mons) == 3
    by_id = {m.id: m for m in mons}
    assert "RPI5-1 SSH" in by_id
    assert by_id["RPI5-1 SSH"].status == "up"
    assert by_id["RPI5-1 SSH"].hostname == "rpi5-1.hacknow.info"
    assert by_id["RPI5-1 SSH"].port == "22"
    assert by_id["RPI5-1 SSH"].response_time_ms == 2
    assert by_id["RPI5-2 SSH"].status == "down"
    # string "null" cleaned
    assert by_id["Home Assistant"].hostname == ""
    assert by_id["Home Assistant"].port == ""


def test_normalize_base_url():
    assert kuma.normalize_base_url("http://rpi5-4:3001/") == "http://rpi5-4:3001"
    with pytest.raises(ValueError):
        kuma.normalize_base_url("not-a-url")
    with pytest.raises(ValueError):
        kuma.normalize_base_url("")


def test_metrics_url():
    assert kuma.metrics_url("http://rpi5-4:3001") == "http://rpi5-4:3001/metrics"


def test_suggest_monitor_for_server():
    mons = kuma.parse_prometheus_metrics(SAMPLE_METRICS)
    hit = kuma.suggest_monitor_for_server(
        mons, hostname="rpi5-1", ip_address="", ssh_port=22
    )
    assert hit is not None
    assert hit.id == "1"
    hit2 = kuma.suggest_monitor_for_server(
        mons, hostname="other", ip_address="10.0.0.2", ssh_port=22
    )
    assert hit2 is not None
    assert hit2.id == "2"
    miss = kuma.suggest_monitor_for_server(
        mons, hostname="nope", ip_address="", ssh_port=22
    )
    assert miss is None


def test_suggest_fqdn_and_ssh_name():
    mons = kuma.parse_prometheus_metrics(SAMPLE_METRICS_NO_ID)
    hit = kuma.suggest_monitor_for_server(
        mons, hostname="rpi5-1", ip_address="", ssh_port=22
    )
    assert hit is not None
    assert hit.id == "RPI5-1 SSH"
    hit_fqdn = kuma.suggest_monitor_for_server(
        mons, hostname="rpi5-2.hacknow.info", ssh_port=22
    )
    assert hit_fqdn is not None
    assert hit_fqdn.id == "RPI5-2 SSH"


def test_fetch_metrics_auth_and_parse(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization", "")
        # Basic :key → base64 of ":secret-key"
        assert auth.startswith("Basic ")
        return httpx.Response(200, text=SAMPLE_METRICS)

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(kuma.httpx, "Client", client_factory)
    result = kuma.fetch_metrics("http://kuma.test:3001", "secret-key")
    assert result.ok
    assert len(result.monitors) == 3


def test_fetch_metrics_401(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="nope")

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(kuma.httpx, "Client", client_factory)
    result = kuma.fetch_metrics("http://kuma.test:3001", "bad")
    assert not result.ok
    assert "Auth" in result.error or "401" in result.error


def test_encrypt_credentials_roundtrip():
    enc = reg.encrypt_credentials("uk1_abc_secret")
    assert enc
    assert "uk1_abc" not in enc
    plain = decrypt_str(enc)
    data = json.loads(plain)
    assert data["api_key"] == "uk1_abc_secret"


def test_status_json_shape():
    mons = kuma.parse_prometheus_metrics(SAMPLE_METRICS)
    result = kuma.KumaPollResult(ok=True, monitors=mons, raw_metric_lines=5)
    d = result.to_status_json()
    assert d["ok"] is True
    assert d["monitor_count"] == 3
    assert d["monitors"][0]["id"]


def test_open_kuma_url_dashboard():
    assert kuma.open_kuma_url("https://uptime.example", dashboard_id="32") == (
        "https://uptime.example/dashboard/32"
    )
    assert kuma.open_kuma_url("https://uptime.example/", monitor_id="5") == (
        "https://uptime.example/dashboard/5"
    )
    assert kuma.open_kuma_url("https://uptime.example") == "https://uptime.example"


def test_parse_cert_metrics():
    body = """
monitor_status{monitor_name="Grafana",monitor_type="http",monitor_url="https://g.example",monitor_hostname="null",monitor_port="null"} 1
monitor_cert_days_remaining{monitor_name="Grafana",monitor_type="http",monitor_url="https://g.example",monitor_hostname="null",monitor_port="null"} 57
monitor_cert_is_valid{monitor_name="Grafana",monitor_type="http",monitor_url="https://g.example",monitor_hostname="null",monitor_port="null"} 1
"""
    mons = kuma.parse_prometheus_metrics(body)
    assert len(mons) == 1
    assert mons[0].cert_days_remaining == 57
    assert mons[0].cert_is_valid is True
    assert mons[0].is_service_like() is True


def test_apply_dashboard_id_map():
    mons = kuma.parse_prometheus_metrics(SAMPLE_METRICS_NO_ID)
    kuma.apply_dashboard_id_map(mons, {"RPI5-1 SSH": "32", "RPI5-2 SSH": "33"})
    by_name = {m.name: m for m in mons}
    assert by_name["RPI5-1 SSH"].dashboard_id == "32"
    assert (
        kuma.open_kuma_url("https://uptime.hacknow.info", dashboard_id=by_name["RPI5-1 SSH"].dashboard_id)
        == "https://uptime.hacknow.info/dashboard/32"
    )
