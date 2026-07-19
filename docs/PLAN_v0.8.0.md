# PiHerder v0.8.0 — RC3 development plan

**Status:** **Active** (opens with `v0.7.0` tag — 2026-07-19)  
**Date opened:** 2026-07-19  
**Baseline:** `v0.7.0` (tagged)  
**Package version during cycle:** prefer `0.8.0.dev0` after first product commit · **tag:** `0.8.0`  
**Related:** [PLAN_v0.7.0.md](PLAN_v0.7.0.md) · [RELEASE_v0.7.0.md](RELEASE_v0.7.0.md) · [FEATURE_PLAN_LAN_NMAP.md](FEATURE_PLAN_LAN_NMAP.md) · [FEATURE_PLAN_RUNTIME_TOPOLOGY.md](FEATURE_PLAN_RUNTIME_TOPOLOGY.md) · [FEATURE_PLAN_HOST_LIFECYCLE.md](FEATURE_PLAN_HOST_LIFECYCLE.md) · [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · [SPEC.md](../SPEC.md) · [wiki screenshots README](../wiki/assets/screenshots/README.md)

### Decision (locked)

| Choice | Value |
|--------|--------|
| Git tag target | **`v0.8.0`** |
| Theme | **RC3 quality + LAN discovery** — overall polish, deeper tests (**~50% unit coverage**), full docs/screenshots, **nmap** as the headline feature |
| Planning frame | Four pillars in parallel: **polish · E2E/coverage · docs+screenshots · nmap**; cut polish before cutting nmap or docs bar |
| Coverage bar | **~50%** unit line coverage (tag should-meet; force growth without 100% chase) |
| Nmap product leans | Auto-create **discovery** · **network view** · **manual** promote · **separate worker** + **vuln volume** (Vulners) — see feature plan |
| Production path | ~~v0.5.0 RC1~~ → ~~**v0.6.0 RC2**~~ → ~~**v0.7.0**~~ **tagged** → **v0.8.0 RC3** (this cycle) → **v1.0** |
| Docs strategy | Full wiki/prose review + **screenshot pack** (deferred from 0.7) + nmap UX docs; `RELEASE_v0.8.0.md` at tag |
| **Out of 0.8 by default** | Web SSH (P5), full ACME-in-herder, K8s, large template pack |

### Why RC3

| RC | Tag | Role |
|----|-----|------|
| **RC1** | `v0.5.0` | First candidate — ops depth, fabric, multi-arch |
| **RC2** | `v0.6.0` | Operator polish — template Jobs, cert UX, Docker bulk, topology |
| **(onboarding)** | `v0.7.0` | Wizard + Playwright E2E foundation + compose sets / annotations |
| **RC3** | **`v0.8.0`** | Quality depth + docs truth + **nmap** — last major candidate wave before **v1.0** |

---

## 1. Theme

**Make 0.7 production-grade and discover the LAN.** v0.7 shipped the add-host wizard, E2E harness, and Docker topology presentation. RC3 hardens that surface: polish UX gaps, grow automated confidence, refresh every important wiki PNG, and add **opt-in LAN discovery (nmap-class)** as the only large new product slice.

v0.8.0 RC3 pillars:

1. **Overall polish** — residual Jobs/UX/perf cut from 0.7 + light operator clarity  
2. **Extend E2E + unit coverage** — more journeys, HTTP smoke, meaningful service tests  
3. **Full document review + screenshots** — prose audit + complete capture pack  
4. **LAN discovery (nmap)** — headline **new feature**  

This is **not** web SSH, ACME-in-herder, or a second onboarding rewrite.

---

## 2. What 0.7.0 already delivered (do not re-build)

| Area | v0.7.0 state |
|------|----------------|
| Onboarding | Add-host **wizard** primary; advanced form secondary |
| Quality | Playwright **Phase A + wizard B**; e2e compose set; CI job |
| Docker / topology | Annotations (T0–T4), view groups, **compose sets**, stack panel |
| Docs | Wizard + compose-set prose; **PNG pack deferred to 0.8** |

0.8 **hardens and extends**; it does not replace the wizard or E2E harness.

---

## 3. Workstreams

### Summary

| Stream | Must for 0.8? | Status |
|--------|---------------|--------|
| **P** Overall polish | Should / strong | Inventory below — mostly open |
| **Q** E2E + test coverage | **Must** (grow bar) | **In progress** — HTTP smoke + nmap/cleanup unit depth; cov floor 30% in CI; ~50% still open |
| **A** Full docs review + screenshot pack | **Must** | Prose: LAN Discovery + Hosts map discovery overlay + names/kinds wiki updated (2026-07-20); **PNG pack open** |
| **N** LAN discovery (nmap-class) | **Must** (feature) | **Product largely done** · Hosts map overlay · display names · kind heuristics · N9 shells · screenshots open |
| **R** Data retention / grooming / delete cascades | Should / capacity | **R1 done** (Jobs/Audit/nmap-run opt-in) · R2 docs partial · cascade UI later |
| **L** Host lifecycle P3 | Nice / capacity | Optional / parked |
| **D** Packaging | Must at tag | End of cycle |

---

### P — Overall polish

Carry list from 0.7 stream C + operator friction. **None of these block nmap**, but RC3 should clear the high-value ones.

| Item | Why | Priority | Status |
|------|-----|----------|--------|
| Cert **multi-map / multi-host deploy as Job** | Long ops pattern | Nice | Open |
| Template wizard copy / empty states | First-use clarity | Nice | Open |
| Docker management chips cohesive with stack panel (T6) | Stretch UX | Stretch | Open |
| List query / Docker inventory spam / fabric pulse | Light perf | Stretch | Open |
| Topology **link-to-column** / per-project column profiles | Residual after T0–T4 | Stretch | Open |
| Git template catalog pull | Ops depth | Out unless cheap | Open |
| Misc UX consistency (wizard/docker/certs chrome) | Operator polish | Nice | Open |

If freeze pressure returns, **ship N + A + Q growth**; cut low-value P items.

---

### R — Data retention, grooming, and referential cleanup

Operator feedback (2026-07-19): long-running labs fill **Jobs** / **Audit**; nmap and fleet deletes need a clear story for what stays vs what is purged. Prefer **opt-in** schedules (same spirit as backup retention), defaults that match “keep a month of ops history.”

#### R1 — Time-based retention (platform)

| Item | Default lean | Notes |
|------|----------------|-------|
| **Jobs history purge** | **Off** until enabled · **30 days** when on | Settings (or Status/ops) toggle + `retention_days` (min floor e.g. 7). Scheduled job (APScheduler) + optional “Run now”. Never delete **pending/running**. Prefer delete finished rows older than N days; keep optional “keep last K per type” later. |
| **Audit log purge** | **Off** until enabled · **30 days** when on | Same pattern as Jobs. **Separate** enable + days (ops may want audit longer than jobs). |
| **Safety** | Preview count → confirm · audit the purge itself | Dry-run summary: “would delete X jobs / Y audit rows”. Admin-only. |
| **nmap run XML artifacts** | Bound under `DATA_ROOT/nmap/…` | Align with [FEATURE_PLAN_LAN_NMAP.md](FEATURE_PLAN_LAN_NMAP.md) retention: drop old run XML + `NmapScanRun` summaries older than N (or keep last K runs per integration). |
| **Backup file retention** | Already exists per-server (`retention_days` / retention job) | Do not conflate with Jobs/Audit DB purge. |

**Ship shape (when pulled in):** `AppSetting` (or dedicated config keys) · `retention` / `jobs_purge` / `audit_purge` job types · wiki/ADMIN section · unit tests with frozen time.

#### R2 — Entity delete & cascade (what happens today vs target)

| Entity | **Today** (baseline) | **Target roadmap** |
|--------|----------------------|---------------------|
| **Server** (fleet remove) | Cancels active jobs; unregisters schedules; deletes compose **drafts**; DNS fabric cleanup; **nulls** `Job`/`Audit`/`Notification.server_id` (history kept, unlinked); **does not** SSH or wipe remote/backups | Document clearly in UI/wiki. Optional later: “also purge unlinked jobs/audit for this former host” checkbox. |
| **Integration** (Kuma/Grafana/nmap…) | Type-specific | Explicit cascade matrix: bindings, nmap devices/runs/schedules, status JSON. Prefer “delete integration → delete discovery children” with confirm. |
| **Nmap device** | Ignore/link/unlink | Dismiss/delete device → drop script results; unlink server only when intended. Stale auto-age. |
| **Service template / deployment** | Existing rows | Document FKs; no orphan deploy jobs pointing at missing templates. |
| **Certificate / map** | Edge apply state | On cert delete: clear maps, stage files policy, audit. |
| **User** | RBAC | Reassign or null audit/user refs; never leave sole-admin trap (existing). |
| **Docker annotation / topology edge** | Per host | Clean when server deleted or project gone (orphans). |

**Principle:**  
1) **History by default** for Jobs/Audit (unlink > hard-delete on entity remove).  
2) **Opt-in time purge** for bulk DB growth (R1).  
3) **Product-owned trees** (nmap devices, compose drafts, bindings) **cascade** on parent delete with preview.  
4) **Never** silently delete remote host data (align with server delete “host left intact”).

