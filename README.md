# PiHerder

**Secure fleet management for Raspberry Pi clusters — backups, patching, containers, and control with zero plaintext secrets.**

![PiHerder Logo](app/static/images/piherder-logo.png)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Release](https://img.shields.io/badge/release-v0.5.0--rc-green.svg)](docs/RELEASE_v0.5.0.md)
[![Docs](https://img.shields.io/badge/docs-wiki-red.svg)](https://bjorngluck.github.io/piherder/)

### Why PiHerder?

After 30+ years as an engineer, senior cybersecurity leader, tinkerer, hacker, and 3D designer/builder, I got tired of brittle bash scripts and manual processes across my Raspberry Pi clusters and homelab.

PiHerder was born the same way many great tools are: **scripts that automate the boring stuff so I could focus on building and securing systems**. It replaces manual workflows with an auditable web UI while keeping secrets encrypted at rest and never storing plaintext.

Inspired by projects like [Nginx Proxy Manager](https://github.com/NginxProxyManager/nginx-proxy-manager) — simple, powerful, self-hosted tools that just make life easier.

### Key Features

- SSH key management with encrypted private keys
- Backups, OS patching, container patching with schedules
- Docker Compose browser, multi-file editing, inventory cache
- Service templates (deploy wizard, variables, preview/confirm)
- Integrations: Uptime Kuma, Grafana, Pi-hole (v6), Nginx Proxy Manager + cert management
- Network Maps (DNS fabric, logical/physical topology, service paths)
- PWA + Web Push notifications
- RBAC, 2FA, audit trail, self-backup with full DR
- Token REST API for automation (n8n, Home Assistant, etc.)

### Quick Start

See the full **[Getting Started guide](https://bjorngluck.github.io/piherder/getting-started/install/)**.

1. Clone this repo or copy `.env.example` → `.env`
2. Generate `PIHERDER_MASTER_KEY` (critical)
3. (Recommended) Set hostname + trusted TLS certs for PWA/push
4. `docker compose up -d --build`
5. Register first admin user and start adding servers

### Open Source & Contributing

PiHerder is now **open source** under the MIT license. Contributions, issues, feature ideas, and PRs are very welcome.

See [CONTRIBUTING.md](CONTRIBUTING.md) for details.

If you find PiHerder useful, consider [sponsoring the project](https://github.com/sponsors/bjorngluck) or buying me a coffee — it helps fund continued development and infrastructure.

### Documentation

- Full docs & wiki: [bjorngluck.github.io/piherder](https://bjorngluck.github.io/piherder/)
- Admin guide: [docs/ADMIN.md](docs/ADMIN.md)
- Ecosystem roadmap: [docs/ROADMAP_ECOSYSTEM.md](docs/ROADMAP_ECOSYSTEM.md)
- API reference: [docs/API.md](docs/API.md)
- Release notes: [docs/RELEASE_v0.5.0.md](docs/RELEASE_v0.5.0.md)

### Tech Stack

FastAPI + SQLModel + PostgreSQL + Paramiko + cryptography (Fernet) + Jinja2 + Tailwind + HTMX + Alpine + APScheduler + Celery.

**Offline / air-gapped ready** — Once built, the container has no external CDN dependencies.

### License

MIT — see [LICENSE](LICENSE).

---

**Repository:** [github.com/bjorngluck/piherder](https://github.com/bjorngluck/piherder)
