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

- [ ] **Must. Restore ownership.** A backup records numeric `uid`, `gid`, and octal `mode` in a sidecar manifest. Restore writes that owner, group, and mode on the host and strips `setuid` and `setgid` (`mode & 0o1777`). A named volume that must belong to the app user does not come back as the rclone account
- [ ] **Should. Backup stagger ceiling.** Host backup minutes spread across all 1,440 minutes in a day before two hosts share a minute slot

## Discovery

- [ ] **Must. NIC change.** Issue [#50](https://github.com/bjorngluck/piherder/issues/50). A new MAC for a hostname that already has a non-ignored row at that IP, stale for longer than one scan interval, shows a "possible NIC change" notice (Discovery badge and a `lan_discovery` `info` notification). Device identity stays the MAC. Rows are not merged and are not auto-ignored. **Replace stale MAC** marks that stale row `ignored`, copies the custom display name, kind override, and notes onto the new MAC row, and writes one audit row

## Docker

- [ ] **Should. Build cache.** The host Docker page, the token API, and hosted MCP prune the Docker build cache and the buildx cache. The API and MCP need `edit`. One audit row. This is not `docker_prune`. No schedule. The adapter bumps to **0.4.4** for this prune
- [ ] **Should. Prune on a schedule.** A per-host saved schedule starts the same prune the API starts by hand. Volumes stay off. Build cache stays its own action. About **View schedule** lists the job, and the missed-schedule watch covers it
- [ ] **Should. Prune after an update.** A successful `container_patch` or redeploy ends with `docker image prune -f`. Dangling images only. Volumes, exited containers, and the build cache are left alone
- [ ] **Should. Expire stuck inventory refresh.** `docker_inventory_status` left at `refreshing` for 30 minutes resets, the same way a stuck `host_facts` refresh clears. A dropped background refresh does not stay stuck
- [ ] **Should. Per-project `update_available` in the inventory API.** The Docker inventory API names which compose projects and images have an update. Callers do not scrape `container_update_check` job logs for that

## Alerts

- [ ] **Should. Stuck queue.** One alert opens when any job stays `pending` past 15 minutes with no worker heartbeat. It resolves when the queue drains. `/metrics` exposes `piherder_queue_oldest_pending_seconds` and `piherder_stuck_queue`
- [ ] **Should. Read-only alerts.** `GET /api/v1/alerts` (`read` scope) and MCP `list_alerts` return open notifications. Automation does not guess open alerts from summary counts

## MCP

- [ ] **Should. OAuth token names.** `mcp_oauth.py` adds a short suffix so two MCP agents that send the same client name do not look the same. The suffix is not the token value or the token prefix

## Security

- [ ] **Must. One-time token banner off the query string.** After a token is created, the redirect URL does not carry `token_secret`. The new secret is not in browser history or access logs

## Internals

- [ ] **Must. Docs and release-sync sweep.** `SECURITY.md`, `CONTRIBUTING.md`, `SPEC.md`, `docs/ADMIN.md`, `docs/ROADMAP_ECOSYSTEM.md`, `docs/API.md`, and `wiki/` agree on `v1.12.1`. Relative links to `docs/archive/v0/` resolve. `AGENTS.md` states the public-repo hygiene rules. `scripts/check_release_sync.py` checks those version pins
- [ ] **Should. Reconcile orphaned running jobs.** On worker startup or the fleet tick, a `running` job whose Celery worker is gone is failed or marked interrupted
- [ ] **Should. `get_engine()`.** Sessions in `app/` use one engine accessor. A test fails if anything outside `app/database.py` imports the module-level engine. No operator-visible change