#### R3 — Priority / placement

| Slice | Suggest | Priority for 0.8 |
|-------|---------|------------------|
| R1 Jobs + Audit 30d toggle | High operator value; small surface | **Done** (2026-07-19) — Settings → Stale data cleanup · job `stale_data_cleanup` |
| R1 nmap artifact TTL | With nmap ship | **Done** (opt-in toggle; default off · 30d when on) |
| R2 document current server-delete behavior | Cheap | **Done** (wiki remove-server + HOST_LIFECYCLE baseline + ADMIN) |
| R2 full cascade matrix + UI preview | Design + care | **Post-0.8** / 0.9 unless bugs force it |

---

### Q — Extend E2E + test coverage — **must grow**

Build on the 0.7 Playwright platform and unit suite.

| Track | Target | Priority | Status |
|-------|--------|----------|--------|
| **E2E B6** | Viewer cannot add server (403 / redirect) | Should | **Done** — `e2e/test_rbac_viewer.py` + hide Add CTA for viewers |
| **E2E deeper B** | Template list shell, certs list, Docker page chrome, Jobs filters | Should | Open |
| **HTTP TestClient smoke** | Auth gates + main page 200s in unit job (SQLite override, no lifespan) | **Must** (cheap) | **Done** — `tests/test_http_smoke.py` |
| **Unit coverage growth** | **~50% line coverage** target; prioritize crypto, RBAC, path policy, fabric, compose sets, annotations, cert vault, nmap helpers | **Must** (~50%, not 100%) | **In progress** — **~42%** (was ~33%); CI floor 30%; more pure depth next |
| **Nmap / cleanup unit depth** | schedules CRUD, argv edges, stale nmap purge, runtime/config, enqueue | Should | **Done** (strong) |
| **Registry / fabric / cert pure** | credentials encrypt, grafana labels, host IP/tokens, upsert PEM | Should | **Done** (slice) |
| **Playwright Phase C slice** | Optional visual/a11y on 3–5 shells | Nice | Open |
| Flake hygiene | Stable testids; no arbitrary sleeps; CI artifacts already wired | Must keep | Keep |

