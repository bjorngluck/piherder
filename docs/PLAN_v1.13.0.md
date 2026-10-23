# PiHerder v1.13.0: ownership on restore, and a hint when a MAC changes

**Status:** **Opened** 2026-10-23. Not built. No tag.  
**Date opened:** 2026-10-23  
**Git branch:** `v1.13.0-dev`  
**Package / image version:** package stays **`1.12.1`**. About / footer stay **1.12.1**. This train does not bump the package.  
**Theme:** ownership on restore, and a hint when a MAC changes  
**Baseline:** `v1.12.1` (tagged 2026-10-23)  
**Mode:** **Must → Should.** Tiers were voted 2026-10-23. Nothing was voted Discover, Later, or Out.  
**QA:** [QA_v1.13.0.md](QA_v1.13.0.md) (maintainer stub. **Not** the operator wiki.)  
**Related:** [PLAN_v1.12.0.md](PLAN_v1.12.0.md) · [RELEASE_v1.12.1.md](RELEASE_v1.12.1.md) · [RELEASE_v1.12.0.md](RELEASE_v1.12.0.md) · wiki [Backups](../wiki/day-to-day/backups.md) · wiki [LAN Discovery](../wiki/integrations/lan-discovery.md)

> Opened 2026-10-23 from `main` after **v1.12.1** shipped. Package stays `1.12.1`. The public demo is not pointed at this branch. Plugin **0.5.2** stays the card. Adapter **[0.4.3](https://github.com/bjorngluck/piherder-mcp/releases/tag/v0.4.3)** stays the published adapter. Open issues were reviewed the same day. PiHerder has one open issue, [#50](https://github.com/bjorngluck/piherder/issues/50). piherder-ha and piherder-mcp have none. PiHerder has no open pull request.

---

## 0. Intent

v1.12.1 is a certifi patch on v1.12.0. v1.12.0 shipped remote restore and Docker prune. This train puts the Unix owner back on a restore, and it names a possible NIC change. The areas below are the vote. Must blocks the tag. Should is built on this train, and the tag can ship if that walk slips.

---

## 1. Decision lock

### Backups

| Tier | Lock |
|------|------|
| Must | **Restore ownership.** Record the Unix owner, group, and mode when a tree is backed up, and put them back on the host during restore. Drive, OneDrive, and a LAN share do not keep a Unix owner. The pull lands as the account that runs rclone, and the reverse rsync writes that owner onto the host. A named volume that must belong to the app user can come back as the wrong account. The container may then fail to start |
| Should | **Backup stagger ceiling.** Past about 1,320 hosts on the default 02:00 base, the stagger must not reuse a minute |

### Discovery

| Tier | Lock |
|------|------|
| Must | **NIC change.** Issue [#50](https://github.com/bjorngluck/piherder/issues/50). When a scan inserts a MAC identity, and another non-ignored row has the same hostname and the same IP and has been stale for longer than one scan interval, show a "possible NIC change" notice. Device identity stays the MAC. This train does not mark the old row ignored and does not merge the rows. The issue stays open until the release |

### Docker

| Tier | Lock |
|------|------|
| Should | **Build cache.** The host Docker page, the token API, and hosted MCP can prune the Docker build cache and the buildx cache. `edit` scope on the API and MCP, one audit row per run. It is not part of `docker_prune`. No schedule on this action |
| Should | **Prune on a schedule.** A host can save a schedule that starts the same prune the API starts by hand. Volumes stay off that schedule. Build cache stays its own action |
| Should | **Prune after an update.** After a successful image update, prune dangling images only. Not volumes, not exited containers, and not the build cache |

### Alerts

| Tier | Lock |
|------|------|
| Should | **Stuck queue.** One alert when any job stays pending past 15 minutes with no worker heartbeat. Resolve when the queue drains |

### MCP

| Tier | Lock |
|------|------|
| Should | **OAuth token names.** A short suffix so two MCP agents that send the same client name do not look the same. The suffix is not the token value or the token prefix. This does not need an adapter tag by itself |

### Internals

| Tier | Lock |
|------|------|
| Should | **`get_engine()`.** One accessor for every session. Callers stop importing the module-level engine. No operator-visible change |

| Topic | Lock |
|-------|------|
| Version bump | Package stays **1.12.1** until freeze. About stays **1.12.1** |
| Demo | The public demo is not pointed at this branch |
| Plugin | The Home Assistant card stays the separate HACS repo. Putting it in this image is not a backlog row |
| Adapter | Stays **0.4.3** |
| Issues | [#50](https://github.com/bjorngluck/piherder/issues/50) stays open until the release |

Not on this train: the push-denylist secret (the workflow is in), volume prune on the API, borg, kopia, rclone crypt, fine-grained roles, and putting the Home Assistant plugin in the image.

Nothing was voted **Out**.

---

## 2. Capture log

| Date | What changed |
|------|----------------|
| 2026-10-19 | **Build cache.** Noted from the v1.12 prune work. The page, the token API, and hosted MCP should clear the Docker build cache and the buildx cache. Not on the v1.12 train. Not built. |
| 2026-10-19 | **Restore ownership.** Noted from the v1.12 remote restore. Record the Unix owner, group, and mode at backup time and put them back on restore, especially for named volumes. A wrong owner can stop the container from starting. Not on the v1.12 train. Not built. |
| 2026-10-23 | **Train opened** from `main` after **v1.12.1** shipped. Tiers voted. Must: restore ownership, NIC change ([#50](https://github.com/bjorngluck/piherder/issues/50)). Should: backup stagger ceiling, build cache, prune on a schedule, prune dangling images after a successful update, stuck queue, OAuth token name suffix, `get_engine()`. Nothing was voted Discover, Later, or Out. Open issues reviewed: [#50](https://github.com/bjorngluck/piherder/issues/50) is the only open issue. piherder-ha and piherder-mcp have none. No open pull request. Package stays `1.12.1`. Nothing is built. |

---

## 3. Next

| # | Step | Status |
|---|------|--------|
| 1 | Open **`v1.13.0-dev`** | **Done** 2026-10-23 |
| 2 | Must, by area | Not started. Restore ownership, and the possible NIC change notice |
| 3 | Should, if it fits | Not started |
| 4 | Freeze | Not set. Package stays **1.12.1** |
| 5 | Operator QA | Walks are empty |
| 6 | Tag and Hub | Not cut |

---

*Opened 2026-10-23. Not built.*
