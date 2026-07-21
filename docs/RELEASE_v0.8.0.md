# PiHerder v0.8.0

**Status:** **Draft** (pre-tag — 2026-07-21)  
**Date:** *fill at tag*  
**Git tag:** `v0.8.0` *(pending)*  
**Baseline:** `v0.7.0` (onboarding + E2E)  
**Theme:** RC3 quality + **LAN discovery (nmap)** — opt-in scans, network view, Hosts map overlay, retention, coverage growth, docs/screenshots

**Plans:** [PLAN_v0.8.0.md](PLAN_v0.8.0.md) · [FEATURE_PLAN_LAN_NMAP.md](FEATURE_PLAN_LAN_NMAP.md) · [FEATURE_PLAN_RUNTIME_TOPOLOGY.md](FEATURE_PLAN_RUNTIME_TOPOLOGY.md)  
**Prior:** [RELEASE_v0.7.0.md](RELEASE_v0.7.0.md)  
**Roadmap:** [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · [SPEC.md](../SPEC.md)  
**Docs:** https://piherder-docs.hacknow.info/

**Image:** [bjorngluck/piherder](https://hub.docker.com/r/bjorngluck/piherder) — multi-arch `linux/amd64` + `linux/arm64`  
**Tags (at publish):** `0.8.0` · `0.8` · `latest`

> **Draft note:** Product + wiki prose for LAN Discovery are on `main`. **Wiki screenshot PNG pack** and Hub multi-arch publish remain freeze gates. Package version is still `0.8.0.dev0` until tag day. Update this file’s Date / Status / Package version when tagging.

---

## Highlights

### LAN Discovery (nmap) — headline feature

Opt-in discovery of hosts on configured LAN CIDR(s). Discovery is **not** a managed Server until you promote or link.

- **Opt-in compose profile `nmap`:** dedicated `celery-worker-nmap` image (`Dockerfile.nmap`), Celery queue `nmap` only — **web never runs nmap**
- **Worker fence:** `PIHERDER_NMAP_WORKER=0` on web / main workers; `=1` only on the nmap worker (compose hard-codes). Tasks refuse if the fence is off or the `nmap` binary is missing
- **Integrations → LAN Discovery:** enable, CIDRs, excludes, Prefer SYN, worker online status, vuln pack status
- **Intensity ladder:** discovery · inventory · detailed · per-IP deep (curated argv; deep NSE presets: none / CPE / offline / full)
- **Multiple schedules** with create/edit (interval or cron), per-schedule options (SYN override, vuln scripts, timing/ports)
- **On-demand + per-device deep** from Network/Devices; detailed/deep confirm before enqueue
- **Auto-create devices** with MAC-first identity (DHCP IP updates in place); known/new lifecycle; ignore / link / unlink / promote (wizard prefill)
- **Network tab:** subnet-grouped cards + centered edit modal (map name, kind override, gateway role)
- **Hosts map overlay:** outer discovery chips; dual layout (radar = full canvas + rings; compact fleet fan when discovery off); 1:1 fit differs by mode; one-line chrome
- **Soft embed:** fleet list LAN chip + server detail discovery card
- **Vuln pack volume** (opt-in): nmap-vulners + vulscan tables + optional Exploit-DB index; Update job on nmap worker; web mounts volume **:ro** for Overview status
- Operator wiki: [LAN Discovery](https://piherder-docs.hacknow.info/integrations/lan-discovery/) · [env reference](https://piherder-docs.hacknow.info/operations/env-reference/) · [install](https://piherder-docs.hacknow.info/getting-started/install/)

### Stale data cleanup (stream R1)

- Settings → **Stale data cleanup**: opt-in schedule + Run now
- Purges terminal **Jobs**, **Audit**, and optional **nmap runs/XML** older than N days (default 30 when enabled)
- Never deletes pending/running jobs; distinct from per-server backup file retention

### Quality & tests (stream Q)

- HTTP TestClient smoke (auth gates + main shells + seeded nmap/server surfaces)
- Unit depth for nmap (parse, schedules, classify, worker guard, mocked scan, device lifecycle, fabric projection)
- CI coverage floor **30%**; freeze target **~50%** line coverage (~**49%** on main pre-tag)
- Playwright E2E: 0.7 base + extensions (e.g. B6 viewer cannot add server); still Chromium / no live lab nmap in CI

### Fabric / Hosts map polish

- Sticky map focus; independent stack order per view group
- Map expand respects view-group order; panel soft-reload fix after reorder

### Docs (stream A — prose)

- LAN Discovery, Hosts chrome / 1:1 behaviour, worker fence across wiki + [ADMIN.md](ADMIN.md) + `.env.example`
- **Screenshot PNG pack:** capture pass scheduled for freeze (wizard + nmap + residual 0.6/0.7 surfaces) — see [screenshots README](../wiki/assets/screenshots/README.md)

---

## Known issues (ship with awareness)

These are **accepted for v0.8.0** — not blockers for the RC3 tag. Expect follow-up in **v0.8.x / v0.9**.

| # | Area | Issue | Direction |
|---|------|--------|-----------|
| **1** | **Server onboarding wizard** | Experience works end-to-end but still needs **clarifications and refinements** for a smoother first-run path (copy, step guidance, edge cases, resume/save flows). | Polish pass in a follow-up release — not a rewrite of the wizard. |
| **2** | **Certificate management** | **Sudoers suggestions** can be incorrect for some layouts. Map UX still leans on **paired** fullchain/privkey; more **individual cert options** (not only pairs) and practical **app-specific templates** (e.g. Grafana-style) are **not practical** as shipped. | Revisit cert maps, sudoers snippets, and layout templates in the **next** cert-focused release. |
| **3** | **UX consistency & polish** | Residual chrome inconsistencies across wizard, Docker, certs, Jobs, and fabric (empty states, chips, list/perf, multi-map deploy Jobs, etc.). | **Continue polishing in the next release** — stream P from [PLAN_v0.8.0.md](PLAN_v0.8.0.md) / residual operator friction. |

Also note (ops, not product bugs):

- **Wiki screenshot PNG pack** may lag product briefly until the freeze capture pass lands (stream A).
- **Unit coverage** is ~**49%** pre-tag (RC3 target ~50%; CI floor 30%) — more depth welcome but not a ship-stopper.
- **Nmap worker** is opt-in; after enabling profile `nmap`, recreate `celery-worker-nmap` so `PIHERDER_NMAP_WORKER=1` is live.

---

## Intentionally not in v0.8.0

| Horizon | Items |
|---------|--------|
| **Later / P2 nmap** | Hosts map icons/shapes by kind · per-service port labels · dual-layout HTTP contracts · worker heartbeat on boot only |
| **Later** | Full entity delete cascade matrix + UI preview (R2) · host stats / allowlisted commands (P3) · bootstrap depth (P4) · **web SSH (P5)** · ACME-in-herder · NPM proxy write CRUD · Cloudflare DNS · K8s as supported install · large curated template pack · cert layout/template overhaul (see Known issues §2) · deep wizard UX pass (see §1) |
| **CI** | Live nmap of real networks (fixtures / mocks only) |

---

## Breaking / migration notes

| Change | Action |
|--------|--------|
| **Alembic migrations** | `025`–`030` nmap discovery (devices, schedules, scripts, MAC vendor, display name, kind/map role) — apply on web startup |
| **Opt-in nmap stack** | Default `docker compose up` does **not** start the nmap worker. Enable profile `nmap`, set `PIHERDER_NMAP_VULN_PATH` if using the vuln pack, recreate `celery-worker-nmap` after upgrade so `PIHERDER_NMAP_WORKER=1` is live |
| Encrypted secrets / certs | Same **`PIHERDER_MASTER_KEY`** required for restore and DR |
| Discovery ≠ Server | Discovered devices are not fleet members until link/promote; no automatic SSH/backup enable |

Existing v0.7.0 deployments: pull new image / checkout tag, keep `.env` + volumes, `docker compose up -d`. Add nmap only if you want LAN Discovery.

---

## Install

```bash
git clone https://github.com/bjorngluck/piherder.git
cd piherder
git checkout v0.8.0
cp .env.example .env
# set PIHERDER_MASTER_KEY (Fernet) and SECRET_KEY — never use compose defaults in production
docker compose up -d
```

Optional pin:

```bash
export PIHERDER_IMAGE=bjorngluck/piherder:0.8.0
docker compose up -d
```

### Optional: LAN Discovery (nmap worker)

```bash
# In .env: ensure PIHERDER_NMAP_VULN_PATH if you want the vuln pack volume
docker compose --profile nmap up -d
```

See [Install § optional nmap](https://piherder-docs.hacknow.info/getting-started/install/) · [LAN Discovery](https://piherder-docs.hacknow.info/integrations/lan-discovery/).

Docs: [Install](https://piherder-docs.hacknow.info/getting-started/install/) · [README](../README.md)

### Upgrade from v0.7.0

```bash
# 1) Self-backup + confirm PIHERDER_MASTER_KEY is safe offline
git fetch --tags
git checkout v0.8.0
docker compose pull
docker compose up -d
# Optional LAN Discovery:
# docker compose --profile nmap up -d
# docker compose up -d --force-recreate celery-worker-nmap   # if nmap fence env changed
```

Migrations run on web startup. Review **Integrations → LAN Discovery** and Settings → **Stale data cleanup** if you enable those features.

---

## Package version

`pyproject.toml` / `APP_VERSION` → **`0.8.0`** *(set on tag day; currently `0.8.0.dev0` on main)*

---

## Docs & tests

| Doc | Role |
|-----|------|
| [ADMIN.md](ADMIN.md) | Operator / deploy (incl. nmap fence) |
| [API.md](API.md) | REST `/api/v1` |
| [PUBLISH_IMAGE.md](PUBLISH_IMAGE.md) | Multi-arch publish process |
| [PLAN_v0.8.0.md](PLAN_v0.8.0.md) | RC3 ship plan |
| [FEATURE_PLAN_LAN_NMAP.md](FEATURE_PLAN_LAN_NMAP.md) | LAN Discovery design |
| [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) | Horizon roadmap |
| Wiki | https://piherder-docs.hacknow.info/ |

**Unit tests:** full `tests/` pack at freeze (~**49%** line coverage pre-tag; CI fail-under **30%**).  
**E2E:** Playwright Chromium in `e2e/` (0.7 base + RC3 extensions).

**Notable tests:** `test_nmap_*` · `test_http_smoke` · `test_http_seeded_surfaces` · `test_coverage_rc3_pure` · `test_stale` / cleanup · e2e shell + wizard + B6

---

## Verify after upgrade

1. `docker compose ps` — web healthy; image `bjorngluck/piherder:…`
2. About page shows **0.8.0**
3. **Add server** wizard still works (0.7 path)
4. **Without** nmap profile: app runs; no nmap worker required
5. **With** `--profile nmap`: Integrations → LAN Discovery → worker online after first scan/heartbeat; configure CIDR; run discovery; Network tab + Hosts map outer chips when enabled
6. `PIHERDER_NMAP_WORKER`: web container shows `0`; nmap worker shows `1` (`docker compose exec … env | grep NMAP`)
7. Settings → Stale data cleanup (if enabled) · Jobs Cancel · audit trail
8. Wiki builds: `mkdocs build --strict` after screenshot pack lands

---

## Freeze checklist (maintainer)

- [ ] Screenshot PNG pack (stream A) + wiki truth
- [ ] Unit + E2E green; coverage ~50%
- [ ] `mkdocs build --strict`
- [ ] Bump `pyproject.toml` + `APP_VERSION` → `0.8.0`
- [ ] Finalize this file (Date, Status, package version)
- [ ] Tag `v0.8.0` + Hub multi-arch (`0.8.0` / `0.8` / `latest`)

---

## Changelog (draft sources)

Product commits since `v0.7.0` include nmap foundation through N10 (map identity, dual Hosts layout, worker fence), fabric map polish, stale cleanup, E2E/CI hardening, and RC3 unit coverage growth. Full history: `git log v0.7.0..v0.8.0`.
