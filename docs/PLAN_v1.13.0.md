# PiHerder v1.13.0: ownership on restore, and a hint when a MAC changes

**Status:** **Opened** 2026-10-23. Team review the same day widened the lock. Not built. No tag.  
**Date opened:** 2026-10-23  
**Git branch:** `v1.13.0-dev`  
**Package / image version:** package stays **`1.12.1`**. About / footer stay **1.12.1**. This train does not bump the package.  
**Theme:** ownership on restore, and a hint when a MAC changes  
**Baseline:** `v1.12.1` (tagged 2026-10-23)  
**Mode:** **Must → Should.** Tiers were voted 2026-10-23. Nothing was voted Discover, Later, or Out.  
**QA:** [QA_v1.13.0.md](QA_v1.13.0.md) (maintainer stub. **Not** the operator wiki.)  
**Related:** [PLAN_v1.12.0.md](PLAN_v1.12.0.md) · [RELEASE_v1.12.1.md](RELEASE_v1.12.1.md) · [RELEASE_v1.12.0.md](RELEASE_v1.12.0.md) · wiki [Backups](../wiki/day-to-day/backups.md) · wiki [LAN Discovery](../wiki/integrations/lan-discovery.md)

> Opened 2026-10-23 from `main` after **v1.12.1** shipped. Package stays `1.12.1`. The public demo is not pointed at this branch. Plugin **0.5.2** stays the card. Adapter **[0.4.3](https://github.com/bjorngluck/piherder-mcp/releases/tag/v0.4.3)** stays the published adapter. This train bumps it to **0.4.4** for build-cache prune. Open issues were reviewed the same day. PiHerder has one open issue, [#50](https://github.com/bjorngluck/piherder/issues/50). piherder-ha and piherder-mcp have none. PiHerder has no open pull request.

---

## 0. Intent

v1.12.1 is a certifi patch on v1.12.0. v1.12.0 shipped remote restore and Docker prune. This train puts the Unix owner back on a restore, names a possible NIC change, and can replace the stale MAC in one audited step. A new token secret stays off the redirect query string. The same vote adds Docker prune follow-ups, a stuck queue, a read-only alerts read, and a docs sweep. The areas below are the vote. Must blocks the tag. Should is built on this train, and the tag can ship if that walk slips.

---

## 1. Decision lock

### Backups

| Tier | Lock |
|------|------|
| Must | **Restore ownership.** Record numeric `uid`, `gid`, and octal `mode` in a sidecar manifest when a tree is backed up. After the pull, restore that numeric owner, group, and mode on the host. Strip `setuid` and `setgid` (`mode & 0o1777`). Drive, OneDrive, and a LAN share do not keep a Unix owner. The pull lands as the account that runs rclone. A named volume that must belong to the app user can come back as the wrong account until this step runs. The container may then fail to start |
| Should | **Backup stagger ceiling.** Spread host backup minutes across all 1,440 minutes in a day before two hosts share a minute slot |

### Discovery

| Tier | Lock |
|------|------|
| Must | **NIC change.** Issue [#50](https://github.com/bjorngluck/piherder/issues/50). When a scan inserts a MAC identity, and another non-ignored row has the same hostname and the same IP and has been stale for longer than one scan interval, show a "possible NIC change" notice. The notice is a Discovery badge and a `lan_discovery` notification at `info`. Device identity stays the MAC. Rows are not merged and are not auto-ignored. A one-click **Replace stale MAC** action marks that stale row `ignored` and copies the custom display name, kind override, and notes onto the new MAC row. That step writes one audit row. The issue stays open until the release |

### Docker

| Tier | Lock |
|------|------|
| Should | **Build cache.** The host Docker page, the token API, and hosted MCP can prune the Docker build cache and the buildx cache. `edit` scope on the API and MCP. One audit row per run. It is not part of `docker_prune`. No schedule on this action |
| Should | **Prune on a schedule.** A per-host saved schedule starts the same prune the API starts by hand. Volumes stay off. Build cache stays its own action. The job is listed in About **View schedule** (`scheduler_visibility.py`) and in the missed-schedule watch (`schedule_watch.py`) |
| Should | **Prune after an update.** At the end of a successful `container_patch` or redeploy, run `docker image prune -f` inline. Dangling images only. Not volumes, not exited containers, and not the build cache |
| Should | **Expire stuck inventory refresh.** If `docker_inventory_status` stays `refreshing` for 30 minutes, reset it, the same way a stuck `host_facts` refresh is cleared. A dropped background refresh must not stay stuck |
| Should | **Per-project `update_available` in the inventory API.** The Docker inventory API names which compose projects and images have an update. Callers do not scrape `container_update_check` job logs for that |

### Alerts

| Tier | Lock |
|------|------|
| Should | **Stuck queue.** One alert when any job stays `pending` past 15 minutes with no worker heartbeat. Resolve when the queue drains. `/metrics` exposes `piherder_queue_oldest_pending_seconds` and `piherder_stuck_queue` |
| Should | **Read-only alerts.** `GET /api/v1/alerts` with the `read` scope, and MCP `list_alerts`, return open notifications. Automation does not have to guess open alerts from summary counts |

### MCP

| Tier | Lock |
|------|------|
| Should | **OAuth token names.** `mcp_oauth.py` adds a short suffix so two MCP agents that send the same client name do not look the same. The suffix is not the token value or the token prefix. This does not need an adapter tag by itself |

### Security

| Tier | Lock |
|------|------|
| Must | **One-time token banner off the query string.** After a token is created, do not put `token_secret` on the redirect URL. A new secret must not land in browser history or access logs |

### Internals

| Tier | Lock |
|------|------|
| Must | **Docs and release-sync sweep.** Fix `v1.12.1` version drift in `SECURITY.md`, `CONTRIBUTING.md`, `SPEC.md`, `docs/ADMIN.md`, `docs/ROADMAP_ECOSYSTEM.md`, `docs/API.md`, and `wiki/`. Fix broken relative links to `docs/archive/v0/`. Add public-repo hygiene rules in `AGENTS.md`. Extend `scripts/check_release_sync.py` so those version pins stay checked |
| Should | **Reconcile orphaned running jobs.** On worker startup or the fleet tick, fail or mark interrupted any `running` job whose Celery worker is no longer alive |
| Should | **`get_engine()`.** One accessor for every session in `app/`. Callers stop importing the module-level engine. A test fails if anything outside `app/database.py` imports that engine. No operator-visible change |

| Topic | Lock |
|-------|------|
| Version bump | Package stays **1.12.1** until freeze. About stays **1.12.1** |
| Demo | The public demo is not pointed at this branch |
| Plugin | The Home Assistant card stays the separate HACS repo. Putting it in this image is not a backlog row |
| Adapter | Bumps to **0.4.4** with build-cache prune |
| Issues | [#50](https://github.com/bjorngluck/piherder/issues/50) stays open until the release |

Not on this train: the push-denylist secret (the workflow is in), volume prune on the API, borg, kopia, rclone crypt, fine-grained roles, and putting the Home Assistant plugin in the image. Pushed to v1.14.0+: a pre-restore host rollback snapshot, backup manifest file-count and byte verification, a dry-run restore preview API, and a compose git checkout drift badge.

Nothing was voted **Out**.

---

## 2. Capture log

| Date | What changed |
|------|----------------|
| 2026-10-19 | **Build cache.** Noted from the v1.12 prune work. The page, the token API, and hosted MCP should clear the Docker build cache and the buildx cache. Not on the v1.12 train. Not built. |
| 2026-10-19 | **Restore ownership.** Noted from the v1.12 remote restore. Record the Unix owner, group, and mode at backup time and put them back on restore, especially for named volumes. A wrong owner can stop the container from starting. Not on the v1.12 train. Not built. |
| 2026-10-23 | **Train opened** from `main` after **v1.12.1** shipped. Tiers voted. Must: restore ownership, NIC change ([#50](https://github.com/bjorngluck/piherder/issues/50)). Should: backup stagger ceiling, build cache, prune on a schedule, prune dangling images after a successful update, stuck queue, OAuth token name suffix, `get_engine()`. Nothing was voted Discover, Later, or Out. Open issues reviewed: [#50](https://github.com/bjorngluck/piherder/issues/50) is the only open issue. piherder-ha and piherder-mcp have none. No open pull request. Package stays `1.12.1`. Nothing is built. |
| 2026-10-23 | **Team review.** Lock tightened the same day. Restore ownership records numeric `uid`, `gid`, and octal `mode` in a sidecar and strips `setuid` and `setgid`. NIC change adds a Discovery badge, a `lan_discovery` `info` notification, and one-click **Replace stale MAC** (ignore the stale row, copy display name, kind override, and notes, one audit row). Backup stagger uses all 1,440 minutes. Docker adds build-cache prune (adapter bumps to **0.4.4**), prune on a schedule in About and the missed-run watch, `docker image prune -f` after a successful update, a 30-minute reset for a stuck inventory refresh, and per-project `update_available` on the inventory API. Alerts add the stuck-queue gauges and read-only `GET /api/v1/alerts` plus MCP `list_alerts`. Must also covers the docs and release-sync sweep, and keeping `token_secret` off the redirect query string. Should also covers orphaned `running` jobs and a `get_engine()` guard. Pushed to v1.14.0+: pre-restore host rollback snapshot, backup manifest file-count and byte verification, dry-run restore preview API, compose git checkout drift badge. Not built. |

---

## 3. Next

| # | Step | Status |
|---|------|--------|
| 1 | Open **`v1.13.0-dev`** | **Done** 2026-10-23 |
| 2 | Must, by area | Not started. Restore ownership, the possible NIC change notice and Replace stale MAC, the docs and release-sync sweep, and the one-time token secret off the query string |
| 3 | Should, if it fits | Not started. Stagger ceiling, Docker prune follow-ups, stuck queue and read-only alerts, OAuth token suffix, orphaned running jobs, `get_engine()` |
| 4 | Freeze | Not set. Package stays **1.12.1** |
| 5 | Operator QA | Walks are empty |
| 6 | Tag and Hub | Not cut |

---

*Opened 2026-10-23. Not built.*
