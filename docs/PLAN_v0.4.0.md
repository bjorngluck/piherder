# PiHerder v0.4.0 — release plan

**Status:** Active — Phase 1 of production path  
**Date:** 2026-07-12  
**Baseline:** `v0.3.0` (Grafana + Kuma hub)  
**Package version at tag:** `0.4.0` (`pyproject.toml`)  
**Related:** [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · [FEATURE_PLAN_TEMPLATES.md](FEATURE_PLAN_TEMPLATES.md) · [RELEASE_v0.4.0.md](RELEASE_v0.4.0.md) · [SPEC.md](../SPEC.md)

### Decision (locked)

| Choice | Value |
|--------|--------|
| Next git tag | **`v0.4.0`** only (no separate `v0.3.1`) |
| Destination for post-0.3 commits | `main` → included when `v0.4.0` is cut |
| Release notes source | **§ Bug list** + features below + [RELEASE_v0.4.0.md](RELEASE_v0.4.0.md) |
| How to add new bugs | Append a row to the table (id, area, status, commit when fixed) |
| Production path | **3 phases:** v0.4.0 (templates) → v0.4.x (drift/secrets/NPM) → v0.5.0 RC |

---

## 1. Commits on `main` since `v0.3.0`

Update with `git log --oneline v0.3.0..HEAD` before tagging.

| Commit | Summary |
|--------|---------|
| `ba56c2b` | fix(docker): surface deploy pull/up results (was silent success) |
| `d33f286` | fix(docker): resolve container update alert after successful deploy |
| `538e7e2` | docs: draft PLAN_v0.4.0 … |
| `069b065` | fix: jobs list cancel and backup-failed alert dismiss/resolve |
| `4686009` | docs: track all post-0.3 fixes for v0.4.0 release notes |

---

## 2. Bug / fix list (living — for release notes)

Status: **fixed** = on `main`, will ship in v0.4.0 · **open** = still to do · **wontfix** = deferred with note.

| ID | Area | Issue | Status | Commit(s) | Release-notes line (draft) |
|----|------|--------|--------|-----------|----------------------------|
| **B01** | Docker Deploy | Deploy always audited as success; pull/up output discarded; no result banner; paths unquoted | **fixed** | `ba56c2b` | Deploy records pull/up exit codes and command output in audit, shows success/fail banner |
| **B02** | Docker / alerts | “Update available” / `container_updates` alert stayed open after successful Deploy | **fixed** | `d33f286` | Successful Deploy clears pending stack badge and resolves container-update alert when none remain |
| **B03** | Docker UX | Check updates only pulls; operators expected containers to update (docs/banner) | **fixed** (docs/UI) | `ba56c2b` | UI clarifies Check updates = pull only; Deploy = pull + `up -d` |
| **B04** | Jobs | List **Cancel** button did nothing; modal Cancel worked (`stopPropagation` blocked handler) | **fixed** | `069b065` | Jobs list Cancel works (capture-phase handler) |
| **B05** | Notifications | Backup-failed alert could stay open after successful backup (`_finish` early-return skipped resolve) | **fixed** | `069b065` | Successful backup always resolves `backup_failed` alert |
| **B06** | Notifications | Dismiss returned 404 if alert already resolved/dismissed | **fixed** | `069b065` | Dismiss is idempotent for already-closed alerts |
| **B07** | Docker | Deploy/Check updates are sync SSH (no Job row) — long pulls opaque | **open** (stretch → 0.4.x) | — | *Optional:* run as Jobs with live log |
| **B08** | Backup / DR | Service logo files not in herder self-backup | **open** (→ 0.4.x) | — | *Optional:* pack `service_logos/` in self-backup |
| **B09** | Notifications | No push when an alert auto-resolves | **open** (stretch) | — | *Optional:* silent resolve only (current) |

### Operator behaviour (after B01–B03)

| Action | Effect |
|--------|--------|
| **Check updates** | `docker compose pull` only; badge/alert if new layers; **does not** recreate containers |
| **Deploy** | Optional pull + `up -d`; audit with pull_rc/up_rc + output; banner; clear stack pending; resolve alert if none left |

---

## 3. Theme for v0.4.0 (Phase 1)

**Templates foundation + post-0.3 quality**

Versioned service templates: compose recipe + variables (incl. boolean/volume) + post-deploy checklist (manual DNS).  
Wizard: preview → confirm → audit; **wait modal** during SSH deploy. Desired config **V1** with Fernet-encrypted secrets + step-up 2FA. From-host templatize.

Full roadmap (drift, Docker secrets, NPM connector, git catalog, restore+config, wiki, Docker Hub): see [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) and session plan (Phase 2 = v0.4.x, Phase 3 = v0.5.0 RC).

Detail: [FEATURE_PLAN_TEMPLATES.md](FEATURE_PLAN_TEMPLATES.md).

---

## 4. v0.4.0 ship bar

1. **Quality** — B01–B06 fixed  
2. **Templates v1** — schema, builtin catalog, import own, apply-to-host wizard  
3. **OOTB pack** — NPM, Uptime Kuma, Pi-hole, Grafana (volume-aware)  
4. **Desired state V1** — encrypted secrets; view/edit + redeploy  
5. **Host picker** — Docker-enabled hosts + inventory project/container counts  
6. **Secrets UX** — step-up 2FA unlock; optional setting for template deploy / secrets  
7. **Variables** — boolean + volume modes; from-host parameterization  
8. **Deploy feedback** — wait modal on preview / confirm / redeploy / from-host  
9. **Manual DNS** checklist in templates  
10. **Docs** — ADMIN + FEATURE_PLAN_TEMPLATES + RELEASE + SPEC  
11. **Tests** — render, encrypt, volumes/booleans, from-host parameterize  
12. **Version** — `pyproject.toml` → `0.4.0`; tag `v0.4.0`  

### Explicitly out of v0.4.0 freeze

- Git template catalog pull (v0.4.x)  
- Docker secrets migration (v0.4.x)  
- Drift scheduler (v0.4.x)  
- NPM integration connector (v0.4.x; OOTB **template** only in 0.4.0)  
- Restore last known config (v0.5.0 RC)  
- Docker Hub multi-arch publish (v0.5.0 RC)  
- Automated DNS (post-RC)  

---

## 5. Sequencing

```text
1. Land template engine + OOTB packs + wizard on main
2. Keep appending bugs to §2
3. Freeze: no open P0 bugs
4. Finalise RELEASE_v0.4.0.md
5. Bump pyproject 0.4.0 · tag v0.4.0 · push
```

---

## 6. How to maintain this list

When you find or fix a bug:

1. Add or update a row in **§2** (`Bxx`, area, status, commit).  
2. Add a one-line bullet under **Fixed** or **Open** in [RELEASE_v0.4.0.md](RELEASE_v0.4.0.md).  
3. Do **not** open a separate `v0.3.x` release unless we reverse the locked decision above.

```bash
git log --oneline v0.3.0..HEAD
```

---

**End of plan** — living until `v0.4.0` is tagged.
