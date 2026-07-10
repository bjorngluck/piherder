# PiHerder v0.2.0

**Date:** 2026-07-10  
**Git tag:** `v0.2.0`  
**Theme:** Production install story (H0) + platform reliability (H0.5) + early integration hub (Uptime Kuma)

This is the first tagged release. Image registry publish (Docker Hub / GHCR) is optional follow-up; until then operators build with `docker compose up -d --build`. See [PUBLISH_IMAGE.md](PUBLISH_IMAGE.md).

---

## Highlights

### Production readiness (Horizon 0)
- Compose volume defaults (`./backups`, `./piherder_backups`, `./piherder_data`, `./certs`)
- Token REST API (`/api/v1`) with scopes, feature gates, IP allowlists
- Prometheus `GET /metrics` exporter
- Production operator docs (TLS, upgrades, API tokens, webhooks)
- Community scaffolding: `SECURITY.md`, Discussions pointers

### Platform reliability (Horizon 0.5 / v0.2.x)
- Remote **host dependency** probes (`rsync`, docker, apt) with chips and install hints
- Settings → **Status** tab (web, Postgres, Redis, Celery, scheduler, mount free; on-demand backup tree usage)
- Celery multi-slot concurrency (`CELERY_CONCURRENCY`, default 2) + per-server backup mutex

### Integration hub — Uptime Kuma (H1 slice, ahead of v0.3 freeze)
- Top-level **Integrations** nav
- Kuma API key + `GET /metrics` poll; optional login for dashboard IDs
- Bindings: SSH, host services, Docker project/container
- Fleet `/services` + per-server Services pages; logos; deep links; down notifications

### Fleet core (already on main before this tag)
- SSH onboard, backups, OS/Docker patch, jobs, audit, IAM/2FA, PWA + Web Push
- Docker inventory cache, multi-file compose edit, herder self-backup/restore
- DB-backed settings, feature hard-hide, responsive UI

---

## Install

```bash
git clone https://github.com/bjorngluck/piherder.git
cd piherder
git checkout v0.2.0
cp .env.example .env
# set PIHERDER_MASTER_KEY (Fernet) and other required vars
docker compose up -d --build
```

Details: [README.md](../README.md) · [ADMIN.md](ADMIN.md)

---

## Package version

`pyproject.toml` → `0.2.0`

---

## Toward v0.3.0

- Grafana integration (deep links / chips) — implemented on `main` after this tag
- Multi Pi-hole / generic URL adapters
- Freeze remaining H1 items; published multi-arch image when credentials allow
