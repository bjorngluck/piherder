"""Stock compose must restart the control plane after a host reboot.

Docker default RestartPolicy is ``no``. That left ``web`` exited after reboot
while db/redis/celery/caddy (already unless-stopped) came back — Caddy 502.
"""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker-compose.yml"

# Profile-gated nmap is still a production worker when enabled.
CORE_SERVICES = ("web", "db", "redis", "celery-worker", "celery-worker-nmap", "caddy")


def test_core_services_restart_unless_stopped():
    data = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    services = data["services"]
    missing = [name for name in CORE_SERVICES if name not in services]
    assert not missing, missing
    for name in CORE_SERVICES:
        policy = (services[name] or {}).get("restart")
        assert policy == "unless-stopped", f"{name} restart={policy!r} (want unless-stopped)"


def test_stock_caddy_is_one_address_and_nmap_is_opt_in():
    data = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    services = data["services"]
    trusted = services["web"]["environment"]["PIHERDER_TRUSTED_PROXY_CIDRS"]
    assert trusted.startswith("${PIHERDER_TRUSTED_PROXY_CIDRS:-")
    assert "192.0.2.10/32" in trusted
    assert "127.0.0.1/32" in trusted
    assert "192.0.2.0/24" not in trusted
    caddy_ip = services["caddy"]["networks"]["default"]["ipv4_address"]
    assert "192.0.2.10" in caddy_ip
    subnet = data["networks"]["default"]["ipam"]["config"][0]["subnet"]
    assert "192.0.2.0/24" in subnet
    assert services["celery-worker-nmap"]["profiles"] == ["nmap"]
