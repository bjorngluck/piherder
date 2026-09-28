# PiHerder v1.8.0 — Home Assistant cards

**Status:** **Active** (train opened 2026-09-28). No product code yet.  
**Date opened:** 2026-09-28  
**Git branch:** `v1.8.0-dev` → `main` · tag `v1.8.0` at freeze  
**Package / image version:** stays **`1.7.0`** until freeze  
**Theme:** richer Home Assistant cards and the 24-hour chart that does not draw on plugin **0.3.0**  
**Baseline:** `v1.7.0` (tagged 2026-09-28; Hub digest `sha256:174cb1313f6717d323211c8c899b30240e97f5097bd35770f7a6c4555de95270`)  
**Mode:** **Must → Should → Discover.** Must **HA-vis** (richer cards + working 24-hour series). Discover stays parked.  
**QA:** [QA_v1.8.0.md](QA_v1.8.0.md) (maintainer stub — **not** the operator wiki)  
**Related:** [PLAN_v1.7.0.md](PLAN_v1.7.0.md) · [RELEASE_v1.7.0.md](RELEASE_v1.7.0.md) · [FEATURE_PLAN_HOME_ASSISTANT.md](FEATURE_PLAN_HOME_ASSISTANT.md) · wiki [Home Assistant](../wiki/integrations/home-assistant.md)

> **Train open 2026-09-28.** Production stays **v1.7.0** on `main`. Package stays **`1.7.0`** until freeze. Plugin work lands in [bjorngluck/piherder-ha](https://github.com/bjorngluck/piherder-ha), not in this image. Do not redeploy the public demo onto this branch.

---

## 0. Intent

Plugin **0.3.0** shipped with v1.7.0: host, updates, and resources cards, plus confirm actions. The cards are plain. The host 24-hour sparkline and the resources 24-hour series do not draw. The points, when they draw, are Home Assistant history of the snapshot sensors (about every 15 minutes), not a live SSH chart.

Wanted:

1. Cards that are easier to read at a glance: clearer gauges, stats, and layout on the same three card types  
2. The 24-hour host sparkline and the resources series actually draw from HA history  
3. Same token rules as 0.3.0 (`read` sensors only; `jobs` confirms; `edit` toggles). No new herder route unless a walk proves the history the sensors already publish is not enough  

**Out until promoted:** the HA job-finished bus event, Slice 3 (container start/stop, webhooks, Move-from-HA, Files), plugin-in-image, Bak-alt, AC-fg, Undo-2, Mux-2.

---

## 1. Decision lock (train open)

| Choice | Value |
|--------|--------|
| Integration branch | **`v1.8.0-dev`** |
| Production line | **`main` @ `v1.7.0`** — hotfixes → **`v1.7.x`**, port here |
| Git tag (freeze) | **`v1.8.0`** |
| Image tags (freeze) | `1.8.0` · `1.8` · `latest` (multi-arch); keep `1.7` / `1.7.x` pins valid |
| In-scope | **HA-vis** Must — plugin repo. This repo tracks the operator wiki and QA |
| Discover (no code until promoted) | **AC-fg** · HA `piherder_job_completed` bus event · Undo-2 · Mux-2 · **Bak-alt** · HA Slice 3 |
| Version bump | Freeze only. About / footer stay **1.7.0** until then |
| Demo | Stays the published **1.7.0** image. Do not redeploy this branch |

---

## 2. Capture log

| Date | Note |
|------|------|
| 2026-09-28 | Train opened from `main` after **v1.7.0** shipped. Must is the visual pass and the 24-hour chart on the HACS cards. Package stays `1.7.0`. |

---

## 3. Next

| # | Step | Status |
|---|------|--------|
| 1 | Open **`v1.8.0-dev`** | **Done** 2026-09-28 |
| 2 | Design the card pass in piherder-ha and tick [QA_v1.8.0.md](QA_v1.8.0.md) while walking | Not started |
| 3 | Freeze · `1.8.0` · tag · Hub | Only when asked |
