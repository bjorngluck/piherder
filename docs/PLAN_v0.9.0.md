# PiHerder v0.9.0 — last pre-production

**Status:** **Active** (locked 2026-07-23)  
**Date:** 2026-07-23  
**Git tag target:** `v0.9.0`  
**Theme:** Operator UX/UI consistency · quality bar (unit **55%+**, E2E on touched surfaces) · **HAOS path 1 (SSH / `ha` CLI)**  
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
4. **Home Assistant (HAOS):** path 1 for **full HAOS over SSH** only — auto-mark, CLI stats, **OS check/upgrade via `ha` CLI** (apt equivalent), host deps (SSH add-on + rsync), no Docker fleet mgmt. Container HA, REST/LLAT, and HA→PiHerder component are **not** 0.9 ship requirements.

**Not goals for 0.9:** HA container (S1) profile; full HA REST/LLAT; HAOS custom component (path 2); per-add-on updates; human-readable cron platform (E6); customizable hero stats (E9); full templates catalog redesign; web SSH; ACME-in-herder; cert map overhaul.

---

## 2. Workstreams

### Stream D — Discovery chrome

| ID | Item | Effort | Notes |
|----|------|--------|-------|
| **B2** | Devices + Network filter boxes consistent | M | **Done** — shared `nmap-filter-bar` chrome, search sizing, empty states, E2E |
| **E1** | Server detail: LAN discovery less dominant | S–M | **Done** — collapsed strip after dest cards; richer chip (IP · ports) |
| **E2** | Offline / unmatched devices | M | **Done** — stale UI = Offline flag + warning colour; never auto-delete; ignore stays manual |
| **E3** | Overview cleanup | M | **Done** — Scan now + vuln pack update → modals; page keeps stats + vuln strip; **no** Devices/Network/Jobs shortcut buttons (tabs only) |
| **E5** | Schedules UX | M–L | **Done** — list-first; mobile **card actions**; desktop Edit/Run + ⋯ for Enable/Delete; add/edit modal (`?new=1` / `?schedule=`) |
| **E3b** | Devices + Network merge | M | **Done** — single **Devices** tab with **List \| Map** view toggle; `?tab=network` → map view |
| **E1b** | LAN chip → server return | S | **Done** — link-style LAN pill; device modal `return=server:{id}` closes back to fleet host |
| **E4b** | Runs / Schedules mobile | S | **Done** — card layouts on narrow screens (no min-width table scroll) |

**Product locks (from triage):**

1. Offline: flag + colour, not auto-remove.  
2. Schedules live on Schedules tab (list-first + modal), not a separate Network tab.

### Stream N — Catalog Network hub

| ID | Item | Effort | Notes |
|----|------|--------|-------|
| **E7** | Network hub too busy | M–L | **Done** — Host/External/Network/Adopt → modals; **settings strip above** long path list; Host DNS modal = stacked rows (no wide table) |

E8 already done in micro-pass; residual polish only if needed after E7.

### Stream K — Kuma coverage mobile

| ID | Item | Effort | Notes |
|----|------|--------|-------|
| **E10** | Coverage tables not mobile-friendly | M | **Done** — path + dep gaps as card rows; stacked bind forms; keep audit logic |

### Stream U — Cross-cutting UX consistency

When we touch a surface, apply shared patterns (not a full redesign):

- Secondary actions → **modal/drawer** (Docker / nmap Network edit pattern).  
- Filter/search chrome: one padding + chip + empty-state pattern.  
- Dense lists: **⋯ overflow** for row actions where we already use that pattern.  
- Mobile: tables → cards where decided (E10) and for new dense tables we touch.  
- Wizard micro-copy / edge cases only if capacity (RELEASE_v0.8.0 known issue §1) — **not** a rewrite. **Done** — step guidance, Connect order (install → test → clear), Features HAOS/rsync hints, resume/Save & exit note; no flow rewrite.

North star: residual chrome from RELEASE_v0.8.0 known issue **#3**. Concrete checklist: B2, E1–E5, E7, E10.

### Stream T — Templates light (stretch)

| ID | Item | Effort | Notes |
|----|------|--------|-------|
| **E11 partial** | Badge / group OOTB vs user | S | **Done** — OOTB / Yours badges + section groups when both kinds present; detail badge; pulse ootb/yours |
| **From-host files** | Extra config files + host vars | M | **Done** — relative config mounts (e.g. promtail.yaml) imported; `NODE_NAME` / remote URL vars; editor “Additional files” |

