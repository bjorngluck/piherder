"""External product integrations (Kuma, Grafana, Pi-hole, NPM, generic URL)."""

from . import generic_url, grafana, npm, pihole, poll, registry, uptime_kuma

__all__ = [
    "poll",
    "registry",
    "uptime_kuma",
    "grafana",
    "pihole",
    "npm",
    "generic_url",
]