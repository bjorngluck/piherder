# PiHerder v1.14.0: catch up a missed slot, warn on Drive OAuth, and summarise the fleet

**Status:** **Not built.** Train opened 2026-10-24. Nothing is coded. No tag.  
**Date opened:** 2026-10-24  
**Git branch:** `v1.14.0-dev`  
**Package / image version:** package stays **`1.12.1`**. About / footer stay **1.12.1**. This train does not bump the package.  
**Theme:** catch up a missed slot, warn on Drive OAuth, and summarise the fleet  
**Baseline:** `v1.13.0-dev` tip (not tagged). Production image stays **v1.12.1**.  
**Mode:** **Must → Should.** Build order is [#62](https://github.com/bjorngluck/piherder/issues/62), [#63](https://github.com/bjorngluck/piherder/issues/63), [#61](https://github.com/bjorngluck/piherder/issues/61), then [#64](https://github.com/bjorngluck/piherder/issues/64).  
**QA:** not opened.  
**Related:** [PLAN_v1.13.0.md](PLAN_v1.13.0.md) · [RELEASE_v1.12.1.md](RELEASE_v1.12.1.md)

> Opened 2026-10-24 from the `v1.13.0-dev` tip. `v1.13.0-dev` is frozen for new scope. Package stays `1.12.1`. The public demo is not pointed at this branch. Plugin **0.5.2** stays the card. Adapter **[0.4.3](https://github.com/bjorngluck/piherder-mcp/releases/tag/v0.4.3)** stays the published adapter.

---

## 0. Intent

v1.13.0-dev holds restore ownership, the NIC change hint, host tool bootstrap, and the Docker prune follow-ups. This train does not add to that branch. It explains a missed schedule slot and can catch up only the safe kinds, warns when Google Drive OAuth is failing or ageing in testing mode, and adds a read-only fleet summary. Mute rules come after those three. Must blocks the tag. Should can slip.

---

## 1. Decision lock

Build order: **#62, #63, #61, then #64.**

### Schedules

| Tier | Lock |
|------|------|
| Must | **Missed slot catch-up and why it was missed.** Issue [#62](https://github.com/bjorngluck/piherder/issues/62). Opt-in per schedule. Safe kinds only: image checks, update checks, and backups. One catch-up per missed slot, idempotent, no overlap with a run that is already active, and skip the slot when a newer run of that schedule has already finished. Each catch-up is audited. Never patch, redeploy, or prune. The same work records why the slot was missed |

### Drive

| Tier | Lock |
|------|------|
| Must | **Google Drive OAuth health.** Issue [#63](https://github.com/bjorngluck/piherder/issues/63). Alert when a refresh fails. Warn from day 5 when a testing-mode token is ageing. The alert carries presence and age only. Never log the token |

### Fleet read

| Tier | Lock |
|------|------|
| Must | **Fleet summary.** Issue [#61](https://github.com/bjorngluck/piherder/issues/61). Read-only `GET /api/v1/fleet/summary?since=` and hosted MCP tool `fleet_summary`. `since=` is the changes-since-last-check view. Counts and states only. Token `read` scope. Audited and deduped |

### Alerts

| Tier | Lock |
|------|------|
| Should | **Mute rules.** Issue [#64](https://github.com/bjorngluck/piherder/issues/64). One model, scoped to a host or an alert type. An optional time window must have an end time. Mute availability alerts only. Never skip schedules. Audited. Auth, OAuth, and backup failures still fire. Sensor-level ignores stay in Prometheus |

| Topic | Lock |
|-------|------|
| Version bump | Package stays **1.12.1** until freeze. About stays **1.12.1** |
| Demo | The public demo is not pointed at this branch |
| Plugin | The Home Assistant card stays the separate HACS repo |
| Adapter | Published adapter stays **0.4.3** |
| Issues | [#62](https://github.com/bjorngluck/piherder/issues/62), [#63](https://github.com/bjorngluck/piherder/issues/63), and [#61](https://github.com/bjorngluck/piherder/issues/61) are Must. [#64](https://github.com/bjorngluck/piherder/issues/64) is Should. They stay open until the release |
| Frozen branch | Do not add this scope to `v1.13.0-dev` |

### Out

| Item | Why it is out |
|------|----------------|
| Per-host sensor ignore | Prometheus relabel config, not PiHerder. Sensor-level ignores stay there |
| Per-target scrape status | Grafana owns it. PiHerder does not call the Prometheus API. At most a later per-host Grafana link template |

---

## 2. Capture log

| Date | What changed |
|------|----------------|
| 2026-10-24 | **Train opened** from the `v1.13.0-dev` tip. `v1.13.0-dev` stays frozen for new scope. Must, in build order: [#62](https://github.com/bjorngluck/piherder/issues/62) missed-slot catch-up for checks and backups, plus why the slot was missed; [#63](https://github.com/bjorngluck/piherder/issues/63) Google Drive OAuth health; [#61](https://github.com/bjorngluck/piherder/issues/61) `GET /api/v1/fleet/summary?since=` and hosted `fleet_summary`. Should: [#64](https://github.com/bjorngluck/piherder/issues/64) mute rules. Out: Prometheus sensor ignore, and scrape status that Grafana already owns. Package stays `1.12.1`. Nothing is built. |

---

## 3. Next

| # | Step | Status |
|---|------|--------|
| 1 | Open **`v1.14.0-dev`** | **Done** 2026-10-24 |
| 2 | Must, in order #62, #63, #61 | Not started |
| 3 | Should #64, if it fits | Not started |
| 4 | Freeze | Not set. Package stays **1.12.1** |
| 5 | Operator QA | Not opened |
| 6 | Tag and Hub | Not cut |

---

*Opened 2026-10-24. Plan only. Package stays 1.12.1. Not tagged.*
