# PiHerder v0.9.0 — operator UX polish

**Status:** Planned (post–v0.8.0)  
**Date:** 2026-07-22  
**Git tag target:** `v0.9.0`  
**Theme:** Operator surfaces — discovery chrome, Catalog Network hub, Kuma coverage mobile  
**Baseline:** `v0.8.0` (RC3 — LAN nmap + quality)  
**Related:** [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · [RELEASE_v0.8.0.md](RELEASE_v0.8.0.md) · [FEATURE_PLAN_LAN_NMAP.md](FEATURE_PLAN_LAN_NMAP.md) · [FEATURE_PLAN_RUNTIME_TOPOLOGY.md](FEATURE_PLAN_RUNTIME_TOPOLOGY.md)

> Open implementation only after **v0.8.0 is tagged**. Micro-pass items landed on main for 0.8 screenshots are listed under §0.

---

## 0. Landed early (v0.8.0 micro-pass)

| ID | Item | Status |
|----|------|--------|
| **B1** | Server list: remove footer help text (*Status from last update checks…*) | **Done** (pre-tag) |
| **E4** | Discovery Runs: drop ID column; confine horizontal scroll; hide Ports on narrow | **Done** (pre-tag) |
| **E8** | Catalog Network: “Path mix” → branded **By path type** (Host / App / NPM) | **Done** (pre-tag) |

---

## 1. Goal

Same product capabilities as 0.8; **better chrome, mobile, and IA** on the surfaces operators use after nmap and fabric landed.

**Not goals for 0.9:** new integrations, human-readable cron platform, customizable hero stats, full templates catalog redesign (those are roadmap / post-1.0 discovery — §5).

---

## 2. Workstreams

### Stream D — Discovery chrome

| ID | Item | Effort | Notes |
|----|------|--------|-------|
| **B2** | Devices + Network filter boxes consistent | M | Shared search + chips + empty state; match box chrome across both tabs |
| **E1** | Server detail: LAN discovery less dominant | S–M | Move/collapse discovery into server-info / secondary strip; dest cards stay primary |
| **E2** | Offline / unmatched devices | M | **Flag + colour after stale threshold**; no silent hard-delete; ignore stays manual |
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

### Stream T — Templates light (stretch)

| ID | Item | Effort | Notes |
|----|------|--------|-------|
| **E11 partial** | Badge / group OOTB vs user | S | Optional capacity only — not full catalog redesign |

---

## 3. Explicitly out of v0.9.0

| ID | Item | Destination |
|----|------|-------------|
| **E6** | Human-readable schedules everywhere + advanced cron | Roadmap discovery (post-1.0 platform) |
| **E9** | User-selectable hero card stats | Roadmap discovery (post-1.0; non-committed) |
| **E11 full** | Templates table/filter IA, extra pack files (CA Advisor etc.), source clarity | Horizon 3 / FEATURE_PLAN_TEMPLATES expansion |
| | Web SSH, ACME-in-herder, Cloudflare automation, large curated template pack | Existing later horizons |

---

## 4. Ship bar (indicative)

| # | Item |
|---|------|
| 1 | Stream D items B2, E1–E3, E5 |
| 2 | Stream N item E7 |
| 3 | Stream K item E10 |
| 4 | Smoke / E2E green on touched shells |
| 5 | `RELEASE_v0.9.0.md` + tag + Hub |

Stretch: E11 partial badges.

---

## 5. Roadmap discovery (document only until design)

Capture in [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md):

1. **Human-readable schedules (E6)** — shared formatter for interval/cron across server OS/backup, nmap, herder backup, data cleanup; advanced = raw cron.  
2. **Selectable hero stats (E9)** — preference model, metric registry, per-screen defaults (needs discovery).  
3. **Templates catalog redesign (E11)** — OOTB vs user, extra config files, table/filter layout (needs discovery).

---

## 6. Key files

| Stream | Files |
|--------|--------|
| D | `integrations_nmap_detail.html`, `ops-pages.css`, `nmap_device_modal.html`, `server_detail.html` |
| N | `dns_list.html`, fabric partials, `dns.py` as needed for modal partials |
| K | `dns_coverage.html` |
| T | `templates_list.html`, `service_templates/catalog.py` |

---

## 7. Changelog (planning)

| Date | Note |
|------|------|
| 2026-07-22 | Plan opened from post-0.8 UX triage; B1/E4/E8 micro-pass for 0.8 tag |
| | Implementation starts after v0.8.0 tag |

**End of plan** — living document once 0.9 work begins.
