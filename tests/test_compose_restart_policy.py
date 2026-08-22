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
