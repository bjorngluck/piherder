# PiHerder v0.9.0 — last pre-production

**Status:** **Active** (locked 2026-07-23)  
**Date:** 2026-07-23  
**Git tag target:** `v0.9.0`  
**Theme:** Operator UX/UI consistency · quality bar (unit **55%+**, E2E on touched surfaces) · **Home Assistant discovery**  
**Baseline:** `v0.8.0` (RC3 — LAN nmap + quality)  
**Related:** [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · [RELEASE_v0.8.0.md](RELEASE_v0.8.0.md) · [FEATURE_PLAN_LAN_NMAP.md](FEATURE_PLAN_LAN_NMAP.md) · [FEATURE_PLAN_HOME_ASSISTANT.md](FEATURE_PLAN_HOME_ASSISTANT.md) · [FEATURE_PLAN_RUNTIME_TOPOLOGY.md](FEATURE_PLAN_RUNTIME_TOPOLOGY.md)

> **Last pre-production release** before **v1.0**. Baseline **v0.8.0 is tagged**. Micro-pass items that landed on main for 0.8 screenshots are listed under §0.

---

## 0. Landed early (v0.8.0 micro-pass)

| ID | Item | Status |
|----|------|--------|
| **B1** | Server list: remove footer help text (*Status from last update checks…*) | **Done** (pre-tag) |
| **E4** | Discovery Runs: drop ID column; confine horizontal scroll; hide Ports on narrow | **Done** (pre-tag) |
| **E8** | Catalog Network: “Path mix” → branded **By path type** (Host / App / NPM) | **Done** (pre-tag) |

---

## 1. Goal

Raise the **operator bar** and **confidence bar** for production:

1. **UX/UI consistency** on discovery, Catalog Network, Kuma coverage, and shared modal/filter patterns (same product capabilities as 0.8, better chrome).  
2. **Unit tests ≥ 55%** line coverage on `app` (freeze target); raise CI fail-under in steps (see Stream Q).  
3. **E2E:** any code / UX surface we **touch** in this release gets **basic Playwright coverage** (shell load, primary chrome, modal open/close — no live SSH/nmap/HA).  
4. **Home Assistant discovery:** architecture feature plan + light **path 1** work (mark / auto-discover HAOS, optional SSH facts). Full REST adapter and HAOS-native component are **not** ship requirements for 0.9.

**Not goals for 0.9:** full HA REST integration as a must-ship; HAOS custom component / add-on (path 2); human-readable cron platform (E6); customizable hero stats (E9); full templates catalog redesign; web SSH; ACME-in-herder; cert map overhaul.

---

## 2. Workstreams

### Stream D — Discovery chrome

| ID | Item | Effort | Notes |
|----|------|--------|-------|
| **B2** | Devices + Network filter boxes consistent | M | **Done** — shared `nmap-filter-bar` chrome, search sizing, empty states, E2E |
| **E1** | Server detail: LAN discovery less dominant | S–M | **Done** — collapsed strip after dest cards; richer chip (IP · ports) |
| **E2** | Offline / unmatched devices | M | **Done** — stale UI = Offline flag + warning colour; never auto-delete; ignore stays manual |
| **E3** | Overview cleanup | M | Scan now + vuln pack update → **modals**; page keeps status chips |
| **E5** | Schedules UX | M–L | **Schedules tab list-first**; actions → ⋯ menu (Docker pattern); add/edit → **modal**. Network tab stays map-first |

**Product locks (from triage):**

1. Offline: flag + colour, not auto-remove.  
2. Schedules live on Schedules tab (list-first + modal), not moved onto Network.

### Stream N — Catalog Network hub

| ID | Item | Effort | Notes |
|----|------|--------|-------|
| **E7** | Network hub too busy | M–L | Host DNS · External DNS · Network map · Adopt → **modals/drawers**; hub = nav + service paths + entry cards; mobile-friendly |

E8 already done in micro-pass; residual polish only if needed after E7.

### Stream K — Kuma coverage mobile

| ID | Item | Effort | Notes |
|----|------|--------|-------|
| **E10** | Coverage tables not mobile-friendly | M | `dns_coverage.html`: card rows on narrow viewports; simplify bind row; keep audit logic |

### Stream U — Cross-cutting UX consistency

When we touch a surface, apply shared patterns (not a full redesign):

- Secondary actions → **modal/drawer** (Docker / nmap Network edit pattern).  
- Filter/search chrome: one padding + chip + empty-state pattern.  
- Dense lists: **⋯ overflow** for row actions where we already use that pattern.  
- Mobile: tables → cards where decided (E10) and for new dense tables we touch.  
- Wizard micro-copy / edge cases only if capacity (RELEASE_v0.8.0 known issue §1) — **not** a rewrite.

North star: residual chrome from RELEASE_v0.8.0 known issue **#3**. Concrete checklist: B2, E1–E5, E7, E10.

### Stream T — Templates light (stretch)

| ID | Item | Effort | Notes |
|----|------|--------|-------|
| **E11 partial** | Badge / group OOTB vs user | S | Optional capacity only — not full catalog redesign |

### Stream Q — Quality (primary ship bar)

| ID | Item | Target |
|----|------|--------|
| **Q1** | Unit coverage freeze | **≥ 55%** line (`--cov=app`) |
| **Q2** | CI fail-under | Raise from **35** toward **45–50** once suite is stable at 55% — **do not** set fail-under equal to freeze target overnight |
| **Q3** | Critical-path depth | Prefer pure services: nmap residual, integrations registry/adapters, fabric/coverage, auth/RBAC edges, helpers for UX we touch |
| **Q4** | E2E on touched UX | Each stream D/N/K/HA UI change: ≥1 Playwright test (tab, modal, filter, list chrome) |
| **Q5** | HTTP smoke | Extend `test_http_smoke` / seeded surfaces for any new routes |

**E2E policy (0.9):**

- Baseline: shell login/nav, wizard, B6 viewer, nmap LAN shells.  
- **Rule:** code/UX touched in 0.9 → basic E2E in the same PR or release train.  
- Still **no** live SSH, live nmap, or real Home Assistant in CI.  
- Prefer stable selectors (`data-testid` for new chrome when needed).

**Coverage tactics:** pure unit for classifiers, formatters, offline flags, schedule list models; mocked SSH/HA probes. No router-% farming; no 100% target.

**Baseline (v0.8.0 freeze):** ~49–50% line; CI fail-under 35.

### Stream HA — Home Assistant discovery

Detail: **[FEATURE_PLAN_HOME_ASSISTANT.md](FEATURE_PLAN_HOME_ASSISTANT.md)** (living; refine during 0.9).

| ID | Item | Ship in 0.9? | Notes |
|----|------|--------------|-------|
| **HA0** | Architecture / feature plan | **Yes** | This cycle’s discovery home |
| **HA1** | Mark managed Server as HA / HAOS | **Likely** | Operator flag or detected profile; UI chip |
| **HA2** | Auto-discover HAOS signals | **Spike → ship if small** | SSH (`ha` CLI, os-release), nmap kind (8123), container name patterns |
| **HA3** | Read-only facts via SSH | **Spike** | Version / install type — no HA long-lived token required |
| **HA4** | REST/API poll (LLAT) | **Discovery only** | Document only; gut: **v1.0+** implement |
| **HA5** | HAOS custom component / add-on (path 2) | **Out** | v1.0 or first maintenance after |
| **HA6** | Add-ons inventory / rich dashboard shortcuts | **Discovery only** | Ideas catalog in feature plan |

**Decision lean (refine during 0.9, freeze later):** path 1 REST → **v1.0 roadmap** unless a spike is trivially high-value; path 2 → post-1.0 (or 1.0 maintenance).

---

## 3. Explicitly out of v0.9.0

| ID | Item | Destination |
|----|------|-------------|
| **E6** | Human-readable schedules everywhere + advanced cron | Post-1.0 platform discovery |
| **E9** | User-selectable hero card stats | Post-1.0 discovery |
| **E11 full** | Templates table/filter IA, extra pack files, source clarity | H3 / FEATURE_PLAN_TEMPLATES |
| **HA4 ship** | Full HA REST adapter + bindings UI | Likely **v1.0** ([feature plan](FEATURE_PLAN_HOME_ASSISTANT.md)) |
| **HA5** | HAOS-native integration | ≥1.0 |
| | Web SSH, ACME-in-herder, Cloudflare automation, large curated template pack | Existing later horizons |
| | Cert map / sudoers overhaul (RELEASE #2) | Separate cert-focused release |
| | Deep wizard rewrite | Stretch micro-copy only |

---

## 4. Ship bar

| # | Item |
|---|------|
| 1 | Stream D: B2, E1–E3, E5 |
| 2 | Stream N: E7 |
| 3 | Stream K: E10 |
| 4 | Stream Q: unit **≥ 55%**; CI fail-under raised; E2E for touched UX |
| 5 | Stream HA: feature plan published; **HA1** and/or **HA2/HA3** if discovery lands shippable; **no** forced REST |
| 6 | `RELEASE_v0.9.0.md` + tag + Hub |

Stretch: E11 partial badges; wizard micro-copy.

---

## 5. Roadmap discovery (document only until design)

Capture in [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) / feature plans:

1. **Human-readable schedules (E6)** — shared formatter for interval/cron; advanced = raw cron.  
2. **Selectable hero stats (E9)** — preference model + metric registry.  
3. **Templates catalog redesign (E11)** — OOTB vs user, table/filter, extra config files.  
4. **Home Assistant** — path 1 REST + path 2 HAOS component: [FEATURE_PLAN_HOME_ASSISTANT.md](FEATURE_PLAN_HOME_ASSISTANT.md).

---

## 6. Key files

| Stream | Files / areas |
|--------|----------------|
| D | `integrations_nmap_detail.html`, `ops-pages.css`, `nmap_device_modal.html`, `server_detail.html`, nmap schedules / device_ops |
| N | `dns_list.html`, fabric partials, `dns.py` as needed for modal partials |
| K | `dns_coverage.html` |
| T | `templates_list.html`, `service_templates/catalog.py` |
| Q | `tests/**`, `e2e/**`, `.github/workflows/test.yml` |
| HA | `FEATURE_PLAN_HOME_ASSISTANT.md`; possible `device_classify` kind; server profile / features; SSH diagnostics helpers |

---

## 7. Changelog (planning)

| Date | Note |
|------|------|
| 2026-07-22 | Plan opened from post-0.8 UX triage; B1/E4/E8 micro-pass for 0.8 tag |
| 2026-07-23 | **Locked as last pre-production:** UX consistency + unit **55%+** + E2E-on-touch + HA discovery stream; [FEATURE_PLAN_HOME_ASSISTANT.md](FEATURE_PLAN_HOME_ASSISTANT.md) |
| 2026-07-23 | **B2** + **E1** landed: shared discovery filter bar; server-detail LAN strip collapsed after dest cards |
| 2026-07-23 | **E2** offline flag: state pill/card/row colour; filter chip Offline; label Offline |

**End of plan** — living document; freeze into RELEASE notes at tag.
