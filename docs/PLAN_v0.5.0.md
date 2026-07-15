# PiHerder v0.5.0 — development plan

**Status:** **QA / release prep** (feature work for RC substantially complete; operator QA then freeze)  
**Date opened:** 2026-07-12  
**Last plan refresh:** 2026-07-15 (dep lock for RC; PyJWT; Catalog certs / Services polish)  
**Baseline:** `v0.4.0` (templates foundation + post-0.3 quality)  
**Package version on main:** `0.5.0.dev0` (`pyproject.toml`) — bump to `0.5.0` at tag  
**Related:** [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · [FEATURE_PLAN_TEMPLATES.md](FEATURE_PLAN_TEMPLATES.md) · [FEATURE_PLAN_PIHOLE_NPM_CERTS.md](FEATURE_PLAN_PIHOLE_NPM_CERTS.md) · [RELEASE_v0.4.0.md](RELEASE_v0.4.0.md) · [SPEC.md](../SPEC.md) · Wiki: [Network maps](../wiki/integrations/dns-fabric.md)

### Decision (locked)

| Choice | Value |
|--------|--------|
| Git tag target | **`v0.5.0`** (first RC) |
| Planning frame | **Single target** — former v0.4.x ops + RC polish land in this cycle |
| Intermediate tags | Optional only if something must ship early; not the plan structure |
| Production path | ~~v0.4.0~~ **done** → **v0.5.0 QA → tag** → v1.0 |
| **GitHub visibility** | Stay **private** while developing; **make public at RC** (enables free GitHub Pages) |
| **Wiki / Pages go-live** | **Live** — https://bjorngluck.github.io/piherder/ (repo public; `gh-pages` branch deploy) |
| **License** | **PolyForm Noncommercial 1.0.0** (source-available, non-commercial). Copyright Bjorn Gluck. Commercial use = separate grant. Not OSI open source. |

### GitHub Pages

- **Live:** [https://bjorngluck.github.io/piherder/](https://bjorngluck.github.io/piherder/)  
- **Source:** `wiki/` + `mkdocs.yml` on `main`  
- **Publish path today:** force-push built site to **`gh-pages`** (branch Pages). Optional later: switch Pages source to **GitHub Actions** (`.github/workflows/docs.yml`).

---

## 1. Theme

**First RC** — templates become production-trustworthy (ops depth + UX polish + restore), install story completes (multi-arch image + wikis), freeze bar holds.

---

## 2. Workstreams (implementation order)

### A — Template polish (UX trust)

| Item | Notes | Status |
|------|--------|--------|
| Redeploy volume editor | Mode + name/path on deployment page (no full re-wizard) | **Done** |
| From-host edge cases | Multi-file notes, missing `.env` / `.env.example`, clearer path errors | **Done** |
| Operator feedback | Post-redeploy Docker/Audit links; clearer failure banners | **Done** |

### B — Desired-state ops (was “v0.4.x”)

| Item | Notes | Status |
|------|--------|--------|
| Config **drift** schedule | Host compose/.env vs desired state; alert + audit; manual Check + every 6h | **Done** |
| `.env` migrate UX | **Import host .env** → encrypted secrets / public vars | **Done** |
| **Git** template catalog pull | Preview before enable; operator-owned after import | Nice-to-have |
| **NPM connector** | Proxy hosts, bindings, encrypted certs | **Done** (workstream F) |

### C — Restore + last known config

| Item | Notes | Status |
|------|--------|--------|
| Restore service from backup | Matching backup sources listed on deployment; use server Backups dry-run/apply | **Done** (guided) |
| Apply last known config from PiHerder | **Apply last known config** re-writes stored desired state + compose up | **Done** |

### D — RC packaging & docs

| Item | Notes | Status |
|------|--------|--------|
| Production user wiki + dev wiki | **Live** at github.io; source `wiki/`; real screenshots ongoing | Open (polish) |
| Docker Hub / GHCR multi-arch image | [PUBLISH_IMAGE.md](PUBLISH_IMAGE.md); tags for `0.5.0` | Open |
| RC freeze bar | pytest, smoke (deploy + drift + restore), secret-path review | Open |

### E — Fleet ops polish (v0.5.0 track)

Operator friction fixes and multi-host workflows that do not depend on templates.

| Item | Notes | Status |
|------|--------|--------|
| **Exclusive OS/container jobs** | One active job of each type per host (`os_patch`, `container_patch`, checks). UI/API return **409** + existing `job_id`; JobHold follows the running job. Celery multi-slot does **not** re-run container jobs (those are web-process). | **Done** |
| **Reboot hang fix** | Deferred background reboot (`sleep 1` + least-priv reboot path) + timeout SSH close so the request finishes — especially when rebooting the PiHerder host itself. | **Done** |
| **Servers list bulk actions** | Select one/many/all → Check OS, Upgrade OS, Check containers, Patch containers, Backup. Feature-flag aware (Docker-off hosts not queued for container actions). `POST /servers/bulk`. | **Done** |
| **Docker full editor links** | Quick edit + **Full editor…** in ⋯ menu; reliable navigation from quick-edit modal; URL-encoded project paths. | **Done** |
| **Backup complete audit** | Celery success path was skipping terminal audit (stale Session identity map). Now records compact `backup` success/fail with source count + sizes; job wall-clock for duration. | **Done** |
| **App timezone display** | Settings timezone applied consistently: Audit/Jobs/Notifications/server list & detail; ISO strings + naive UTC parsed as UTC; client `data-utc` treats naive as UTC. | **Done** |
| **Server detail UX** | Remove → Edit **Remove** tab; host deps checks under SSH access (Test connection also probes deps); Grafana + Kuma SSH cards in dest-card grid with Host status; equal desktop card sizing. | **Done** |
| **Nav: bell vs Alerts** | Dropped Alerts nav link; notifications via bell only. | **Done** |
| **Catalog nav** | **Catalog** → `/catalog` → **Integrations \| Certificates \| Templates \| Network** (shared `settings-tab-btn`). | **Done** |
| **Grafana preferred name** | Integration-level `display_names[uid]` edited only on **Inventory** tab; binding rows Clone/Remove only; new binds inherit; poll preserves. | **Done** |
| **Users create modal** | Create user + one-time credentials confirmation in modals. | **Done** |
| **Server audit footer** | Host detail: All / Backup / Docker / OS audit log deep links. | **Done** |
| **Docs sync (UX batch)** | Wiki + ADMIN/SPEC/README aligned: Catalog, preferred names, Remove tab, SSH deps, bell-only alerts, dest cards. | **Done** |

**Tests:** `tests/test_job_exclusive.py` · `tests/test_audit_format.py` · `tests/test_app_settings.py` · `tests/test_request_ip_audit.py`  
**Wiki:** [Updates & patching](../wiki/day-to-day/updates-and-patching.md) · [Jobs / Audit](../wiki/day-to-day/jobs-audit-notifications.md) · [Compose edit](../wiki/docker/compose-edit.md) · [Multi-worker](../wiki/operations/multi-worker.md)

### F — Ecosystem connectors (primary for RC — 2026-07-13)

Elevated from nice-to-have / out-of-scope: **Pi-hole + NPM + TLS cert ops** are **must-have** for v0.5.0. Template polish / drift / restore may slip if schedule conflicts. Detail: [FEATURE_PLAN_PIHOLE_NPM_CERTS.md](FEATURE_PLAN_PIHOLE_NPM_CERTS.md).

| Item | Notes | Status |
|------|--------|--------|
| **Pi-hole v6 multi-instance** | Connect N instances; stats poll; deep links; primary flag; multi summary | **Done** |
| **Pi-hole DNS/CNAME** | List on primary path; add/remove fans out to all enabled instances | **Done** |
| **Pi-hole actions** | Update Gravity, Restart DNS, Flush network (audited) | **Done** |
| **NPM connector** | Token auth; proxy hosts RO + bind to server/docker; cert inventory | **Done** |
| **Managed certificates** | NPM pull **or PEM upload**; encrypted fullchain+key; metadata UI | **Done** |
| **Cert deploy targets** | pair / combined / pfx; perms/owner; post-deploy command via SSH | **Done** |
| **Cert renew loop** | ≤21d → NPM renew → poll → distribute; scheduler every 6h | **Done** |
| **Herder backup** | Includes managed certs + targets | **Done** |
| **Docs / wiki** | Feature plan + wiki pages + PLAN/SPEC/ROADMAP | **Done** (refreshed 2026-07-15) |
| **Network maps (DNS fabric)** | See § F.1 below | **Done** — **in operator QA** |

#### F.1 — Network maps / DNS fabric (detail, updated 2026-07-15)

| Capability | Notes | Status |
|------------|--------|--------|
| Host DNS | `Server.dns_name` / `dns_manage_a` / IP; Edit → General; Pi-hole A fan-out; duplicates = ok | **Done** |
| Service mappings | `ServiceDnsRecord` — CNAME or **host identity** (`record_type=a` when name = host FQDN) | **Done** |
| Adopt existing | Catalog → **Network**: **Import all from Pi-hole**; HTMX candidates; host-identity map | **Done** |
| Path resolution | NPM proxy_host + Kuma service + deployments → project/container layers | **Done** |
| Network settings | LAN CIDR, gateway IP, public WAN IP, lookup-public-ip; app settings keys | **Done** |
| Hosts map topology | Internet → Router → LAN → home hosts; **cloud** hosts (public IP / outside CIDR) → Internet; RFC1918 fallback when no CIDR; spine always drawn | **Done** |
| Kuma on infra | Optional Router + Public IP monitor dropdowns; status chip + Open in Kuma | **Done** |
| Layout polish | LAN ring top-gap (no host on Router→LAN spine); theme-aware Internet/Router/LAN/NPM fills (light mode) | **Done** |
| Focus UX | Path focus **and** node focus (any host incl. Nomad w/o services, Router, LAN, Internet); Open host / Open in Kuma; copy path; touch lock | **Done** |
| Map chrome | viewBox pinch/zoom to 500%, pan, +/−/1:1; mobile list-first + View full map; filters; status dots | **Done** |
| Catalog label | UI **Network** (URL `/dns*` kept) | **Done** |
| External DNS | Checklist only (Cloudflare automation = post-0.5) | **Done** |
| Tests / CI | `tests/test_dns_fabric.py` · `.github/workflows/test.yml` | **Done** |
| Wiki / docs | [Network maps](../wiki/integrations/dns-fabric.md) · ADMIN · SPEC · README · ROADMAP | **Done** |

**QA smoke (operator):** set network map (LAN/gateway/public ± Kuma) → host DNS on 2+ servers → import Pi-hole → Hosts map (spine + Nomad cloud + selectable empty hosts) → Path map → sync path card → host identity (e.g. 3D Print) → external checklist · hard-refresh after image rebuild.

### G — Stretch quality (B07–B09)

| ID | Item | Status |
|----|------|--------|
| **B07** | Docker Deploy/Check as Jobs + live log — per-stack `docker_stack_check` / `docker_stack_deploy`; JobHold UI; exclusive per host | **Done** |
| **B08** | Service logos in herder self-backup (`data/service_logos/…`) | **Done** |
| **B09** | Web Push on auto-resolve of alerts (same type prefs; “Resolved: …” payload) | **Done** |

### H — Audit client IP (must-have for RC)

| Item | Notes | Status |
|------|--------|--------|
| **`AuditLog.client_ip`** | Migration `018_audit_client_ip`; indexed column | **Done** |
| **Caddy-correct IP** | Shared `extract_client_ip` (XFF → X-Real-IP → peer); middleware binds request context | **Done** |
| **All audit writers** | `make_audit_log()` + helpers; job `details.client_ip` survives Celery | **Done** |
| **Auth / tokens** | Login, login-failed, 2FA; API token create/update/rotate/revoke audited with IP | **Done** |
| **UI + search** | Audit list/detail show IP; free-text search matches IP | **Done** |
| **Alembic commit fix** | `migrations/env.py` commits after upgrade (prevents silent rollback of stamps) | **Done** |

**Tests:** `tests/test_request_ip_audit.py` · **Wiki:** [Jobs / Audit](../wiki/day-to-day/jobs-audit-notifications.md) · **ADMIN:** API tokens / client IP section

### Stretch / carry (do not block RC)

| ID | Item |
|----|------|
| Phase 5 remainder | HA / Frigate / n8n generic URL integrations |
| Template deploy as Jobs | Wait-modal remains; optional later (beyond stack B07) |
| **Test coverage + Playwright** | Post-RC / post first production — [ROADMAP Quality & platform](ROADMAP_ECOSYSTEM.md#quality--platform-post-rc--post-10-first-production) (phases A/B/C) |
| **Dep lock in Docker/CI** | `uv.lock` + `requirements.lock.txt` (hashes); Dockerfile/`test.yml` install frozen | **Done** |
| **pip-audit in CI** | Optional workflow step / Dependabot | Open (post-tag ok) |
| **JWT → PyJWT** | Drop `python-jose`/`ecdsa`; sessions via PyJWT HS256 | **Done** |
| **Custom branding** | Far horizon — not post-RC 1.0 |

### UX (this cycle, non-blocking polish)

| Item | Notes | Status |
|------|--------|--------|
| Catalog **Certificates** tab | First-class Catalog section; list shows maps + expiry summary | **Done** (2026-07-15) |
| Fleet **Services** polish | Filter All/Up/Down/TLS; search; clearer empty state; Docker deep link | **Done** (2026-07-15) |
| Repo cruft cleanup | Removed `Caddyfile.old`, `THEMING_VARIABLES.md`, `UI_UNIFICATION_PLAN.md` | **Done** |

---

## 3. Ship bar (`v0.5.0` tag)

Must-have:

1. Template UX polish (redeploy volumes + critical from-host fixes) — **Done** (workstream A)
2. Drift detection (scheduled + alert/audit) — **Done** (workstream B; every 6h + manual)
3. Restore service + last-known-config path — **Done** (workstream C; apply config + backup match list)
4. Production user wiki + maintained ADMIN/dev notes; **repo public + GitHub Pages live** (or documented blocker)
5. Multi-arch image publish path exercised **or** documented credentials blocker (compose-build remains primary)
6. `pytest` green; smoke checklist in release notes
7. Secrets paths reviewed (step-up 2FA, no cleartext in audit, host `.env` mode `600`)
8. **Audit client IP** on every request-driven event (Caddy XFF; workstream **H**) — **Done**
9. Version `0.5.0` + git tag + [RELEASE_v0.5.0.md](RELEASE_v0.5.0.md) (at freeze)

**Nice-to-have in the same tag:** git catalog. **B07–B09** and **audit IP** landed. Prefer shipping if clean; do not slip RC forever for the full laundry list.

**Must-have (workstream F):** Pi-hole multi-instance + DNS fan-out, NPM RO + cert pull, managed certs (upload + NPM), deploy targets, renew loop, **DNS fabric** — **Done** (QA).

### Explicitly out of v0.5.0

- Advanced secret stores (Swarm / vault / sealed) — Horizon 3  
- Create/edit proxy hosts in NPM; redirect/stream/404 hosts  
- Pi-hole v5 API; Cloudflare DNS **automation** (checklist + fabric ship in 0.5; provider API later)  
- Service migrate host→host; destructive service wipe (compose down -v)  
- First-class **container dependency graph** (app → DB/Redis) — [ROADMAP H2.5](ROADMAP_ECOSYSTEM.md)  
- Topology export plugins (Mermaid / Cytoscape) — H2.5/H3  
- ACME / Let’s Encrypt inside PiHerder (use NPM or external)  
- Large curated pack expansion (Frigate, HA, n8n, media…)  
- Kubernetes / bare install  
- Multi-tenant orgs  
- Optional AI  

### QA / freeze checklist (operator)

- [ ] DNS fabric smoke (F.1)  
- [ ] Template deploy + drift + apply last known config  
- [ ] Pi-hole Actions → job + audit  
- [ ] Cert list / target deploy (if used)  
- [ ] `pytest` green in container  
- [ ] Secrets path review (step-up 2FA, no cleartext audit, host `.env` 600)  
- [ ] Multi-arch / Hub publish **or** documented blocker  
- [ ] `RELEASE_v0.5.0.md` + version bump + git tag `v0.5.0`

---

## 4. Baseline commits

```bash
git log --oneline v0.4.0..HEAD
```

**Baseline tag:** `v0.4.0` — [RELEASE_v0.4.0.md](RELEASE_v0.4.0.md)

### Landed in this cycle (non-template)

| Area | Summary |
|------|---------|
| Exclusive jobs | Per-server exclusive OS/container patch + update-check jobs; 409 reuse |
| Reboot | Deferred background reboot; no hang on herder host |
| Bulk fleet actions | Server list multi-select + bulk check/patch/backup |
| Docker editor UX | Full editor links + ⋯ **Full editor…** |
| Backup audit | Terminal backup audit always recorded; size summary in trail |
| Timezone UI | App timezone (e.g. SAST) for Audit, Jobs, Notifications, fleet times |
| B07 stack jobs | Per-stack Check updates / Deploy as Jobs + JobHold live log |
| B08 logos backup | Service logos packed in herder self-backup / restore |
| B09 resolve push | Web Push when alerts auto-resolve (type prefs) |
| Audit client IP (H) | `client_ip` on all request audits; login/token audits; Celery keeps queue IP; Alembic commit fix |
| Pi-hole / NPM / certs | Workstream F (multi Pi-hole, NPM RO, managed certs, renew) |
| Network maps (DNS fabric) | Host A + service mappings; adopt Pi-hole; Hosts/Path maps; LAN/cloud/Internet spine; Kuma infra; node+path focus |
| Catalog Certificates + Services UX | Certificates as Catalog tab; list maps/expiry; fleet Services filters + search |
| A template polish | Volume editor on redeploy; from-host edges; post-redeploy links |
| B drift + env migrate | Scheduled/manual drift; import host `.env` into encrypted SoT |
| C restore / last config | Apply last known config; backup sources matched on deployment |

---

## 5. Docs map

| Doc | Role |
|-----|------|
| This file | Living ship plan |
| [FEATURE_PLAN_TEMPLATES.md](FEATURE_PLAN_TEMPLATES.md) | Schema + security model (foundation frozen; polish here) |
| [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) | Multi-horizon roadmap |
| [SPEC.md](../SPEC.md) | Phase 6 → v0.5.0 checklist |
| [ADMIN.md](ADMIN.md) | Operator guide (extend as features land) |
| [API.md](API.md) | REST `/api/v1` (409 exclusive jobs) |
| [PUBLISH_IMAGE.md](PUBLISH_IMAGE.md) | Multi-arch publish process |
| User wiki (`wiki/`) | Day-to-day + ops pages (MkDocs → GitHub Pages) |
| [RELEASE_v0.5.0.md](RELEASE_v0.5.0.md) | Written at freeze only |

---

**End of plan** — update statuses as work lands; freeze into RELEASE notes at tag.