**Coverage bar (locked):** **~50%** overall unit coverage is the RC3 target — good enough to force meaningful growth without chasing 100%. Measure from the unit job; new nmap code should not tank the average (helpers + fixtures first).

**Out of CI:** live SSH, live nmap of real networks (use fixtures/mocks).

---

### A — Full document review + screenshot pack — **must**

Deferred from 0.7 so product could ship; **hard tag gate for 0.8**.

| Item | Notes | Priority |
|------|--------|----------|
| Prose audit | Walk wiki + ADMIN/SPEC for 0.6–0.7 surfaces (wizard, Jobs, certs, Docker sets, fabric) | Must |
| Refresh **stale** high-priority PNGs | dashboard, servers, jobs, templates, certs list, dns maps, … | Must |
| **New** surfaces | cert setup/detail/edge; docker lifecycle + set pills; jobs live log; coverage; stack panel; **wizard**; **nmap UI** when ready | Must |
| Operator scenarios | Align Journey A with wizard; add discovery journey when nmap lands | Should |
| Capture policy | Light theme · desktop default · see [screenshots README](../wiki/assets/screenshots/README.md) | Locked |

---

### N — LAN discovery (nmap-class) — **must (new feature)**

**Problem:** Operators know services on **managed** hosts, but not the broader LAN without external tools.