### Stream Q — Quality (primary ship bar)

| ID | Item | Target |
|----|------|--------|
| **Q1** | Unit coverage freeze | **≥ 55%** line (`--cov=app`) — **✅ ~57.4%** suite (baseline 49%; scheduler/audit/backup + certs packs) |
| **Q2** | CI fail-under | **✅ Raised 35 → 45 → 50 → 55** (matches freeze bar) |
| **Q3** | Critical-path depth | Prefer pure services: nmap residual, integrations registry/adapters, fabric/coverage, auth/RBAC edges, helpers for UX we touch |
| **Q4** | E2E on touched UX | **✅** D/N/K chrome: List\|Map Devices, Overview modals, Schedules modal + mobile cards, Runs mobile, Network hub modals + host stacked list, coverage cards |
| **Q5** | HTTP smoke | Extend `test_http_smoke` / seeded surfaces for any new routes |

**E2E policy (0.9):**

- Baseline: shell login/nav, wizard, B6 viewer, nmap LAN shells.  
- **Rule:** code/UX touched in 0.9 → basic E2E in the same PR or release train.  
- Still **no** live SSH, live nmap, or real Home Assistant in CI.  
- Prefer stable selectors (`data-testid` for new chrome when needed).

**Coverage tactics:** pure unit for classifiers, formatters, offline flags, schedule list models; mocked SSH/HA probes. No router-% farming; no 100% target.

**Baseline (v0.8.0 freeze):** ~49–50% line; CI fail-under 35.

### Stream HA — Home Assistant (HAOS / S2) — **Done for 0.9**

Detail: **[FEATURE_PLAN_HOME_ASSISTANT.md](FEATURE_PLAN_HOME_ASSISTANT.md)** (path 1 closed 2026-07-23).

**Shipped:** full **HAOS appliance** over SSH — mark/auto-detect, System Info (Core/OS/Supervisor + disk), OS check/apply via `ha` CLI, host-deps copy, no Docker fleet on HAOS.

| ID | Item | Status |
|----|------|--------|
| **HA0** | Architecture / feature plan | **Done** |
| **HA1** | Mark Server as HAOS + UI chip | **Done** |
| **HA2** | Auto-detect over SSH (`ha` CLI; add-on is Alpine) | **Done** |
| **HA3** | CLI stats + System Info panel | **Done** |
| **HA-upd** | OS update check via CLI | **Done** |
| **HA-upd-apply** | OS upgrade via CLI (opt-in) | **Done** |
| **HA-deps** / **HA-cap** | Deps copy + capability bar | **Done** (wiki install detail optional later) |
| **HA4–HA6** | REST, path 2 component, add-ons | **Deferred** — later release |

**Not reopening in 0.9:** further HA integration (REST/LLAT, container HA, path 2, add-on matrix).

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
| 4 | Stream Q: unit **≥ 55%** (**~57.4%**); CI fail-under **55**; E2E for touched UX **done** |
| 5 | Stream HA: **Done** — S2 HAOS path 1 (mark, CLI stats/System Info, check/apply, deps); further HA deferred |
| 6 | `RELEASE_v0.9.0.md` + tag + Hub |

Stretch: **E11 partial** + **wizard micro-copy** — **Done**.

---

## 5. Roadmap discovery (document only until design)

Capture in [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) / feature plans:

1. **Human-readable schedules (E6)** — shared formatter for interval/cron; advanced = raw cron.  
2. **Selectable hero stats (E9)** — preference model + metric registry.  
3. **Templates catalog redesign (E11)** — OOTB vs user, table/filter, extra config files.  
4. **Home Assistant (post-0.9)** — path 1 REST + S1 container + path 2 component: [FEATURE_PLAN_HOME_ASSISTANT.md](FEATURE_PLAN_HOME_ASSISTANT.md) (0.9 path 1 HAOS/SSH **shipped**).

---

## 6. Key files

| Stream | Files / areas |
|--------|----------------|
| D | `integrations_nmap_detail.html`, `ops-pages.css`, `nmap_device_modal.html`, `server_detail.html`, nmap schedules / device_ops |
| N | `dns_list.html`, fabric partials, `dns.py` as needed for modal partials |
| K | `dns_coverage.html` |
| T | `templates_list.html`, `service_templates/catalog.py` |
| Q | `tests/**`, `e2e/**`, `.github/workflows/test.yml` |
| HA | `FEATURE_PLAN_HOME_ASSISTANT.md`; server `os_type`/HA facts; SSH `ha` probes; `os_patching` HAOS branch; server detail HAOS chrome; onboarding/backup copy |

