# PiHerder v1.14.0: catch up a missed slot, warn on Drive OAuth, summarise the fleet, and fix the mobile console

**Status:** **Not built.** Voted 2026-10-27. Nothing is coded. No tag.  
**Date opened:** 2026-10-24 (draft on the unreleased `v1.13.0-dev` tip). **Voted and restarted from `main`:** 2026-10-27.  
**Git branch:** `v1.14.0-dev`  
**Package / image version:** package stays **`1.13.0`**. About / footer stay **1.13.0**. This train does not bump the package.  
**Theme:** catch up a missed slot, warn on Drive OAuth, summarise the fleet, and fix the mobile console  
**Baseline:** tagged **`v1.13.0`** (2026-10-26). Production image is **1.13.0**.  
**Mode:** **Must → Should.** Tiers were voted 2026-10-27. Nothing was voted Discover.  
**QA:** not opened.  
**Related:** [PLAN_v1.13.0.md](PLAN_v1.13.0.md) · [RELEASE_v1.13.0.md](RELEASE_v1.13.0.md)

> Restarted 2026-10-27 from `main` after **v1.13.0** shipped. Package stays `1.13.0`. The public demo is not pointed at this branch. Plugin **[0.5.3](https://github.com/bjorngluck/piherder-ha/releases/tag/v0.5.3)** stays the card until the brand cleanup cuts a plugin release. Adapter **[0.4.3](https://github.com/bjorngluck/piherder-mcp/releases/tag/v0.4.3)** stays the published adapter until the Must adapter cut. Open issues were checked the same day. [#50](https://github.com/bjorngluck/piherder/issues/50) is already in v1.13.0. [#61](https://github.com/bjorngluck/piherder/issues/61), [#62](https://github.com/bjorngluck/piherder/issues/62), [#63](https://github.com/bjorngluck/piherder/issues/63), and [#64](https://github.com/bjorngluck/piherder/issues/64) are not in the code and are on this train. piherder-mcp has no open issues. [piherder-ha#6](https://github.com/bjorngluck/piherder-ha/issues/6) is in the plugin tree on `fix/drop-root-brand`. The four root copies are removed. `brand/` stays. Plugin stays **0.5.3**. Not pushed.

---

## 0. Intent

v1.13.0 shipped restore ownership, the NIC change hint, host tool bootstrap, and the Docker prune follow-ups. This train catches up a missed backup or image check, warns when Google Drive OAuth is failing or ageing in testing mode, adds a read-only fleet summary beside the existing heartbeat, and fixes the mobile console. The same vote adds mute rules, restore safety, a compose drift badge, volume prune on the API, and the HACS install wording. Must blocks the tag. Should can slip.

---

## 1. Decision lock

Build order for Must: [#62](https://github.com/bjorngluck/piherder/issues/62), [#63](https://github.com/bjorngluck/piherder/issues/63), [#61](https://github.com/bjorngluck/piherder/issues/61), the mobile console, then the 85% coverage gate, the plugin brand cleanup, and the adapter tag last.

### Schedules

| Tier | Lock |
|------|------|
| Must | **Missed slot catch-up and why it was missed.** Issue [#62](https://github.com/bjorngluck/piherder/issues/62). In the code. Opt-in per schedule, off by default. Allowed kinds are backups (`backup`, alert `backup_schedule_missed`) and image checks (`container_update_check`, alert `container_check_missed`). OS update checks, OS patch, redeploy, and `docker_prune` are rejected even if a caller opts in. One catch-up per missed slot, idempotent, no overlap with a run that is already active, and skip the slot when a newer run of that schedule has already finished. The catch-up uses the normal exclusive job so it appears in Job history. Each catch-up is audited. The job records why the slot was missed: `scheduler_down`, `overlap`, `clock`, `error`, or `unknown`. A successful catch-up auto-resolves the matching `*_missed` alert. Prometheus counter `piherder_schedule_catchup_total{kind,result}` with `kind` of `backup` or `container_check` and `result` of `ran`, `skipped`, or `failed` |

### Drive

| Tier | Lock |
|------|------|
| Must | **Google Drive OAuth health.** Issue [#63](https://github.com/bjorngluck/piherder/issues/63). In the code. No new credential store. Record the time the refresh token was saved. That timestamp is not the token. `drive_oauth_refresh_failed` (critical) on refresh failure. A destination checkbox, **Google app is in Testing**, defaults off. When it is on and the saved token is older than 5 days, `drive_oauth_token_aging` (warning). A missing save time does not fire the ageing warning. The alert carries presence and age only. Never log, display, or include the token. These two types cannot be muted. Either resolves on a successful refresh or a new token |

### Fleet read

| Tier | Lock |
|------|------|
| Must | **Fleet summary.** Issue [#61](https://github.com/bjorngluck/piherder/issues/61). In the code. Read-only `GET /api/v1/fleet/summary?since=` and hosted MCP tool `fleet_summary`. This does not replace `GET /api/v1/summary` or hosted `summary`. `since=` is an ISO-8601 timestamp. Omitted means the current snapshot. Set, it returns only areas that changed after that time. An unusable `since` is HTTP 400. Counts and states for hosts, open alerts, failed jobs, backups, Drive replication, and pending updates. Host ids are allowed. No addresses, tokens, webhook URLs, or filesystem paths. Token scope `read`. Audited with `api_read` when read audit is on. No new data store |

### Console

| Tier | Lock |
|------|------|
| Must | **Mobile console.** Deferred this pass. The new bug is not written down, so the console is unchanged. Wiki residual [KI-console-mobile-soft-tab](../wiki/day-to-day/web-ssh-console.md#known-issues): soft Tab on a phone can still flash the IME token. The operator also reports a new bug and a bad experience. Capture that bug before changing the console. Do not invent the symptom |

### Backups

| Tier | Lock |
|------|------|
| Should | **Pre-restore host rollback snapshot.** Before a real restore, take a host snapshot that can undo that restore. Dry-run does not take it |
| Should | **Manifest verification.** A restore is not called good until the file count and the byte count match the manifest |
| Should | **Dry-run restore preview API.** A read that shows what a restore would change. It does not write |

### Docker

| Tier | Lock |
|------|------|
| Should | **Compose git checkout drift badge.** Show when a tracked checkout differs from the remote. No deploy |
| Should | **Volume prune on the API and hosted `/mcp`.** Scope `edit`, an explicit confirm, and one audit row. The Docker page confirm stays. It is not part of scheduled `docker_prune`. If this lands, the tool is on the same adapter tag as `fleet_summary` |

### Alerts

| Tier | Lock |
|------|------|
| Should | **Mute rules.** Issue [#64](https://github.com/bjorngluck/piherder/issues/64). Not in the code. One rule is one host or one alert type. An optional time window must have an end. A window without an end is rejected. Mute delivery only. Schedules still run. Auth failures, OAuth problems (including `drive_oauth_refresh_failed` and `drive_oauth_token_aging`), and backup failures cannot be muted. Creating, editing, and deleting a rule is audited and needs scope `edit`. `read` cannot change a rule. Sensor-level ignores stay in Prometheus. `GET /api/v1/alerts` items gain `muted`. Gauge `piherder_alerts_muted` is the number of active mute rules |

### Plugin

| Tier | Lock |
|------|------|
| Must | **Drop root brand copies.** [piherder-ha#6](https://github.com/bjorngluck/piherder-ha/issues/6). In the plugin tree on `fix/drop-root-brand`. `icon.png`, `icon@2x.png`, `logo.png`, and `logo@2x.png` are removed from the integration root. `brand/` stays. Version stays **0.5.3**. Not tagged and not pushed. Not herder code. The herder tag waits on this landing in piherder-ha |
| Should | **Official HACS install.** [hacs/default#11444](https://github.com/hacs/default/pull/11444) is merged. The plugin README, the HACS badge, and the herder wiki stop saying to add a custom repository. Install is HACS, Integrations, PiHerder |

### Release hygiene

| Tier | Lock |
|------|------|
| Must | **Adapter cut.** Hosted `/mcp` on v1.13.0 already has `list_alerts`, `prune_build_cache`, and `replace_stale_mac`. `fleet_summary` is in this herder tree. The bump is on `feat/adapter-0.4.4` in piherder-mcp and adds those four tools. Package version on that branch is **0.4.4**. Published adapter stays **0.4.3**. The `v*` tag is not cut. piherder-mcp has no open issues |
| Must | **Coverage 85.** CI `--cov-fail-under` is **85** on `app`. Compose report **85%** (49078/57940, 84.70% before rounding). The gate uses the rounded figure |
| Should | **Push-denylist secret.** The workflow is in. This is an operator step: set the GitHub Actions secret. The denylist stays off git. An empty secret must not pretend it checked |

| Topic | Lock |
|-------|------|
| Version bump | Package stays **1.13.0** until freeze. About stays **1.13.0** |
| Demo | The public demo is not pointed at this branch |
| Plugin | The card stays the separate HACS repo. Plugin stays **0.5.3** until that repo cuts the brand cleanup |
| Adapter | Published adapter stays **0.4.3** until the Must cut |
| Issues | [#62](https://github.com/bjorngluck/piherder/issues/62), [#63](https://github.com/bjorngluck/piherder/issues/63), and [#61](https://github.com/bjorngluck/piherder/issues/61) are Must. [#64](https://github.com/bjorngluck/piherder/issues/64) is Should. [piherder-ha#6](https://github.com/bjorngluck/piherder-ha/issues/6) is Must. They stay open until the release. [#50](https://github.com/bjorngluck/piherder/issues/50) is already in v1.13.0 (`nic_change`, Replace stale MAC). It is not rebuilt. Closing it is separate |
| Frozen release | Do not add this scope to the `v1.13.0` tag |

### Later

Fine-grained roles (AC-fg). Stricter command-audit redaction. Per-host Prometheus sensor ignore. Per-target scrape status.

### Removed on 2026-10-27

Do not put these on a future ballot. borg, kopia, rclone crypt, optional AI, Path B, Brand-3, ACME-in-herder, a richer Files token API, M-live, a herder to Home Assistant webhook, undo of a finished Move, MCP undo, the console as an MCP tool, token admin on MCP, `docker_stack_down`, `docker_stack_remove`, restic, SMB Kerberos, selectable hero stats, a templates catalog redesign, Ansible / cloud-init, Discord / Discussions, Swarm / Kubernetes, CodeQL, Actions pinned to commit SHAs, `CODEOWNERS`, and putting the Home Assistant plugin in the image.

Nothing else was voted **Out**.

---

## 2. Capture log

| Date | What changed |
|------|----------------|
| 2026-10-24 | **Train drafted** from the unreleased `v1.13.0-dev` tip. Must, in build order: [#62](https://github.com/bjorngluck/piherder/issues/62), [#63](https://github.com/bjorngluck/piherder/issues/63), [#61](https://github.com/bjorngluck/piherder/issues/61). Should: [#64](https://github.com/bjorngluck/piherder/issues/64). Package was still `1.12.1`. Nothing was built. |
| 2026-10-24 | **HACS default merged.** [hacs/default#11444](https://github.com/hacs/default/pull/11444). [piherder-ha#6](https://github.com/bjorngluck/piherder-ha/issues/6) drops the root icon and logo copies. Install docs point at the default HACS integration. Nothing was built. |
| 2026-10-27 | **Vote.** Restarted from shipped `v1.13.0`. Package stays `1.13.0`. Must: [#62](https://github.com/bjorngluck/piherder/issues/62), [#63](https://github.com/bjorngluck/piherder/issues/63), [#61](https://github.com/bjorngluck/piherder/issues/61), [piherder-ha#6](https://github.com/bjorngluck/piherder-ha/issues/6), one adapter tag, coverage fail-under **85**, and the mobile console. Should: [#64](https://github.com/bjorngluck/piherder/issues/64), HACS install docs, a pre-restore rollback snapshot, manifest file-count and byte verification, a dry-run restore preview API, a compose git drift badge, the push-denylist secret, and volume prune on the API and hosted `/mcp`. Later: fine-grained roles, command-audit redaction, Prometheus sensor ignore, scrape status. A removed set stays off future ballots. Nothing is built. |
| 2026-10-27 | **Coverage gate.** Packs `tests/test_coverage_v114_*.py`. Compose report **85%** (49078/57940). CI `--cov-fail-under` raised **80** to **85**. Two older failures stay: the git inspect test needs `git` on PATH, and the herder self-backup scheduler test. |
| 2026-10-27 | **Open issues checked.** [#50](https://github.com/bjorngluck/piherder/issues/50) is already in v1.13.0 (`app/services/nmap/nic_change.py`, Replace stale MAC). It stays open on GitHub and is not on this train. [#61](https://github.com/bjorngluck/piherder/issues/61), [#62](https://github.com/bjorngluck/piherder/issues/62), [#63](https://github.com/bjorngluck/piherder/issues/63), and [#64](https://github.com/bjorngluck/piherder/issues/64) have no code yet and are on this train. piherder-mcp has no open issues. [piherder-ha#6](https://github.com/bjorngluck/piherder-ha/issues/6) still has the four root brand files. It is Must. |
| 2026-10-27 | **Mobile console deferred.** The new phone bug is not written down. The console is unchanged. **Plugin brand.** On `fix/drop-root-brand`, the four integration-root copies are removed. `brand/` stays. Plugin stays **0.5.3**. |
| 2026-10-27 | **Adapter bump.** `feat/adapter-0.4.4` adds `list_alerts`, `prune_build_cache`, `replace_stale_mac`, and `fleet_summary`. Version on that branch is **0.4.4**. Published adapter stays **0.4.3**. The `v*` tag is not cut. Volume prune is not on this bump. |

---

## 3. Next

| # | Step | Status |
|---|------|--------|
| 1 | Open **`v1.14.0-dev`** on shipped `main` | **Done** 2026-10-27 |
| 2 | Must, in order #62, #63, #61, mobile console, coverage 85, plugin brand, adapter tag | In progress. Coverage 85 is pushed. #62, #63, and #61 are in the herder tree. Mobile console waits. Plugin brand is on `fix/drop-root-brand`. Adapter bump is on `feat/adapter-0.4.4`. The adapter tag is not cut |
| 3 | Should, if it fits | Not started |
| 4 | Freeze | Not set. Package stays **1.13.0** |
| 5 | Operator QA | Not opened |
| 6 | Tag and Hub | Not cut |

---

*Voted 2026-10-27 on tagged v1.13.0. Plan only. Package stays 1.13.0. Not tagged.*
