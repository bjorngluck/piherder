# PiHerder v0.4.0 — release plan

**Status:** Active — all post-`v0.3.0` work targets **v0.4.0**  
**Date:** 2026-07-11 (updated)  
**Baseline:** `v0.3.0` (Grafana + Kuma hub)  
**Package version at tag:** `0.4.0` (`pyproject.toml`)  
**Related:** [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · [SPEC.md](../SPEC.md) · [RELEASE_v0.3.0.md](RELEASE_v0.3.0.md) · WIP notes [RELEASE_v0.4.0.md](RELEASE_v0.4.0.md)

### Decision (locked)

| Choice | Value |
|--------|--------|
| Next git tag | **`v0.4.0`** only (no separate `v0.3.1` for these fixes) |
| Destination for post-0.3 commits | `main` → included when `v0.4.0` is cut |
| Release notes source | **§ Bug / fix list** below + [RELEASE_v0.4.0.md](RELEASE_v0.4.0.md) |
| How to add new bugs | Append a row to the table (id, area, status, commit when fixed) |

---

## 1. Commits on `main` since `v0.3.0`

Update with `git log --oneline v0.3.0..HEAD` before tagging.

| Commit | Summary |
|--------|---------|
| `ba56c2b` | fix(docker): surface deploy pull/up results (was silent success) |
| `d33f286` | fix(docker): resolve container update alert after successful deploy |
| `538e7e2` | docs: draft PLAN_v0.4.0 … |
| `069b065` | fix: jobs list cancel and backup-failed alert dismiss/resolve |

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
| **B07** | Docker | Deploy/Check updates are sync SSH (no Job row) — long pulls opaque | **open** (stretch) | — | *Optional:* run as Jobs with live log |
| **B08** | Backup / DR | Service logo files not in herder self-backup | **open** (stretch) | — | *Optional:* pack `service_logos/` in self-backup |
| **B09** | Notifications | No push when an alert auto-resolves | **open** (stretch) | — | *Optional:* silent resolve only (current) |

### Operator behaviour (after B01–B03)

| Action | Effect |
|--------|--------|
| **Check updates** | `docker compose pull` only; badge/alert if new layers; **does not** recreate containers |
| **Deploy** | Optional pull + `up -d`; audit with pull_rc/up_rc + output; banner; clear stack pending; resolve alert if none left |

---

## 3. Roadmap theme for v0.4 (Horizon 2)

From [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) § Horizon 2 and SPEC Phase 6:

> Versioned **templates**: compose/install recipe + variables + post-deploy checklist/actions.  
> Onboard wizard steps: monitoring / DNS / TLS-proxy / feature flags — always **preview → confirm → audit**.

v0.4.0 should pick a **thin vertical slice**, not the entire curated pack + every provider.  
**Quality bar:** all **fixed** rows in §2 ship with the tag even if template work is partial.

---

## 4. Feature candidates (discussion)

### A. Remaining H1 — multi-URL / generic adapters

| Item | Value | Effort | Notes |
|------|-------|--------|-------|
| Generic **URL bookmark** integration | Medium | Low–med | Pi-hole, NPM, HA, Frigate, n8n without full APIs |
| Multi Pi-hole (status/deep link) | Medium | Med | Seed from `PIHOLE_URL` |
| Docs: cert path NPM → n8n → consumers | Low | Low | ADMIN only |
| Kuma **create monitor** | High later | High | Provider actions / later slice |

### B. Service templates — core (Phase 6)

| Slice | Description | Ship bar idea |
|-------|-------------|----------------|
| **B0 Schema + store** | Template model: id, name, variables, files, checklist | DB or repo YAML; import/export |
| **B1 Apply to host** | New Docker project from template: vars → preview → confirm → write + optional deploy | Generic + 1–2 curated |
| **B2 Add-server wizard** | After SSH onboard | Depends on B1 |
| **B3 Post-deploy actions** | Checklist-only first | Safer than auto Kuma create |
| **B4 Full curated pack** | 9 stacks | May spill past 0.4.0 |

**Strawman feature ship bar:** **B0 + B1** + generic web + one real stack; checklist-only post-steps.

### C. Explicitly out of freeze (unless reopened)

- Full curated pack production-ready  
- Cloudflare / NPM automation  
- HA component, AI, Ansible (Phase 7)  
- Multi-arch Hub publish  
- k8s / bare install  

---

## 5. Proposed v0.4.0 ship bar

**Theme:** *Templates foundation + post-0.3 quality (Docker, jobs, alerts)*

1. **Quality** — all **fixed** bugs in §2 (B01–B06)  
2. **Templates v1** — schema, apply-to-server, import/export, ≥2 samples *(if agreed)*  
3. **Docs** — ADMIN + [RELEASE_v0.4.0.md](RELEASE_v0.4.0.md)  
4. **Tests** — cover fixed bugs + template dry-run as applicable  
5. **Version** — `pyproject.toml` → `0.4.0`; tag `v0.4.0`  

**Stretch:** B07 Deploy-as-Job · B08 logo backup · generic URL integration  

---

## 6. Sequencing

```text
1. Keep landing fixes on main; append to §2 bug table + RELEASE_v0.4.0 changelog WIP
2. Feature work (templates or agreed theme)
3. Freeze: no open P0 bugs in §2
4. Finalise RELEASE_v0.4.0.md from §2 + features
5. Bump pyproject 0.4.0 · tag v0.4.0 · push
```

---

## 7. How to maintain this list

When you find or fix a bug:

1. Add or update a row in **§2** (`Bxx`, area, status, commit).  
2. Add a one-line bullet under **Fixed** or **Open** in [RELEASE_v0.4.0.md](RELEASE_v0.4.0.md).  
3. Do **not** open a separate `v0.3.x` release for these unless we reverse the locked decision above.

```bash
# Before cutting the release:
git log --oneline v0.3.0..HEAD
```

---

**End of plan** — living document until `v0.4.0` is tagged.