---

## 7. Changelog (planning)

| Date | Note |
|------|------|
| 2026-07-22 | Plan opened from post-0.8 UX triage; B1/E4/E8 micro-pass for 0.8 tag |
| 2026-07-23 | **Locked as last pre-production:** UX consistency + unit **55%+** + E2E-on-touch + HA discovery stream; [FEATURE_PLAN_HOME_ASSISTANT.md](FEATURE_PLAN_HOME_ASSISTANT.md) |
| 2026-07-23 | **B2** + **E1** landed: shared discovery filter bar; server-detail LAN strip collapsed after dest cards |
| 2026-07-23 | **E2** offline flag: state pill/card/row colour; filter chip Offline; label Offline |
| 2026-07-23 | **E3** Overview: Scan now + vuln pack → modals; status chips/strip remain on page |
| 2026-07-23 | **E5** Schedules list-first + ⋯ menu + add/edit modal |
| 2026-07-23 | **E7** Catalog Network hub: Host/External/Network/Adopt modals |
| 2026-07-23 | **E10** Kuma coverage path/dep gaps → card rows (mobile-friendly) |
| 2026-07-23 | **Q** unit coverage: `test_coverage_v09_pure.py` (schedules, edges, annotations, certs, registry, fabric IP…); CI fail-under **45**; suite ~**50%** |
| 2026-07-23 | **Q** `dns_fabric/core.py` deep pack (`test_dns_fabric_core_coverage.py`) — core ~**76%** (was ~42%) |
| 2026-07-23 | **Q** poll/certs/registry pack — poll ~**92%**, registry ~**78%**, certs ~**51%**; suite ~**54%**; CI fail-under **50** |
| 2026-07-23 | **Q** certificates deep pack (`test_certificates_deep.py`) — edge Caddy, fleet deploy SSH, NPM renew/scheduler; certificates.py ~**93%**; suite ~**54.3%** |
| 2026-07-23 | **E11 partial** — OOTB/Yours source badges + catalog groups; **wizard micro-copy** polish (Connect order, Features/HAOS, resume note); **from-host** additional files + NODE_NAME/remote URL vars (grafana-monitoring / promtail) |
| 2026-07-23 | **Q** scheduler sync + audit_format branches + backup status helpers — suite **~56.1%** (**≥ 55%** freeze bar); scheduler ~**90%**, audit_format ~**97%**, backup pure ~**53%** |
| 2026-07-23 | **Maps** Host/Path: second-click unlocks focus; stack expand anchors to compact dual layout when Discovered off |
| 2026-07-23 | **UX pack** Devices List\|Map merge; LAN chip return-to-server; Overview shortcuts trimmed; Schedules/Runs mobile cards; Network hub settings placement; Host DNS stacked modal; server detail skip Pi-hole probe; backups drop recursive `du` + Full configure button |
| 2026-07-23 | **Q2** CI fail-under **55**; suite remeasure **~57.4%**; **Q4** Playwright: Devices List\|Map + legacy `tab=network`, Runs/Schedules mobile cards, Host DNS list, coverage empty/cards — **18 e2e passed** |
| 2026-07-23 | Operator testing in progress — wiki/docs updated for 0.9 chrome (no tag yet) |
| 2026-07-23 | **HA scope lock:** **S2 Full HAOS over SSH** only — auto-mark + CLI stats; deps SSH add-on + rsync; no Docker fleet mgmt; port not identity; S1/REST later |
| 2026-07-23 | **HA-upd:** OS check/apply for HAOS via `ha core|os|supervisor` CLI (apt equivalent); reuse `os_patch_enabled` + jobs; apply order supervisor→core→os |
| 2026-07-23 | **HA implement:** `haos.py` + check/apply branch + auto-mark + host deps `ha`/rsync hints + server HAOS chrome; unit tests |
| 2026-07-23 | **HA System Info** fixed/live-verified (JSON `data` unwrap, host disk usage); **stream HA closed for 0.9** — deeper integration later |
| 2026-07-23 | Docs/wiki sweep: ADMIN, README, API; wiki **HAOS hosts** + nav; scenarios C2; screenshots recapture list; no push |
| 2026-07-23 | Wiki full pass: wizard step table, templates OOTB/from-host, Journey D2, screenshots **operator testing in progress**, testing.md; no push |

**End of plan** — living document; freeze into RELEASE notes at tag.
