# PiHerder

**Self-hosted control plane for the Pis and Linux boxes you already SSH into — backups, patching, Compose, and an audit trail. Secrets in the database are encrypted; the master key stays on your disk.**

![PiHerder Logo](app/static/images/piherder-logo.png)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Release](https://img.shields.io/badge/release-v1.1.0-green.svg)](docs/RELEASE_v1.1.0.md)
[![Docker Hub](https://img.shields.io/badge/docker-bjorngluck%2Fpiherder-blue.svg)](https://hub.docker.com/r/bjorngluck/piherder)
[![Docs](https://img.shields.io/badge/docs-wiki-red.svg)](https://piherder-docs.hacknow.info/)
[![Demo](https://img.shields.io/badge/demo-view--only-orange.svg)](https://piherder-demo.hacknow.info)
[![Sponsor](https://img.shields.io/badge/Sponsor-%231EAEDB?logo=githubsponsors&logoColor=fff&style=flat)](https://github.com/sponsors/bjorngluck)

### Public demo site (view-only)

**PiHerder has a live public demo** so you can explore the UI before installing:

**→ [https://piherder-demo.hacknow.info](https://piherder-demo.hacknow.info)**

| | |
|--|--|
| **Host** | https://piherder-demo.hacknow.info |
| **Access** | Shared **viewer** account (read-oriented menus; not admin) |
| **Login** | Current username and password: **[wiki / Public demo](https://piherder-docs.hacknow.info/operations/demo-site/)** |

No install required. Log in with the shared credentials from the wiki, click around the dashboard, hosts, jobs, maps, and integrations. There is **no path to your machines** — the fleet is synthetic, jobs are simulated, and real SSH / API tokens / onboarding are disabled.

**Demo ≠ production pixel-for-pixel.** Some screens and “highlighted” features on the demo are **not 100% aligned** with a real self-hosted fleet. That is intentional: hosts, inventory, jobs, maps, and integrations are **seeded / simulated** so the sandbox is safe and disposable. Expect canned job results, static sample data, and occasional empty or simplified panels where a live deployment would talk to real Pis, Docker, or external services. Your own install against real hosts is the accurate product experience.

Full limits and notes: **[Public demo (wiki)](https://piherder-docs.hacknow.info/operations/demo-site/)**. The shared password may rotate; **the live wiki page always has the current password**.

### Why PiHerder?

After 30+ years as an engineer, senior cybersecurity leader, tinkerer, hacker, and 3D designer/builder, I got tired of brittle bash scripts and manual processes across my Raspberry Pi clusters and homelab.

PiHerder was born the same way many great tools are: **scripts that automate the boring stuff so I could focus on building and securing systems**. It replaces manual workflows with an auditable web UI. Fleet secrets in the database are Fernet-encrypted; `PIHERDER_MASTER_KEY` stays in your host `.env`.

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
- RBAC, 2FA (TOTP + passkeys), optional SSO/OIDC (v1.2), audit trail, self-backup with full DR
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
- Public demo (view-only): [piherder-demo.hacknow.info](https://piherder-demo.hacknow.info) · [wiki credentials](https://piherder-docs.hacknow.info/operations/demo-site/)
- Admin guide: [docs/ADMIN.md](docs/ADMIN.md)
- Ecosystem roadmap: [docs/ROADMAP_ECOSYSTEM.md](docs/ROADMAP_ECOSYSTEM.md)
- **Current production (Hub):** [docs/RELEASE_v1.1.0.md](docs/RELEASE_v1.1.0.md) (day-to-day operator improvements — certs · discovery · identity · UX · maps · API)
- **Next release:** `v1.2.0` — **feature-complete** on `v1.2.0-dev`, awaiting [operator QA](docs/QA_v1.2.0.md) (passkeys · SSO · webshell · gated demo · full DB self-backup · security remediations). Not tagged; Hub `latest` is still **1.1.0**. Notes: [RELEASE_v1.2.0.md](docs/RELEASE_v1.2.0.md)
- Prior: [docs/RELEASE_v1.0.0.md](docs/RELEASE_v1.0.0.md) · operator wiki [LAN Discovery](wiki/integrations/lan-discovery.md) · [HAOS hosts](wiki/day-to-day/haos-hosts.md)
- API reference: [docs/API.md](docs/API.md)

### Tech Stack

FastAPI + SQLModel + PostgreSQL + Paramiko + cryptography (Fernet) + Jinja2 + compiled Tailwind + HTMX + Alpine + APScheduler + Celery.

**Offline / air-gapped ready** — Once built, the container has no external CDN dependencies. Tailwind utilities are compiled CSS (no Play / no eval).

### License

MIT — see [LICENSE](LICENSE).

---

**Repository:** [github.com/bjorngluck/piherder](https://github.com/bjorngluck/piherder)
