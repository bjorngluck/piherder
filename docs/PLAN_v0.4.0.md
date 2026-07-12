# PiHerder v0.4.0 — release plan

**Status:** **Frozen / released** as `v0.4.0`  
**Date:** 2026-07-12  
**Baseline:** `v0.3.0` (Grafana + Kuma hub)  
**Package version at tag:** `0.4.0` (`pyproject.toml`)  
**Related:** [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · [FEATURE_PLAN_TEMPLATES.md](FEATURE_PLAN_TEMPLATES.md) · [RELEASE_v0.4.0.md](RELEASE_v0.4.0.md) · [SPEC.md](../SPEC.md)

### Decision (locked)

| Choice | Value |
|--------|--------|
| Git tag | **`v0.4.0`** |
| Post-0.3 work | Shipped on `main` in this release |
| Release notes | [RELEASE_v0.4.0.md](RELEASE_v0.4.0.md) |
| Production path | **v0.4.0 (done)** → v0.4.x (ops) → **v0.5.0** (polish + RC) |

---

## 1. Commits on `main` since `v0.3.0`

```bash
git log --oneline v0.3.0..v0.4.0
```

| Commit | Summary |
|--------|---------|
| `ba56c2b` | fix(docker): surface deploy pull/up results |
| `d33f286` | fix(docker): resolve container update alert after deploy |
| `538e7e2` | docs: draft PLAN_v0.4.0 |
| `069b065` | fix: jobs list cancel + backup-failed dismiss/resolve |
| `4686009` | docs: track post-0.3 fixes |
| `c8463c5` | feat(templates): service templates v1 |
| *(release freeze)* | docs + `pyproject` `0.4.0` + tag |

---

## 2. Bug / fix list (frozen at release)

| ID | Area | Issue | Status | Commit(s) |
|----|------|--------|--------|-----------|
| **B01** | Docker Deploy | Silent success; no pull/up output | **fixed** | `ba56c2b` |
| **B02** | Docker / alerts | Update badge/alert stuck after Deploy | **fixed** | `d33f286` |
| **B03** | Docker UX | Check updates vs Deploy confusion | **fixed** | `ba56c2b` |
| **B04** | Jobs | List Cancel no-op | **fixed** | `069b065` |
| **B05** | Notifications | backup_failed not resolved | **fixed** | `069b065` |
| **B06** | Notifications | Dismiss 404 if already closed | **fixed** | `069b065` |
| **B07** | Docker | Deploy/Check as Jobs + live log | **deferred** → 0.4.x | — |
| **B08** | Backup | Logos not in herder self-backup | **deferred** → 0.4.x | — |
| **B09** | Notifications | Push on auto-resolve | **deferred** (stretch) | — |

---

## 3. Theme (shipped)

**Templates foundation + post-0.3 quality**

Versioned service templates: compose + variables (boolean/volume) + checklist; wizard with wait modal; desired state V1; step-up 2FA secrets; from-host templatize; OOTB NPM/Kuma/Pi-hole/Grafana.

Detail: [FEATURE_PLAN_TEMPLATES.md](FEATURE_PLAN_TEMPLATES.md) · notes: [RELEASE_v0.4.0.md](RELEASE_v0.4.0.md).

---

## 4. Ship bar — all complete at tag

1. ~~Quality B01–B06~~  
2. ~~Templates v1~~  
3. ~~OOTB pack (volume-aware)~~  
4. ~~Desired state V1~~  
5. ~~Host picker~~  
6. ~~Secrets UX (step-up + setting)~~  
7. ~~Boolean + volume; from-host~~  
8. ~~Deploy wait modal~~  
9. ~~Manual DNS checklist~~  
10. ~~Docs~~  
11. ~~Tests~~  
12. ~~Version `0.4.0` + tag~~  

### Explicitly out of v0.4.0 (still true)

- Git template catalog · drift scheduler · NPM connector · Docker secrets migration → **v0.4.x**  
- Template UX polish · restore + last config · wikis · multi-arch → **v0.5.0**  
- Automated DNS → post-RC  

---

## 5. After this freeze

| Next | Focus |
|------|--------|
| **v0.4.x** | Drift, git catalog, NPM connector, optional async deploy jobs (B07), B08 |
| **v0.5.0** | Template polish, restore + config, production docs, RC bar |

---

**End of plan** — historical record for `v0.4.0`.
