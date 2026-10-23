# PiHerder v1.13.0 (operator QA / sign-off)

**Branch:** `v1.13.0-dev`  
**Code freeze:** not set.  
**Package:** stays **`1.12.1`**. About / footer stay **1.12.1**.  
**Operator QA:** walks below are empty.  
**Release notes:** not drafted.  
**Screenshots:** not reserved.  
**Pull request:** not opened.

This file is **maintainer-only** (repo `docs/`). It is **not** published on the operator wiki.

Plan: [PLAN_v1.13.0.md](PLAN_v1.13.0.md). v1.12 sign-off stays [QA_v1.12.0.md](QA_v1.12.0.md). Do not re-open those boxes.

Plugin work is [bjorngluck/piherder-ha](https://github.com/bjorngluck/piherder-ha). It is not inside the PiHerder image. Putting it in the image is not a backlog row. Do not redeploy the public demo onto this branch.

One walk per voted row. Nothing is built. Later is not a walk on this train.

---

## Backups

- [ ] **Must. Restore ownership.** A restore puts the recorded Unix owner, group, and mode back on the host. A named volume that must belong to the app user does not come back as the rclone account
- [ ] **Should. Backup stagger ceiling.** Past about 1,320 hosts on the default 02:00 base, the stagger does not reuse a minute

## Discovery

- [ ] **Must. NIC change.** Issue [#50](https://github.com/bjorngluck/piherder/issues/50). A new MAC for a hostname that already has a non-ignored stale row at that IP, stale for longer than one scan interval, shows a "possible NIC change" notice. The old row stays. The rows are not merged. Device identity stays the MAC

## Docker

- [ ] **Should. Build cache.** The host Docker page, the token API, and hosted MCP prune the Docker build cache and the buildx cache. The API and MCP need `edit`. One audit row. This is not `docker_prune`. No schedule
- [ ] **Should. Prune on a schedule.** A host can save a schedule that starts the same prune the API starts by hand. Volumes stay off that schedule. Build cache stays its own action
- [ ] **Should. Prune after an update.** After a successful image update, dangling images are pruned. Volumes, exited containers, and the build cache are left alone

## Alerts

- [ ] **Should. Stuck queue.** One alert opens when any job stays pending past 15 minutes with no worker heartbeat. It resolves when the queue drains

## MCP

- [ ] **Should. OAuth token names.** Two MCP agents that send the same client name do not look the same. The suffix is not the token value or the token prefix

## Internals

- [ ] **Should. `get_engine()`.** Sessions use one engine accessor. No operator-visible change
