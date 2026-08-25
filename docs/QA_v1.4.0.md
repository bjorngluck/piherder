# PiHerder v1.4.0 — operator QA / sign-off

**Branch:** `v1.4.0-dev` → `main` · tag **`v1.4.0`** (cut after merge)  
**Code freeze:** not yet — train opened 2026-08-25.  
**Package:** stays **`1.3.0`** until freeze bump.

This file is **maintainer-only** (repo `docs/`). It is **not** published on the operator wiki.

Plan: [PLAN_v1.4.0.md](PLAN_v1.4.0.md) · design: [FEATURE_PLAN_SERVICE_MIGRATION.md](FEATURE_PLAN_SERVICE_MIGRATION.md).

1.3 production sign-off stays [QA_v1.3.0.md](QA_v1.3.0.md) (historical). Fill operator clicks at feature freeze — not at train open.

---

## How to run this (at freeze)

| | |
|--|--|
| **Instance** | Rebuilt **`v1.4.0-dev`** stack (`docker compose build web && docker compose up -d`). About / footer show **1.4.0** after the version bump |
| **Browsers** | Desktop Chrome or Firefox **and** one phone |
| **Accounts** | One **admin**, one **operator** (2FA enrolled), one **viewer** |
| **Hosts** | At least **two** real SSH Docker hosts + one HAOS (refuse) + one locked hardware-bound project |
| **Flags** | `PIHERDER_SERVICE_MIGRATE` default **off** until M is complete enough; demo never copies |

---

## Must (stub — expand at freeze)

- [ ] **M1** Host lock + HAOS refuse  
- [ ] **M2** Preflight (arch, ports, disk, exclusive, NPM match)  
- [ ] **M3–M7** Direct migrate (Grafana/Authentik-class)  
- [ ] **M-npm** NPM-fronted migrate (`forward_host` updates; CNAME stays on NPM)  
- [ ] **M8** leftover `compose down` keep volumes  
- [ ] **M9** `devices:` warning + lock-or-acknowledge  
- [ ] **D-F** demo simulated Files (no SFTP)  
- [ ] Viewer 403; demo does not copy  
- [ ] 1.3 regression (policy, Files, console, Reports)

## Should (stub)

- [ ] **M-rm** source remove + volume delete (preview + danger confirm)