**Solution:** Opt-in scan of a configured **Network LAN CIDR**, **auto-create** discovered device records, present an **nmap network view**, and use **manual onboarding** (wizard / server create) where a device should become a managed PiHerder host. Orthogonal to stack dependency edges.

| Aspect | Lean (**locked** 2026-07-19) |
|--------|------------------------------|
| Trigger | Manual + **multiple schedules** (discovery / inventory / detailed); **opt-in** only |
| Engine | **Separate** `celery-worker-nmap` (compose profile `nmap`); web never scans; queue `nmap` |
| Vuln / Vulners | **Mapped volume** for downloads; full Vulners when pack present; **not** in image layers |
| Safety | Preview / confirm / audit; rate limits; CIDR allowlist; no automatic privilege escalation |
| Persistence | **Auto-create** discovery records for found hosts/devices |
| Onboarding | Discovered item ≠ managed server until operator **manually** promotes / links |
| UI | Integrations → LAN Discovery · **network view** · device list · Jobs |
| Out | Replacing Kuma; agent install; wireless survey; silent auto-enroll; flood/brute NSE |

**Progress (2026-07-20):**

| Slice | Status |
|-------|--------|
| Worker image + profile + host network + vuln volume | **Done** |
| Models, parse/upsert, Jobs enqueue, intensities | **Done** |
| UI: Overview / Devices / Network / Schedules / Runs | **Done** |
| Schedules create **and edit** + curated options (preset/timing/ports/UDP/SYN) | **Done** |
| Port scope all/top/list · prefilled CIDRs · intensity-scoped excludes | **Done** |
| Deep scan + vuln DB update + Jobs log progress | **Done** |
| Deep **script presets** (none/cpe/offline/full) + result classification | **Done** |
| Port-level findings cross-link | **Done** |
| Link / ignore / promote shell | **Done** |
| **Device kind heuristics** (MAC vendor/OUI + ports + hostname) | **Done** |
| **Operator map names** (`display_name` → Hosts map chips) | **Done** |
| **Hosts map overlay** — unlinked discoveries, outer chips, toolbar toggle | **Done** |
| Soft embed fleet list + host detail (N8) | **Done** |
| Unit + E2E shells (N9) | **Done** (fixtures only; `e2e/test_nmap_lan.py`) |
| Operator wiki + ADMIN notes | **Done** (refreshed 2026-07-20) |
| Screenshots | **Open** (stream A) |

**Acceptance (detail in feature plan):**

- [x] Configure LAN CIDR(s); discover / inventory / detailed + on-demand / per-IP deep  
- [x] Multiple schedules (incl. edit); auto-created devices; network view  
- [x] Manual promote/link/dismiss shell; audit  
- [x] Compose profile worker + vuln volume; default install without them  
- [x] Curated options + script presets + classify findings (not free-form flags)  
- [x] Soft embed (server list chip + server detail card)  
- [x] End-to-end Hosts map without per-device link; map names; kind badges  
- [x] Wiki (+ ADMIN); high unit/E2E shells (fixtures only in CI)  
- [ ] Screenshots (stream A)

**Design detail:** **[FEATURE_PLAN_LAN_NMAP.md](FEATURE_PLAN_LAN_NMAP.md)** (approved) · operator [lan-discovery.md](../wiki/integrations/lan-discovery.md) · [FEATURE_PLAN_RUNTIME_TOPOLOGY.md](FEATURE_PLAN_RUNTIME_TOPOLOGY.md) · [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md).

---

### L — Host lifecycle (capacity only)

| Phase | Default for 0.8 |
|-------|-----------------|
| **P3** Host stats + allowlisted commands | Capacity / should |
| **P4** Bootstrap depth + enrollment | Out unless tiny |
| **P5** Web SSH | **Out of 0.8** |

