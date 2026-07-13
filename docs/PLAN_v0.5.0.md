# PiHerder v0.5.0 — development plan

**Status:** **In development**  
**Date opened:** 2026-07-12  
**Baseline:** `v0.4.0` (templates foundation + post-0.3 quality)  
**Package version on main:** `0.5.0.dev0` (`pyproject.toml`)  
**Related:** [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · [FEATURE_PLAN_TEMPLATES.md](FEATURE_PLAN_TEMPLATES.md) · [RELEASE_v0.4.0.md](RELEASE_v0.4.0.md) · [SPEC.md](../SPEC.md)

### Decision (locked)

| Choice | Value |
|--------|--------|
| Git tag target | **`v0.5.0`** (first RC) |
| Planning frame | **Single target** — former v0.4.x ops + RC polish land in this cycle |
| Intermediate tags | Optional only if something must ship early; not the plan structure |
| Production path | ~~v0.4.0~~ **done** → **v0.5.0 in development** → v1.0 |
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
| Redeploy volume editor | Edit volume modes/paths without full re-wizard | Open |
| From-host edge cases | Odd compose layouts, multi-file, missing `.env` | Open |
| Operator feedback | Clearer errors, post-deploy links; optional async job stream (**B07** stretch) | Open |

### B — Desired-state ops (was “v0.4.x”)

| Item | Notes | Status |
|------|--------|--------|
| Config **drift** schedule | Host compose/env vs `stackdeployment`; alert + audit | Open |
| `.env` migrate UX | Pull existing host secrets into PiHerder encrypted SoT | Open |
| **Git** template catalog pull | Preview before enable; operator-owned after import | Nice-to-have |
| **NPM connector** | Proxy hosts, bindings, encrypted certs | Nice-to-have |

### C — Restore + last known config

| Item | Notes | Status |
|------|--------|--------|
| Restore service from backup | Existing backup sources + path policy | Open |
| Apply last known config from PiHerder | Redeploy desired state V1 after host loss / wipe | Open |

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

**Tests:** `tests/test_job_exclusive.py`  
**Wiki:** [Updates & patching](../wiki/day-to-day/updates-and-patching.md) · [Jobs](../wiki/day-to-day/jobs-audit-notifications.md) · [Compose edit](../wiki/docker/compose-edit.md) · [Multi-worker](../wiki/operations/multi-worker.md)

### Stretch / carry (do not block RC)

| ID | Item |
|----|------|
| **B07** | Docker Deploy/Check as Jobs + live log |
| **B08** | Service logos in herder self-backup |
| **B09** | Web Push on auto-resolve of alerts |
| Phase 5 remainder | Multi Pi-hole / generic URL integrations (capacity permitting) |

---

## 3. Ship bar (`v0.5.0` tag)

Must-have:

1. Template UX polish (redeploy volumes + critical from-host fixes)
2. Drift detection (scheduled + alert/audit) — or explicit deferral documented if scope blows up
3. Restore service + last-known-config path (happy path tested)
4. Production user wiki + maintained ADMIN/dev notes; **repo public + GitHub Pages live** (or documented blocker)
5. Multi-arch image publish path exercised **or** documented credentials blocker (compose-build remains primary)
6. `pytest` green; smoke checklist in release notes
7. Secrets paths reviewed (step-up 2FA, no cleartext in audit, host `.env` mode `600`)
8. Version `0.5.0` + git tag + [RELEASE_v0.5.0.md](RELEASE_v0.5.0.md) (at freeze)

**Nice-to-have in the same tag:** git catalog, NPM connector, B07, B08. Prefer shipping if clean; do not slip RC forever for the full laundry list.

### Explicitly out of v0.5.0

- Advanced secret stores (Swarm / vault / sealed) — Horizon 3  
- Automated DNS (Pi-hole / Cloudflare)  
- Large curated pack expansion (Frigate, HA, n8n, media…)  
- Kubernetes / bare install  
- Multi-tenant orgs  
- Optional AI  

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
