# PiHerder

**Secure fleet management for Raspberry Pi clusters — backups, patching, containers, and control with zero plaintext secrets.**

![PiHerder Logo](app/static/images/piherder-logo.png)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Release](https://img.shields.io/badge/release-v0.8.0-green.svg)](docs/RELEASE_v0.8.0.md)
[![Docker Hub](https://img.shields.io/badge/docker-bjorngluck%2Fpiherder-blue.svg)](https://hub.docker.com/r/bjorngluck/piherder)
[![Docs](https://img.shields.io/badge/docs-wiki-red.svg)](https://piherder-docs.hacknow.info/)
[![Sponsor](https://img.shields.io/badge/Sponsor-%231EAEDB?logo=githubsponsors&logoColor=fff&style=flat)](https://github.com/sponsors/bjorngluck)

### Why PiHerder?

After 30+ years as an engineer, senior cybersecurity leader, tinkerer, hacker, and 3D designer/builder, I got tired of brittle bash scripts and manual processes across my Raspberry Pi clusters and homelab.

PiHerder was born the same way many great tools are: **scripts that automate the boring stuff so I could focus on building and securing systems**. It replaces manual workflows with an auditable web UI while keeping secrets encrypted at rest and never storing plaintext.

Inspired by projects like [Nginx Proxy Manager](https://github.com/NginxProxyManager/nginx-proxy-manager) — simple, powerful, self-hosted tools that just make life easier.

### Key Features

- SSH key management with encrypted private keys
- Backups, OS patching (apt), **HAOS** hosts via SSH + `ha` CLI check/apply, container patching with schedules
- Docker Compose browser, multi-file editing, **compose sets** (multiple compose files under one project), inventory cache
- Service templates (deploy wizard, variables, preview/confirm)
- Integrations: Uptime Kuma, Grafana, Pi-hole (v6), Nginx Proxy Manager + cert management
- **LAN Discovery** (opt-in nmap worker, devices, schedules, Hosts map overlay)
- Network Maps (DNS fabric, logical/physical topology, service paths, runtime stack view groups)
- PWA + Web Push notifications
- RBAC, 2FA, audit trail, self-backup with full DR
- Token REST API for automation (n8n, Home Assistant, etc.)

### Quick Start

See the full **[Getting Started guide](https://piherder-docs.hacknow.info/getting-started/install/)**.

1. Clone this repo or copy `.env.example` → `.env`
2. Generate `PIHERDER_MASTER_KEY` (critical)
3. (Recommended) Set hostname + trusted TLS certs for PWA/push
4. `docker compose up -d` (pulls multi-arch `bjorngluck/piherder:latest`)
5. Register first admin user and start adding servers

### Open Source & Contributing

PiHerder is now **open source** under the MIT license. Contributions, issues, feature ideas, and PRs are very welcome.

See [CONTRIBUTING.md](CONTRIBUTING.md) for details.

If you find PiHerder useful, consider [sponsoring the project](https://github.com/sponsors/bjorngluck) or buying me a coffee — it helps fund continued development and infrastructure.

### Documentation

- Full docs & wiki: [piherder-docs.hacknow.info](https://piherder-docs.hacknow.info/)
- Admin guide: [docs/ADMIN.md](docs/ADMIN.md)
- Ecosystem roadmap: [docs/ROADMAP_ECOSYSTEM.md](docs/ROADMAP_ECOSYSTEM.md)
- Latest release: [docs/RELEASE_v0.8.0.md](docs/RELEASE_v0.8.0.md) (RC3 — LAN Discovery · screenshots · quality)
- Prior: [docs/RELEASE_v0.7.0.md](docs/RELEASE_v0.7.0.md) · plan [docs/PLAN_v0.8.0.md](docs/PLAN_v0.8.0.md) · [docs/FEATURE_PLAN_LAN_NMAP.md](docs/FEATURE_PLAN_LAN_NMAP.md) · operator wiki [LAN Discovery](wiki/integrations/lan-discovery.md)
- Active (**v0.9.0** last pre-production — UX · quality · **HAOS path 1**): [docs/PLAN_v0.9.0.md](docs/PLAN_v0.9.0.md) · [docs/FEATURE_PLAN_HOME_ASSISTANT.md](docs/FEATURE_PLAN_HOME_ASSISTANT.md) · wiki [HAOS hosts](wiki/day-to-day/haos-hosts.md)
- Host lifecycle design: [docs/FEATURE_PLAN_HOST_LIFECYCLE.md](docs/FEATURE_PLAN_HOST_LIFECYCLE.md)
- API reference: [docs/API.md](docs/API.md)

### Tech Stack

FastAPI + SQLModel + PostgreSQL + Paramiko + cryptography (Fernet) + Jinja2 + Tailwind + HTMX + Alpine + APScheduler + Celery.

**Offline / air-gapped ready** — Once built, the container has no external CDN dependencies.

### License

MIT — see [LICENSE](LICENSE).

---

**Repository:** [github.com/bjorngluck/piherder](https://github.com/bjorngluck/piherder)
