# PiHerder v0.3.0

**Date:** 2026-07-11  
**Git tag:** `v0.3.0`  
**Theme:** Integration hub — Uptime Kuma (from v0.2) + **Grafana** deep links (H1)

Image registry publish (Docker Hub / GHCR) remains optional; operators build with `docker compose up -d --build`. See [PUBLISH_IMAGE.md](PUBLISH_IMAGE.md).

---

## Highlights

### Grafana integration (Horizon 1)
- Top-level **Integrations → Grafana**: base URL, optional service account token (Fernet-encrypted)
- Health poll (`GET /api/health`) — version / database chips
- Dashboard inventory (`GET /api/search?type=dash-db`) when a token is set
- **Binding kinds** with separate query templates (`var-` prefix):
  - **Host metrics** — e.g. `var-job={hostname_short}_exporter`
  - **Containers** — host overview or per-container (`{container}`)
  - **Host logs** — e.g. `var-host={hostname_short}`
- Placeholders: `{hostname}`, `{hostname_short}`, `{name}`, `{container}`, `{project}`, `{ip}`, …
- **Server detail** — full Grafana rows (kind label + Open in Grafana)
- **Docker page** — per-container **Grafana** chip; **⋯ → Grafana: &lt;dashboard&gt;**; expanded row link (mobile-friendly, no tooltip required)
- Tabbed bind UI: Host metrics / Containers / Logs; clone & edit prefill; unique-scope merge on clone
- Kind inference from Docker scope so container binds stay on the Containers tab after poll/refresh
- Included in **PiHerder self-backup** (`integrations` + `integration_bindings`)

### Uptime Kuma (included since v0.2.0)
- API key + `/metrics` poll; SSH / host service / Docker bindings; Services pages; logos; down notifications

### Platform (carried from v0.2.0)
- Compose defaults, token REST API, Status tab, host dependency probes, multi-worker Celery, IAM/2FA, PWA + push, herder self-backup

---

## Breaking / migration notes

- No DB schema migrations required beyond those already on `main` for integrations (run `alembic upgrade head` / usual compose start migrations as always).
- Existing Grafana container bindings created before kind inference may have been re-kinded by the post-refresh fix; re-open Integrations → Grafana and confirm tabs if needed.
- Restore of encrypted Grafana tokens requires the **same `PIHERDER_MASTER_KEY`**.

---

## Install

```bash
git clone https://github.com/bjorngluck/piherder.git
cd piherder
git checkout v0.3.0
cp .env.example .env
# set PIHERDER_MASTER_KEY (Fernet) and other required vars
docker compose up -d --build
```

Upgrade from v0.2.0:

```bash
git fetch --tags
git checkout v0.3.0
docker compose up -d --build
```

Details: [README.md](../README.md) · [ADMIN.md](ADMIN.md) § Grafana integration

---

## Package version

`pyproject.toml` → `0.3.0`

---

## Docs & tests

- Operator: [ADMIN.md](ADMIN.md) — Grafana templates, Docker chips, DR  
- Plan: [FEATURE_PLAN_INTEGRATIONS.md](FEATURE_PLAN_INTEGRATIONS.md)  
- Roadmap: [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md)  
- Tests: `tests/test_integrations_grafana.py`, herder backup includes integrations/bindings  

---

## Toward v0.4 / remaining H1

- **Post-tag on main:** Docker Deploy honesty + resolve container-update alerts — plan as **v0.3.1** and/or include in **v0.4.0** ([PLAN_v0.4.0.md](PLAN_v0.4.0.md))  
- Multi Pi-hole / NPM / HA / Frigate / n8n generic URL adapters  
- Service deployment templates (H2)  
- Optional multi-arch image publish when credentials allow  
