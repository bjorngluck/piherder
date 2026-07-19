"""Additional pure DNS fabric helpers (IPv4, host IP, tokens, identity)."""
from __future__ import annotations

from types import SimpleNamespace

from app.services.dns_fabric import core as fabric


def test_is_valid_ipv4():
    assert fabric.is_valid_ipv4("192.168.1.10")
    assert fabric.is_valid_ipv4("10.0.0.1")
    assert not fabric.is_valid_ipv4("999.1.1.1")
    assert not fabric.is_valid_ipv4("host.local")
    assert not fabric.is_valid_ipv4("")


def test_host_ip_for_dns_precedence():
    s = SimpleNamespace(
        dns_ip_override="203.0.113.5",
        ip_address="10.0.0.2",
        hostname="10.0.0.9",
    )
    assert fabric.host_ip_for_dns(s) == "203.0.113.5"
    s.dns_ip_override = ""
    assert fabric.host_ip_for_dns(s) == "10.0.0.2"
    s.ip_address = None
    assert fabric.host_ip_for_dns(s) == "10.0.0.9"
    s.hostname = "pi.local"
    assert fabric.host_ip_for_dns(s) == ""


def test_suggest_host_dns_name():
    s = SimpleNamespace(name="RPI5 Lab", hostname="10.0.0.1")
    assert fabric.suggest_host_dns_name(s, "example.com") == "rpi5-lab.example.com"
    assert fabric.suggest_host_dns_name(s, "") == ""


def test_server_name_tokens_and_identity():
    s = SimpleNamespace(
        name="RPI5-1",
        hostname="10.0.0.5",
        dns_name="rpi5-1.example.com",
    )
    tokens = fabric._server_name_tokens(s)
    assert "rpi5-1" in tokens
    assert "rpi5-1.example.com" in tokens
    # pure IPs excluded
    assert "10.0.0.5" not in tokens

    assert fabric.is_host_identity_name("rpi5-1.example.com", s) is True
    assert fabric.is_host_identity_name("app.example.com", s) is False
    assert fabric.is_host_identity_name("x", None) is False


def test_fqdn_match_tokens():
    t = fabric._fqdn_match_tokens("Grafana.Lab.example.com", "my-stack")
    assert "grafana.lab.example.com" in t
    assert "grafana" in t
    assert "my-stack" in t
    assert "mystack" in t  # stripped non-alnum
    assert fabric._fqdn_match_tokens(None, "x") == set()  # too short after filter


def test_already_present_error_detection():
    assert fabric._is_already_present_error("Record already exists")
    assert fabric._is_already_present_error("duplicate entry for domain")
    assert not fabric._is_already_present_error("connection refused")
