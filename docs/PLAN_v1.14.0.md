# PiHerder v1.14.0: catch up a missed slot, warn on Drive OAuth, summarise the fleet, and fix the mobile console

**Status:** **In progress** on `v1.14.0-dev`. Voted 2026-10-27. Package stays `1.13.0`. No tag.  
**Date opened:** 2026-10-24 (draft on the unreleased `v1.13.0-dev` tip). **Voted and restarted from `main`:** 2026-10-27.  
**Git branch:** `v1.14.0-dev`  
**Package / image version:** package stays **`1.13.0`**. About / footer stay **1.13.0**. This train does not bump the package.  
**Theme:** catch up a missed slot, warn on Drive OAuth, summarise the fleet, and fix the mobile console  
**Baseline:** tagged **`v1.13.0`** (2026-10-26). Production image is **1.13.0**.  
**Mode:** **Must → Should.** Tiers were voted 2026-10-27. Nothing was voted Discover.  
**QA:** not opened.  
**Related:** [PLAN_v1.13.0.md](PLAN_v1.13.0.md) · [RELEASE_v1.13.0.md](RELEASE_v1.13.0.md)

> Restarted 2026-10-27 from `main` after **v1.13.0** shipped. Package stays `1.13.0`. No tag. The public demo is not pointed at this branch. Plugin **[0.5.4](https://github.com/bjorngluck/piherder-ha/releases/tag/v0.5.4)** is the card. Adapter **[0.4.4](https://github.com/bjorngluck/piherder-mcp/releases/tag/v0.4.4)** is tagged. **0.4.3** stays the previous package. [#50](https://github.com/bjorngluck/piherder/issues/50) is already in v1.13.0. [#61](https://github.com/bjorngluck/piherder/issues/61), [#62](https://github.com/bjorngluck/piherder/issues/62), [#63](https://github.com/bjorngluck/piherder/issues/63), and [#64](https://github.com/bjorngluck/piherder/issues/64) are in the herder tree and stay open until the release. [piherder-ha#6](https://github.com/bjorngluck/piherder-ha/issues/6) is merged and tagged **v0.5.4**. The four root copies are removed. `brand/` stays. Restore safety in the tree: a pre-restore snapshot, manifest file and byte checks, and `GET /api/v1/servers/{id}/backup/restore-preview`. Compose git drift is in the tree. The stack list compares a checkout with the last fetched origin and does not deploy. Volume prune is in the tree: `POST /api/v1/servers/{id}/docker/volumes` and hosted `prune_volumes` need `confirm` true and write one audit row. They are not the scheduled prune. Adapter **0.4.4** does not list that tool. The push-denylist workflow is in. Secret `PIHERDER_PUSH_DENYLIST` is not set. Until it has at least one pattern, the workflow prints that the manual grep remains and exits 0. It does not report a clean scan. Patterns stay off git.

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
| Must | **Mobile console.** The suggestion bar, a word hidden until Enter, and scrambled Tab were reproduced with Samsung Keyboard on Android Chrome. The same console with Gboard did not reproduce them. A live preview and two later input changes were tried and reverted the same day. They made phone typing worse. Shell input is the committed console again. The short phone sheet no longer hides when the dimmed area is tapped, and it moves above the keyboard. Wiki: [KI-console-samsung-keyboard](../wiki/day-to-day/web-ssh-console.md#ki-console-samsung-keyboard) |

### Backups

| Tier | Lock |
|------|------|
| Should | **Pre-restore host rollback snapshot.** In the tree. Before a real restore, copy the current host folder aside, with owners, so Undo last restore can put it back. A failed snapshot does not start the restore. Dry-run does not take it |
| Should | **Manifest verification.** In the tree. A restore is not called good until the host file count and the byte count match the sidecar. Counts are regular files only. An older sidecar without those counts still restores. Dry-run does not check |
| Should | **Dry-run restore preview API.** In the tree. `GET /api/v1/servers/{id}/backup/restore-preview` lists the files a restore would send. Scope `read`. It does not write, and it is not a hosted MCP tool |

### Docker

| Tier | Lock |
|------|------|
| Should | **Compose git checkout drift badge.** In the tree. Show when a tracked checkout differs from the last fetched origin. The inventory probe does not fetch and does not deploy |
| Should | **Volume prune on the API and hosted `/mcp`.** In the tree. Scope `edit`, an explicit confirm, and one audit row (`docker_volume_prune`). The Docker page confirm stays. It is not part of scheduled `docker_prune`. Not on adapter **0.4.4**. A later adapter tag carries it if it lands |

### Alerts

| Tier | Lock |
|------|------|
| Should | **Mute rules.** Issue [#64](https://github.com/bjorngluck/piherder/issues/64). In the tree (Alembic `053_alert_mute` is not applied to a live database). One rule is one host or one alert type. An optional time window must have an end. A window without an end is rejected. Mute delivery only. Schedules still run. Auth failures, OAuth problems (including `drive_oauth_refresh_failed` and `drive_oauth_token_aging`), and backup failures cannot be muted. Creating, editing, and deleting a rule is audited and needs scope `edit`. `read` cannot change a rule. Sensor-level ignores stay in Prometheus. `GET /api/v1/alerts` items gain `muted`. Gauge `piherder_alerts_muted` is the number of active mute rules |

### Plugin

| Tier | Lock |
|------|------|
| Must | **Drop root brand copies.** [piherder-ha#6](https://github.com/bjorngluck/piherder-ha/issues/6). Merged. `icon.png`, `icon@2x.png`, `logo.png`, and `logo@2x.png` are removed from the integration root. `brand/` stays. Tagged **[v0.5.4](https://github.com/bjorngluck/piherder-ha/releases/tag/v0.5.4)**. Not herder code. The herder package tag still waits |
| Should | **Official HACS install.** [hacs/default#11444](https://github.com/hacs/default/pull/11444) is merged. The plugin README, the HACS badge, and the herder wiki say HACS, Integrations, PiHerder. They no longer say to add a custom repository. Plugin version stays **0.5.4**. No new plugin tag |

### Release hygiene

| Tier | Lock |
|------|------|
| Must | **Adapter cut.** Done. Tag **[v0.4.4](https://github.com/bjorngluck/piherder-mcp/releases/tag/v0.4.4)** is on merge `30bfa2f` (pull request 9). It adds `fleet_summary`, `list_alerts`, `prune_build_cache`, and `replace_stale_mac`. Volume prune is not in this package. **0.4.3** stays the previous tag. piherder-mcp has no open issues |
| Must | **Coverage 85.** CI `--cov-fail-under` is **85** on `app`. Compose report **85%** (49078/57940, 84.70% before rounding). The gate uses the rounded figure |
| Should | **Push-denylist secret.** Workflow is in (`.github/workflows/push-denylist.yml`). Secret `PIHERDER_PUSH_DENYLIST` is not set. One literal per line, at least 4 characters. Comments and blank lines are ignored. An empty secret prints that the manual grep remains and exits 0. It does not report a clean scan. Patterns stay off git |

| Topic | Lock |
|-------|------|
| Version bump | Package stays **1.13.0** until freeze. About stays **1.13.0** |
| Demo | The public demo is not pointed at this branch |
| Plugin | The card stays the separate HACS repo. Plugin **0.5.4** is tagged. **0.5.3** stays the previous tag |
| Adapter | Tag **v0.4.4** is cut. **0.4.3** stays the previous tag |
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
| 2026-10-27 | **Plugin brand.** Pull request 8 is merged. The four integration-root copies are removed. `brand/` stays. Tagged **v0.5.4**. **0.5.3** was not moved. |
| 2026-10-27 | **Adapter bump.** `feat/adapter-0.4.4` adds `list_alerts`, `prune_build_cache`, `replace_stale_mac`, and `fleet_summary`. Version on that branch is **0.4.4**. Published adapter stays **0.4.3**. The `v*` tag is not cut. Volume prune is not on this bump. |
| 2026-10-27 | **Adapter docs.** Version **0.4.4**, the hand-typed badges, and the release notes are on [piherder-mcp#9](https://github.com/bjorngluck/piherder-mcp/pull/9). Herder notes name the four tools. The herder MCP badge stays **v0.4.3** until tag **v0.4.4** exists. Volume prune stays off this tag. |
| 2026-10-27 | **Adapter tag.** Pull request 9 is merged as `30bfa2f`. Annotated tag **v0.4.4** points at that merge. Volume prune stays off this tag. |
| 2026-10-27 | **Mobile console.** Three symptoms are written down: the suggestion bar blocks typing, the word appears on Enter, and Tab completion is wrong. A live preview and two later input changes were tried and reverted the same day. They made phone typing worse. Shell input is the committed console again. The phone sheet no longer hides on an outside tap. The same phone then compared keyboards. Samsung Keyboard reproduced the three failures. Gboard did not. Written up as **KI-console-samsung-keyboard**. |
| 2026-10-27 | **Mute rules.** [#64](https://github.com/bjorngluck/piherder/issues/64) is in the tree. One host or one alert type, optional window, delivery only. Alembic `053_alert_mute` is not applied to a live database. The issue stays open. |
| 2026-10-27 | **HACS install wording.** Install is HACS, Integrations, PiHerder. The plugin README and the herder wiki no longer say to add a custom repository. Plugin version stays **0.5.4**. No new plugin tag. |
| 2026-10-27 | **Pre-restore snapshot.** A real restore copies the current host folder aside first. Undo writes that copy back. Dry-run does not take the snapshot. |
| 2026-10-27 | **Manifest verification.** A new sidecar records the regular-file count and the byte count. A real restore is good only when the host matches both. An older sidecar without those counts still restores. Dry-run does not check. |
| 2026-10-27 | **Dry-run restore preview.** `GET /api/v1/servers/{id}/backup/restore-preview` lists the files a restore would send. It does not write. It is not a hosted MCP tool. |
| 2026-10-27 | **Docs.** Wiki, [API.md](API.md), [SPEC.md](../SPEC.md), and the roadmap name the open train. Package stays **1.13.0**. Not tagged. The restore notes say a 1.13 sidecar puts the owner back, and this train also checks file count and byte count. |
| 2026-10-28 | **Compose git drift.** The Docker stack list shows **Git drift** when a checkout is behind the last fetched origin branch, ahead of it, or has tracked edits. The inventory probe does not fetch and does not deploy. |
| 2026-10-28 | **Volume prune.** `POST /api/v1/servers/{id}/docker/volumes` and hosted `prune_volumes` prune unused volumes when `confirm` is true. One audit row, `docker_volume_prune`. Not a `docker_prune` job and not scheduled. The Docker page confirm stays. Adapter **0.4.4** does not list the tool. |
| 2026-10-28 | **Push denylist.** The workflow is in. Secret `PIHERDER_PUSH_DENYLIST` is not set. An empty secret does not report a clean scan. Patterns stay off git. |

---

## 3. Next

| # | Step | Status |
|---|------|--------|
| 1 | Open **`v1.14.0-dev`** on shipped `main` | **Done** 2026-10-27 |
| 2 | Must, in order #62, #63, #61, mobile console, coverage 85, plugin brand, adapter tag | **Done** 2026-10-27. Plugin **v0.5.4** is tagged. Adapter **v0.4.4** is tagged. Volume prune is not on that tag. Samsung Keyboard is KI-console-samsung-keyboard |
| 3 | Should, if it fits | Mute rules ([#64](https://github.com/bjorngluck/piherder/issues/64)) are in the tree. HACS install wording is in the tree. Plugin version stays **0.5.4**. A pre-restore snapshot is in the tree. Manifest file-count and byte verification is in the tree. The dry-run restore preview API is in the tree. The compose git drift badge is in the tree. Volume prune is in the tree. Adapter **0.4.4** does not list it. The push-denylist workflow is in. Secret `PIHERDER_PUSH_DENYLIST` is not set |
| 4 | Freeze | Not set. Package stays **1.13.0** |
| 5 | Operator QA | Not opened |
| 6 | Tag and Hub | Not cut |

---

*Voted 2026-10-27 on tagged v1.13.0. In progress on `v1.14.0-dev`. Package stays 1.13.0. Not tagged.*