---

### D — Packaging (end of cycle)

| Item | Notes |
|------|--------|
| Unit + E2E green (expanded suite) | Tag gate |
| Screenshot pack landed + `mkdocs build --strict` | Tag gate |
| Version → `0.8.0` | `pyproject.toml` · `app/version_info.py` |
| `RELEASE_v0.8.0.md` | Highlights, upgrade, intentional outs |
| Git tag `v0.8.0` + Hub multi-arch | `0.8.0` / `0.8` / `latest` |

---

## 4. Explicitly out of v0.8.0

| Area | Reason |
|------|--------|
| **H2.75 P5** Web SSH | Separate ship bar |
| ACME inside PiHerder | External / NPM |
| NPM proxy host write CRUD | Later |
| Cloudflare DNS automation | H2.5+ |
| Large curated template pack | H3 |
| Kubernetes / bare metal as supported install | Under consideration |
| Live nmap of real lab networks in GitHub Actions | Mock parsers / fixtures only |
| Full v1.0 process freeze | Goal of 1.0 |

---

## 5. Ship bar (`v0.8.0` tag)

### Must-have

| # | Item | Status |
|---|------|--------|
| 1 | LAN discovery product slice (N) | **Mostly done** (N0–N9 shells + soft embed + presets); screenshots remain |
| 2 | Full docs review + screenshot pack (A) | **Partial** (LAN/settings prose); PNG pack open |
| 3 | HTTP smoke + unit coverage **~50%** bar (Q) | Smoke **done**; ~50% still open (CI floor 30%) |
| 4 | E2E suite green (0.7 base + extensions) | Open (0.7 base exists) |
| 5 | Version `0.8.0` + tag + Hub | Open |
| 6 | `RELEASE_v0.8.0.md` | Open |

### Should

- High-value polish (P) items  
- E2E B6 + deeper shell journeys  
- P3 host stats slice  

### Not tag gates

- P4–P5 · full Playwright Phase C matrix · large catalog  

---

## 6. Implementation order (indicative)

```text
1. PLAN_v0.8.0 open + FEATURE_PLAN_LAN_NMAP skeleton     // DONE
2. Flesh nmap feature plan (deploy model, auto-create, UI) // DONE
3. Discovery model + worker + UI + schedules + network     // DONE (N1–N6)
4. Stream R1 stale Jobs/Audit/nmap cleanup                 // DONE
5. Schedule edit + deep/vuln ops polish                    // DONE
6. Wiki / ADMIN for nmap + cleanup                         // DONE (wiki); screenshots open
7. HTTP smoke + unit depth (nmap/cleanup/ops)              // DONE (~41% cov; floor 30%)
8. N8 soft embed + N9 shells + presets/classify            // DONE
9. Coverage growth toward ~50% + E2E depth                 // Q — NEXT
10. Screenshot pack (wizard + nmap + residual 0.6/0.7)     // A — NEXT
11. Capacity polish (P / P3)                               // optional
12. Full prose review pass                                 // A
13. Freeze: tests · ~50% cov · screenshots · RELEASE · Hub
```

---

## 7. Decisions locked vs open (nmap + quality)

| # | Topic | Status | Lean |
|---|-------|--------|------|
| 1 | Unit coverage bar | **Locked** | **~50%** overall (critical paths first; not 100%) |
| 2 | Auto-create from scan | **Locked direction** | **Yes** — auto-create **discovery records**; flesh schema/lifecycle in nmap plan |
| 3 | Network view | **Locked direction** | Dedicated **nmap network view** (not only a flat list) |
| 4 | Managed-server onboarding | **Locked direction** | **Manual** where appropriate (wizard / promote / link) — discovery ≠ full fleet member |
| 5 | nmap runtime | **Locked** | **Separate** `celery-worker-nmap` + dedicated image target + **vuln volume** (Vulners) — [FEATURE_PLAN_LAN_NMAP.md](FEATURE_PLAN_LAN_NMAP.md) |
| 6 | Persist full port inventory forever? | Open | **Bounded TTL** + latest snapshot (default lean) |
| 7 | Pull P3 into must? | Open | **No** unless operator pain is acute |
| 8 | Jobs/Audit DB retention | **Locked direction** | **Opt-in** enable · default **30 days** each · independently configurable (stream **R**) |
| 9 | Delete host → history | **Locked direction** | Keep unlinked Jobs/Audit by default; product trees cascade; remote host never wiped |

