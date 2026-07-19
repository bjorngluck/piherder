# PiHerder v0.7.0

**Date:** 2026-07-19  
**Git tag:** `v0.7.0`  
**Baseline:** `v0.6.0` (RC2)  
**Theme:** Onboarding clarity — add-host wizard, Playwright E2E, topology presentation (view groups + compose sets)

**Plans:** [PLAN_v0.7.0.md](PLAN_v0.7.0.md) · [FEATURE_PLAN_HOST_LIFECYCLE.md](FEATURE_PLAN_HOST_LIFECYCLE.md) · [FEATURE_PLAN_RUNTIME_TOPOLOGY.md](FEATURE_PLAN_RUNTIME_TOPOLOGY.md)  
**Next:** [PLAN_v0.8.0.md](PLAN_v0.8.0.md) (RC3 — polish, E2E/coverage, docs+screenshots, nmap)  
**Roadmap:** [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · [SPEC.md](../SPEC.md)  
**Docs:** https://piherder-docs.hacknow.info/

**Image:** [bjorngluck/piherder](https://hub.docker.com/r/bjorngluck/piherder) — multi-arch `linux/amd64` + `linux/arm64`  
**Tags:** `0.7.0` · `0.7` · `latest`

---

## Highlights

### Add-host wizard (H2.75 P2)

- Primary **Add server** CTA opens guided wizard at `/servers/new` (8 steps: Identity → Trust → Connect → Privilege → Features → Schedules → Network → Done)
- **Advanced** single-form path kept at `/servers/new/advanced` (and legacy `/servers/add`)
- Connect step: **deploy key**, **test connection**, **clear stored password** CTA in-wizard
- Public key show/copy on Connect for manual install; private key **never** sent to the browser
- HAOS / least-priv guidance; schedules default to **checks only** (no surprise apply jobs)
- **Save & exit** mid-wizard leaves a partial host; resume from server detail / re-open wizard
- Wiki [Add a server](https://piherder-docs.hacknow.info/day-to-day/add-server/) primary path = wizard

### Playwright E2E (hard quality gate)

- New `e2e/` suite with **pytest-playwright** (Chromium)
- Compose set `docker-compose.e2e.yml` under project **piherder** (port **18000**); `scripts/e2e-up.sh` / `e2e-down.sh`
- **Phase A** shell smoke: login, nav, catalog tabs, theme, logout
- **Phase B** wizard journeys: primary CTA, identity→trust, no private key PEM in DOM, save & exit, clear-password, advanced form
- GitHub Actions **e2e** job on main + PR (path-filtered); failure traces/screenshots as artifacts

### Template drift check as Job

- **Check drift** runs as a fleet Job with **JobHold live log** (same pattern as deploy/redeploy)
- Exclusive per-host stack-mutation lane

### Topology annotations + view groups

- Exact compose-project matching; category override; fixed tags vocab
- **Visual service stacks** (presentation-only view groups) inside one compose project — not separate deploys
- Map columns from category vocab; stack panel / expand polish
- Migration **024** topology annotations

### Compose sets (multi-file, one project card)

- Discover extra `docker-compose.<name>.yml` / `compose.<name>.yml` in the same project folder
- Docker page: under-project pills **All / main / set** filter containers by set membership
- Optional **Deploy this set** → `docker compose -f <file> …` under the same project name
- Local e2e services live in `docker-compose.e2e.yml` (same project as main stack)

### Docs

- Wizard primary path, Docker compose sets, fabric view groups, e2e runbook (wiki + CONTRIBUTING)
- Screenshot **PNG pack** deferred to **v0.8.0** (full docs review + capture cycle)

---

## Intentionally not in v0.7.0

| Horizon | Items |
|---------|--------|
| **v0.8.0 RC3** | Overall polish · extended E2E · more unit coverage · **full document review + screenshot pack** · **LAN discovery (nmap)** · residual C items (multi-map cert Jobs, template empty states, T6 chips, list/perf) — [PLAN_v0.8.0.md](PLAN_v0.8.0.md) |
| **Later** | Host stats / allowlisted commands (P3) · bootstrap depth (P4) · web SSH (P5) · ACME-in-herder · NPM proxy write CRUD · Cloudflare DNS · large curated pack |

---

## Breaking / migration notes

| Change | Action |
|--------|--------|
| **New Alembic migration** | `024` topology annotations — apply on web startup |
| Encrypted secrets / certs | Same **`PIHERDER_MASTER_KEY`** required for restore and DR |
| E2E stack (dev only) | Optional compose set under project `piherder`; not required for production |

Existing v0.6.0 deployments: pull new image / checkout tag, keep `.env` + volumes, `docker compose up -d`.

---

## Install

```bash
git clone https://github.com/bjorngluck/piherder.git
cd piherder
git checkout v0.7.0
cp .env.example .env
# set PIHERDER_MASTER_KEY (Fernet) and SECRET_KEY — never use compose defaults in production
docker compose up -d
```

Optional pin:

```bash
export PIHERDER_IMAGE=bjorngluck/piherder:0.7.0
docker compose up -d
```

Docs: [Install](https://piherder-docs.hacknow.info/getting-started/install/) · [README](../README.md)

### Upgrade from v0.6.0

```bash
# 1) Self-backup + confirm PIHERDER_MASTER_KEY is safe offline
git fetch --tags
git checkout v0.7.0
docker compose pull
docker compose up -d
```

Migrations run on web startup. Review **Add server** (wizard) and **Docker** project pills / view groups if you use those surfaces.

---

## Package version

`pyproject.toml` / `APP_VERSION` → **`0.7.0`**

---

## Docs & tests

| Doc | Role |
|-----|------|
| [ADMIN.md](ADMIN.md) | Operator guide |
| [API.md](API.md) | REST `/api/v1` |
| [PUBLISH_IMAGE.md](PUBLISH_IMAGE.md) | Multi-arch publish process |
| [PLAN_v0.7.0.md](PLAN_v0.7.0.md) | Ship plan (feature-locked at tag) |
| [PLAN_v0.8.0.md](PLAN_v0.8.0.md) | Next RC3 cycle |
| Wiki | https://piherder-docs.hacknow.info/ |

**Unit tests:** full pytest pack at freeze.  
**E2E:** 11 Playwright journeys (Phase A + wizard B) in `e2e/`.

**Notable tests:** `test_server_wizard` · `test_compose_sets` · `test_container_annotations` · `test_nest_projects` · e2e shell + wizard suite

---

## Verify after upgrade

1. `docker compose ps` — web healthy; image `bjorngluck/piherder:…`
2. About page shows **0.7.0**
3. **Add server** opens wizard; Advanced form still works
4. Connect: deploy key / test / clear password; public key copyable; no private key in browser
5. Docker project: compose-set pills when extra compose files exist; view groups on stack/map
6. Template **Check drift** → Job + live log
7. Jobs Cancel still works; audit trail present

---

## Commits since v0.6.0

```bash
git log --oneline v0.6.0..v0.7.0
```

Representative areas:

| Area | Summary |
|------|---------|
| Wizard | 8-step add-host; connect actions; public key UX |
| E2E | Playwright A+B, compose set harness, CI job |
| Templates | Drift check as Job + JobHold |
| Topology | Annotations, view groups, map columns |
| Docker | Compose sets, set deploy, e2e file under project |
| Docs | Plans, wiki, freeze → 0.8 RC3 |

---

**End of v0.7.0 release notes.**
