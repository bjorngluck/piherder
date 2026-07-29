# PiHerder v1.1.0 — elevate production

**Status:** **Active** — branch `v1.1.0-dev`  
**Date opened:** 2026-07-29  
**Git branch:** `v1.1.0-dev` (integration) · merge → `main` at freeze → tag `v1.1.0`  
**Package / image version (at tag):** `1.1.0`  
**Theme:** Elevate production — certs · discovery · identity · operator UX · topology/maps · integrations/API  
**Baseline:** `v1.0.0` (first production — 2026-07-28)  
**Related:** [RELEASE_v1.0.0.md](RELEASE_v1.0.0.md) · [PLAN_v1.0.0.md](PLAN_v1.0.0.md) · [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · [FEATURE_PLAN_PIHOLE_NPM_CERTS.md](FEATURE_PLAN_PIHOLE_NPM_CERTS.md) · [FEATURE_PLAN_LAN_NMAP.md](FEATURE_PLAN_LAN_NMAP.md) · [FEATURE_PLAN_RUNTIME_TOPOLOGY.md](FEATURE_PLAN_RUNTIME_TOPOLOGY.md) · [FEATURE_PLAN_INTEGRATIONS.md](FEATURE_PLAN_INTEGRATIONS.md) · [FEATURE_PLAN_IAM_2FA_UPDATES_NOTIFICATIONS.md](FEATURE_PLAN_IAM_2FA_UPDATES_NOTIFICATIONS.md) · [ADMIN.md](ADMIN.md) · [API.md](API.md) · [SECURITY.md](../SECURITY.md)

> **First minor after production.** Elevate what operators already run. **Focus · polish · discover · pull in by capacity · defer enhanced work to v1.2 / v1.3 paths.** Keep `main` patchable for **v1.0.x** while this train runs on `v1.1.0-dev`.

---

## 0. Decision lock

| Choice | Value |
|--------|--------|
| Integration branch | **`v1.1.0-dev`** |
| Production line | **`main` @ `v1.0.0`** — hotfixes → **`v1.0.x`**, port into `v1.1.0-dev` |
| Git tag (freeze) | **`v1.1.0`** |
| Image tags (freeze) | `1.1.0` · `1.1` · `latest` (multi-arch); keep `1.0` / `1.0.x` pins valid |
| In-scope streams | **A** certs · **B** discovery · **C** identity · **D** operator UX · **G** topology/maps · **I** integrations/API |
| Out-of-focus | **E** templates mega · **F** host lifecycle mega · **H** HA REST/path2 · k8s/bare/branding → **v1.2 / v1.3** |
| Mode | Focus · polish · discover · pull-in · defer by time |
| Coverage | **≥ 55%** unit; CI fail-under **55** |
| E2E | Touched surfaces get basic Playwright; no live SSH/nmap/NPM in CI |
| Semver | Additive minor; no silent contract breaks |
| Version bump | `1.1.0` **at freeze only** |

```text
main @ v1.0.0 (+ v1.0.x patches)
  └─ v1.1.0-dev → merge → main → tag v1.1.0 → Hub
```

| Rule | Practice |
|------|----------|
| Must → Should → Discover | Do not start Discover while Must is open |
| Time-box Discover | Spike → promote or defer with RELEASE note |
| No half-built paths | Complete or do not ship |
| Prod critical bugs | **main** as **1.0.x** first |
| Enhanced themes | **v1.2** or **v1.3** paths (§6) |

---

## 1. Goal

1. **A — Certs:** guided distribute + correct sudoers + actionable deploy errors.  
2. **B — Discovery:** last-seen, hide, purge, filters.  
3. **C — Identity:** password policy + trusted-device detail; discover SMTP.  
4. **D — Operator UX:** human-readable schedules, favourites, cross-host jump.  
5. **G — Maps/fabric:** ports clarity + cross-host edge polish.  
6. **I — Integrations/API:** OpenAPI/bearer test UX + generic URL entries.  
7. Docs + quality freeze → merge → tag → publish.

---

## 2. Ship bar

| # | Stream | Item | Tier | Status |
|---|--------|------|------|--------|
| 1–3 | A | **P** wizard · **P-sudo** · **P-fb** errors | Must | **In progress** (A1.1 path helpers + sudoers/deploy alignment landed) |
| 4–7 | B | **S1–S4** last-seen · hide · purge · filters | Must | Planned |
| 8–9 | C | **PP** password policy · **AB** trusted-device detail | Should | Planned |
| 10–12 | D | **E6** schedules · **J** favourites · **K** cross-host jump | Should | Planned |
| 13–14 | G | **Ports** · **Topo-xhost** | Should | Planned |
| 15–16 | I | **Y** OpenAPI/test UX · **Int-gen** generic URLs | Should | Planned |
| 17 | — | Docs freeze + tag + Hub | Must | Planned |

**Success:** all Must + solid elevation from each of C, D, G, I (prefer full Should). Discover promoted or deferred in RELEASE.

**Capacity Should:** **P-job**, **S-hb**, **S-icon**.

---

## 3. Workstreams

### A — Certificates & TLS

| Tier | ID | Item |
|------|-----|------|
| Must | **P** | Guided setup / wizard |
| Must | **P-sudo** | Sudoers generator correctness |
| Must | **P-fb** | Deploy actionable errors |
| Should | **P-job** | Multi-map deploy as Job |
| Discover | — | Optional verify-sudoers probe |
| → v1.2+ | **P-acme**, **P-npm-w** | ACME-in-herder · NPM write CRUD |

### B — LAN Discovery

| Tier | ID | Item |
|------|-----|------|
| Must | **S1–S4** | Last seen · hide · purge · filters |
| Should | **S-hb**, **S-icon** | Heartbeat on boot · icons by kind |
| Discover | **S-port** | Per-service port labels |
| → v1.2+ | — | Scan redesign / new vuln engines |

**Locks:** stale = offline flag; never auto-delete.

### C — Identity

| Tier | ID | Item |
|------|-----|------|
| Should | **PP**, **AB** | Password policy · trusted-device detail |
| Discover | **H-lite** | SMTP + test send |
| Discover | **H** / **H-ch** | Full mail + channels if H-lite easy |
| → v1.2 | **G1**, **G2-mail** | Self-service / admin email reset |
| → v1.3 | **Z**, WebAuthn, multi-tenant | SSO program |

### D — Operator UX

| Tier | ID | Item |
|------|-----|------|
| Should | **E6**, **J**, **K** | Schedules · favourites · cross-host jump |
| Discover | **E9** | Selectable hero stats |
| Optional | **M** | Templates fleet overview |
| → v1.3 | **Brand** | Custom logo / accents |

### G — Topology / maps / fabric

| Tier | ID | Item |
|------|-----|------|
| Should | **Ports**, **Topo-xhost** | Published ports · cross-host picker |
| Discover | **Topo-col**, **P6**, **Topo-prof** | Columns · shared services · profiles |
| → v1.2+ | **DNS-ext**, **Mig** | External DNS · host migrate |

### I — Integrations & API

| Tier | ID | Item |
|------|-----|------|
| Should | **Y**, **Int-gen** | OpenAPI/bearer test · generic URL entries |
| Discover | **Int-multi**, **N-thin** | Multi-instance · fleet health card |
| → v1.2+ | **N**, deep adapters | Full insights · full Frigate/n8n product |

---

## 4. Phased train

| Phase | Focus |
|-------|--------|
| **A0** | Plan lock (this document) |
| **A1** | Certs **P / P-sudo / P-fb** ← **first implementation** |
| **B1** | Discovery **S1–S4** (+ **S-hb**) |
| **D1** | **E6** schedules |
| **D2** | **J + K** navigation |
| **C1** | **PP + AB** |
| **G1** | **Ports + Topo-xhost** |
| **I1** | **Y + Int-gen** |
| **Cap** | Discover pull-ins by capacity |
| **Freeze** | Docs · version · merge · tag · Hub |

---

## 5. Quality bar

| Gate | Target |
|------|--------|
| Unit | ≥ 55% line on `app` |
| CI fail-under | 55 |
| E2E | Touched surfaces; baseline green |
| Docs | `mkdocs build --strict` at freeze |
| CI labs | No live SSH / nmap / NPM / HA |

---

## 6. Later release paths

Not abandoned — scheduled as paths. Items may move between 1.2 and 1.3 as the train progresses.

### v1.2 path

| Theme | Items |
|-------|--------|
| Identity completion | Full **H** · **G1** · **G2-mail** · channels |
| Network | **DNS-ext** · residual cert multi-deploy · **Mig** design |
| Insights | **N-thin** → first **N** slices |
| Templates | **M** · **Git-cat** · git-rich start |
| HA | Add-on updates · component picker · wiki depth |
| Host lifecycle start | **HL-P3** stats/commands · **2c** cascades · **HL-P4** bootstrap |

### v1.3 path

| Theme | Items |
|-------|--------|
| SSO / OIDC (**Z**) | BYO IdP · groups → roles |
| Web SSH (**HL-P5**) | Full security bar |
| HA REST / S1 / path 2 | Integration track |
| ACME · NPM write | TLS product expansion |
| Full insights · branding | Horizon UX |
| k8s / bare | Deploy topologies |

Patches for security/data issues still ship as **v1.0.x** / **v1.1.x** regardless of path.

---

## 7. Freeze checklist

- [ ] A Must (P, P-sudo, P-fb)  
- [ ] B Must (S1–S4)  
- [ ] C / D / G / I Should or explicit defer in RELEASE  
- [ ] Discover promoted or → v1.2/v1.3  
- [ ] Cert known-edges card updated  
- [ ] `RELEASE_v1.1.0.md` · wiki · screenshots as needed  
- [ ] Unit ≥55% · E2E touch · `mkdocs build --strict`  
- [ ] Version `1.1.0` · merge · tag · Hub  
- [ ] ROADMAP + SECURITY supported versions  

---

## 8. Parallel: v1.0.x

| Severity | Where |
|----------|--------|
| Security / data-loss / auth | **`main`** → `v1.0.x` → port to `v1.1.0-dev` |
| 1.1 features | `v1.1.0-dev` only |

---

## 9. Migration

| Topic | Expectation |
|-------|-------------|
| Alembic | Prefer additive; document in RELEASE |
| Master key | Unchanged |
| REST | Compatible scopes |
| Upgrade 1.0 → 1.1 | Self-backup → pull → `compose up` |
| Cert maps | Existing keep working; wizard preferred for new stage+sudo |

---

## 10. Phase A1 — Certs (first implementation slice)

**Goal:** Close residual **P** from 1.0 — stage+sudo maps without hand-debugging sudoers.

### Inventory

| Area | Location | Notes |
|------|----------|--------|
| Sudoers generator | `app/services/certificates.py` → `sudoers_snippet_for_map` | Hardcodes `/home/{user}`; deploy uses real `$HOME` |
| Deploy stage | same file `stage_sudo` | `{home}/.piherder/cert-stage/{map-id}/` |
| Live snippet UI | `certificates_detail.html` JS | Can drift from server |
| Setup page | `certificates_setup.html` | Warning card “later release” — retire when A1 ships |
| Errors | deploy `RuntimeError` | `"sudoers?"` → actionable copy |
| Tests | `tests/test_certificates*.py` | Extend path + snippet ↔ deploy |

### Known edges (1.0)

1. Snippet assumes `/home/<ssh-user>` — custom home / root need absolute paths.  
2. Snippet must match deploy commands exactly (`sudo -n install …`).  
3. Post-deploy restarts need own allow lines (document + hints).  
4. Prefer stage+sudo for root-owned destinations.

### Breakdown

| Step | Work | Done when |
|------|------|-----------|
| **A1.0** | Reproduce mismatch cases | **Done** — custom home / root / `~/` fixtures in unit tests |
| **A1.1** | Shared path helpers; snippet + deploy aligned | **Done** — `resolve_ssh_home`, `expand_remote_dir`, `cert_stage_*`; deploy + `sudoers_snippet_for_map` share them; tests green |
| **A1.2** | UI uses server truth | Map form JS still hardcodes `piherder` / `/home/piherder` — next |
| **A1.3** | Guided setup on `/certificates/setup` | Server → mode → paths → sudoers → create map |
| **A1.4** | Deploy error copy | **Partial** — install -d / install file messages improved; more polish OK |
| **A1.5** | Wiki + retire/shrink warning card | Docs match UI |
| **A1.6** | Tests + E2E chrome | Unit pack expanded; E2E setup chrome later |

### A1 out of scope

ACME · NPM write · auto-SSH sudoers install · **P-job** (Cap/Should later)

### A1 design defaults

| Question | Default |
|----------|---------|
| Wizard surface | Extend **`/certificates/setup`** + deep-link from maps |
| Install sudoers over SSH | **No** in A1 — copy + operator `visudo` |
| `home_dir` for snippet | Optional arg; from server SSH user facts when known |

### Immediate next (implementation)

1. Unit-test expected sudoers lines vs deploy path expansion.  
2. Fix `sudoers_snippet_for_map` (+ helpers) for real home.  
3. Guided setup chrome.  
4. Error strings.  
5. Docs + E2E.

---

## 11. Changelog (planning)

| Date | Note |
|------|------|
| 2026-07-29 | Branch `v1.1.0-dev` from `main` @ v1.0.0. Residual P+S plan opened. |
| 2026-07-29 | Elevation streams **A, B, C, D, G, I** locked. Mode: focus · polish · discover · pull-in · defer. |
| 2026-07-29 | Deferred framed as **v1.2 / v1.3 paths**. Phase **A1 certs** is first implementation slice. |

---

**End of plan** — living on `v1.1.0-dev` until freeze into `RELEASE_v1.1.0.md`.