---

## 8. Docs map

| Doc | Role |
|-----|------|
| **This file** | Living ship plan for RC3 |
| [FEATURE_PLAN_LAN_NMAP.md](FEATURE_PLAN_LAN_NMAP.md) | **Nmap product design** (flesh out early) |
| [PLAN_v0.7.0.md](PLAN_v0.7.0.md) / [RELEASE_v0.7.0.md](RELEASE_v0.7.0.md) | Prior cycle |
| [FEATURE_PLAN_RUNTIME_TOPOLOGY.md](FEATURE_PLAN_RUNTIME_TOPOLOGY.md) | Topology residual · original H1 nmap pointer |
| [FEATURE_PLAN_HOST_LIFECYCLE.md](FEATURE_PLAN_HOST_LIFECYCLE.md) | P3–P5 design · promote/onboard overlap |
| [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) | Multi-horizon |
| [wiki screenshots README](../wiki/assets/screenshots/README.md) | Capture inventory |
| `RELEASE_v0.8.0.md` | Written at tag only |

---

## 9. Success criteria

After upgrading to **0.8.0**, an operator can:

1. **Opt in** to LAN CIDR discovery; see **auto-created** discovery records in an **nmap network view**; **manually** promote/link or dismiss with audit.  
2. Read the **wiki** with **current screenshots** for onboarding, certs, Docker, Jobs, topology, and discovery/network view.  
3. Rely on **broader automated tests** (~**50%** unit coverage + E2E growth) catching shell and critical-path regressions.  
4. Still use 0.7 wizard / compose sets / annotations unchanged (plus polish that made the cut).

Before tagging **0.8.0**, a maintainer can:

1. Run unit + e2e green without live lab nmap in CI; unit coverage meets **~50%**.  
2. Pass `mkdocs build --strict` with the screenshot pack.  
3. Read RELEASE notes that list intentional outs (web SSH, ACME-in-herder, …) and the chosen nmap deploy model.

---

## 10. Changelog (plan)

| Date | Note |
|------|------|
| 2026-07-19 | Plan created at 0.7 freeze: RC3 = polish + E2E/coverage + full docs/screenshots + **nmap** feature |
| 2026-07-19 | Cycle kickoff: coverage bar **~50%**; nmap **auto-create** discovery records + **network view** + manual onboard |
| 2026-07-19 | **Nmap design approved:** separate worker, vuln volume (Vulners), multi-schedule, intensity ladder — [FEATURE_PLAN_LAN_NMAP.md](FEATURE_PLAN_LAN_NMAP.md) |
| 2026-07-19 | **Stream R** added: Jobs/Audit retention (opt-in, 30d default), nmap artifact TTL, entity-delete cascade matrix (document now / implement as capacity) |
| 2026-07-19 | **N1–N6 + R1 shipped:** LAN UI/schedules/edit, vuln pack Jobs, host-network worker; stale_data_cleanup; wiki LAN Discovery + Settings cleanup |
| 2026-07-19 | **Next focus:** stream **Q** (~50% coverage, HTTP smoke, E2E growth) + stream **A** screenshot pack; optional P polish / N8 embed |
| 2026-07-19 | **Q smoke slice:** `test_http_smoke` (auth 401 + logged-in shells); nmap schedule CRUD / argv / nmap-run purge; ops_pulse + update_check_config; CI cov report + fail-under 30 |
| 2026-07-19 | **Nmap N8–N9 + polish:** soft embed (fleet/host), deep script presets, curated timing/ports/UDP, script classify UI, unit + `e2e/test_nmap_lan.py`; remaining for N: **screenshots** (A) |

---

**End of plan** — living document until freeze; freeze narrative moves into `RELEASE_v0.8.0.md` at tag.
