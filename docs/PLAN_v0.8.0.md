# PiHerder v0.8.0 — RC3 development plan

**Status:** **Active** (opens with `v0.7.0` tag — 2026-07-19)  
**Date opened:** 2026-07-19  
**Baseline:** `v0.7.0` (tagged)  
**Package version during cycle:** prefer `0.8.0.dev0` after first product commit · **tag:** `0.8.0`  
**Related:** [PLAN_v0.7.0.md](PLAN_v0.7.0.md) · [RELEASE_v0.7.0.md](RELEASE_v0.7.0.md) · [FEATURE_PLAN_RUNTIME_TOPOLOGY.md](FEATURE_PLAN_RUNTIME_TOPOLOGY.md) · [FEATURE_PLAN_HOST_LIFECYCLE.md](FEATURE_PLAN_HOST_LIFECYCLE.md) · [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · [SPEC.md](../SPEC.md) · [wiki screenshots README](../wiki/assets/screenshots/README.md)

### Decision (locked)

| Choice | Value |
|--------|--------|
| Git tag target | **`v0.8.0`** |
| Theme | **RC3 quality + LAN discovery** — overall polish, deeper tests, full docs/screenshots, **nmap** as the headline feature |
| Planning frame | Four pillars in parallel: **polish · E2E/coverage · docs+screenshots · nmap**; cut polish before cutting nmap or docs bar |
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
| **P** Overall polish | Should / strong | Inventory below |
| **Q** E2E + test coverage | **Must** (grow bar) | Extend from 0.7 foundation |
| **A** Full docs review + screenshot pack | **Must** | Parked from 0.7 |
| **N** LAN discovery (nmap-class) | **Must** (feature) | Planned |
| **L** Host lifecycle P3 | Nice / capacity | Optional |
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

### Q — Extend E2E + test coverage — **must grow**

Build on the 0.7 Playwright platform and unit suite.

| Track | Target | Priority |
|-------|--------|----------|
| **E2E B6** | Viewer cannot add server (403 / redirect) | Should |
| **E2E deeper B** | Template list shell, certs list, Docker page chrome, Jobs filters | Should |
| **HTTP TestClient smoke** | Auth redirects + main page 200s in unit job | **Must** (cheap) |
| **Unit coverage growth** | Critical paths: crypto, RBAC, path policy, fabric pure functions, compose sets, annotations, cert vault | **Must** (meaningful, not 100%) |
| **Playwright Phase C slice** | Optional visual/a11y on 3–5 shells | Nice |
| Flake hygiene | Stable testids; no arbitrary sleeps; CI artifacts already wired | Must keep |

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

**Solution:** Opt-in scan of a configured **Network LAN CIDR**, store/discover devices, optional link to PiHerder servers / fabric nodes. Orthogonal to stack dependency edges.

| Aspect | Lean |
|--------|------|
| Trigger | Manual + optional schedule; **opt-in** only |
| Engine | nmap-class (or equivalent) in job/worker — never silent full-net scans |
| Safety | Preview / confirm / audit; rate limits; no automatic privilege escalation |
| UI | Discovery list + “link to server” / ignore; fabric may show device dots later |
| Out | Replacing Kuma; agent install; wireless site survey; auto-create server rows |

**Acceptance (indicative — refine early in cycle):**

- [ ] Operator can configure LAN CIDR(s) and run a **discover** job  
- [ ] Results list with address / hostname / open ports (bounded)  
- [ ] Link or dismiss a device; audit trail  
- [ ] Wiki + ADMIN security notes (opt-in, blast radius)  
- [ ] Unit tests for parse / link helpers; no live scan required in CI  
- [ ] Screenshot(s) for discovery UI (stream A)  

**Design detail:** [FEATURE_PLAN_RUNTIME_TOPOLOGY.md](FEATURE_PLAN_RUNTIME_TOPOLOGY.md) H1 · [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md).

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
| 1 | LAN discovery product slice (N) | Open |
| 2 | Full docs review + screenshot pack (A) | Open |
| 3 | HTTP smoke + meaningful unit coverage growth (Q) | Open |
| 4 | E2E suite green (0.7 base + extensions) | Open |
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
1. PLAN_v0.8.0 open (this file) + nmap design lock     // docs
2. HTTP smoke + unit coverage pass                       // Q early
3. Screenshot inventory kickoff (stale + missing)        // A parallel
4. Discovery model + job + parser fixtures               // N
5. Discovery UI + link/dismiss + audit                   // N
6. Wiki / ADMIN security + nmap screenshots              // A + N
7. E2E extensions (B6, shell journeys)                   // Q
8. Capacity polish (P / P3)                              // optional
9. Full prose review pass                                // A
10. Freeze: tests · screenshots · RELEASE · version · Hub
```

---

## 7. Open questions (resolve early)

| # | Question | Default lean |
|---|----------|--------------|
| 1 | nmap binary in image vs sidecar vs remote agent? | **Job on herder host** first; document image deps |
| 2 | Persist full port inventory forever? | **Bounded TTL** + latest snapshot |
| 3 | Auto-create server rows from discovery? | **No** — link/dismiss only |
| 4 | How hard is the coverage bar? | **Critical paths first** — no 100% target |
| 5 | Pull P3 into must? | **No** unless operator pain is acute |

---

## 8. Docs map

| Doc | Role |
|-----|------|
| **This file** | Living ship plan for RC3 |
| [PLAN_v0.7.0.md](PLAN_v0.7.0.md) / [RELEASE_v0.7.0.md](RELEASE_v0.7.0.md) | Prior cycle |
| [FEATURE_PLAN_RUNTIME_TOPOLOGY.md](FEATURE_PLAN_RUNTIME_TOPOLOGY.md) | H1 nmap · topology residual |
| [FEATURE_PLAN_HOST_LIFECYCLE.md](FEATURE_PLAN_HOST_LIFECYCLE.md) | P3–P5 design |
| [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) | Multi-horizon |
| [wiki screenshots README](../wiki/assets/screenshots/README.md) | Capture inventory |
| `RELEASE_v0.8.0.md` | Written at tag only |

---

## 9. Success criteria

After upgrading to **0.8.0**, an operator can:

1. **Opt in** to LAN CIDR discovery and link/dismiss devices with audit.  
2. Read the **wiki** with **current screenshots** for onboarding, certs, Docker, Jobs, topology, and discovery.  
3. Rely on **broader automated tests** catching shell and critical-path regressions.  
4. Still use 0.7 wizard / compose sets / annotations unchanged (plus polish that made the cut).

Before tagging **0.8.0**, a maintainer can:

1. Run unit + e2e green without live lab nmap in CI.  
2. Pass `mkdocs build --strict` with the screenshot pack.  
3. Read RELEASE notes that list intentional outs (web SSH, ACME-in-herder, …).

---

## 10. Changelog (plan)

| Date | Note |
|------|------|
| 2026-07-19 | Plan created at 0.7 freeze: RC3 = polish + E2E/coverage + full docs/screenshots + **nmap** feature |

---

**End of plan** — living document until freeze; freeze narrative moves into `RELEASE_v0.8.0.md` at tag.
