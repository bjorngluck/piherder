# PiHerder v1.13.0: ownership on restore, and a hint when a MAC changes

**Status:** **Built** on `v1.13.0-dev` 2026-10-23. Must and Should code and documentation are on the branch. Operator QA walks and screenshot reservations are ready in [QA_v1.13.0.md](QA_v1.13.0.md) and [v1.13 pack](../wiki/assets/screenshots/README.md#v113--pack-status). Walks are not signed. Freeze is not set. No tag.  
**Date opened:** 2026-10-23  
**Git branch:** `v1.13.0-dev`  
**Package / image version:** package stays **`1.12.1`**. About / footer stay **1.12.1**. This train does not bump the package.  
**Theme:** ownership on restore, and a hint when a MAC changes  
**Baseline:** `v1.12.1` (tagged 2026-10-23)  
**Mode:** **Must → Should.** Tiers were voted 2026-10-23. Nothing was voted Discover, Later, or Out.  
**QA:** [QA_v1.13.0.md](QA_v1.13.0.md) (operator walks and screenshot reservations. **Not** the operator wiki. Walks are not signed.)  
**Related:** [PLAN_v1.12.0.md](PLAN_v1.12.0.md) · [RELEASE_v1.12.1.md](RELEASE_v1.12.1.md) · [RELEASE_v1.12.0.md](RELEASE_v1.12.0.md) · wiki [Backups](../wiki/day-to-day/backups.md) · wiki [LAN Discovery](../wiki/integrations/lan-discovery.md)

> Opened 2026-10-23 from `main` after **v1.12.1** shipped. Package stays `1.12.1`. The public demo is not pointed at this branch. Plugin **0.5.2** stays the card. Adapter **[0.4.3](https://github.com/bjorngluck/piherder-mcp/releases/tag/v0.4.3)** stays the published adapter. Hosted `/mcp` on this branch lists `list_alerts`, `prune_build_cache`, and `replace_stale_mac` ahead of that adapter. Open issues were reviewed the same day. PiHerder has one open issue, [#50](https://github.com/bjorngluck/piherder/issues/50). piherder-ha and piherder-mcp have none. PiHerder has no open pull request.

---

## 0. Intent

v1.12.1 is a certifi patch on v1.12.0. v1.12.0 shipped remote restore and Docker prune. This train puts the Unix owner back on a restore, names a possible NIC change, and can replace the stale MAC in one audited step. A new token secret stays off the redirect query string. The same vote adds Docker prune follow-ups, a stuck queue, a read-only alerts read, and a docs sweep. The areas below are the vote. Must blocks the tag. Should is built on this train, and the tag can ship if that walk slips.

---

## 1. Decision lock

### Backups

| Tier | Lock |
|------|------|
| Must | **Restore ownership.** Record numeric `uid`, `gid`, and octal `mode` in `.piherder-ownership.json` when a tree is backed up. After the pull, restore that numeric owner, group, and mode on the host. Strip `setuid` and `setgid` (`mode & 0o1777`). An archive with no sidecar still restores. Drive, OneDrive, and a LAN share do not keep a Unix owner. The pull lands as the account that runs rclone until this step runs |
| Should | **Backup stagger ceiling.** Spread host backup minutes across all 1,440 minutes in a day before two hosts share a minute slot |

### Discovery

| Tier | Lock |
|------|------|
| Must | **NIC change.** Issue [#50](https://github.com/bjorngluck/piherder/issues/50). When a scan inserts a MAC identity, and another non-ignored row has the same hostname and the same IP and has been stale for longer than one scan interval, show a "possible NIC change" notice. The notice is a Discovery badge and a `lan_discovery` notification at `info`. `GET /api/v1/discovery/{id}/devices` sets `nic_change`. Device identity stays the MAC. Rows are not merged and are not auto-ignored. **Replace stale MAC** is the page button, `POST /api/v1/discovery/{id}/devices/{device_id}/replace-stale-mac` (`edit`, body `stale_device_id`), and hosted `replace_stale_mac`. It marks that stale row `ignored`, copies a non-empty display name, kind override, and notes, and writes audit action `nmap_replace_stale_mac`. The issue stays open until the release |

### Docker

| Tier | Lock |
|------|------|
| Should | **Build cache.** The host Docker page (`POST /servers/{id}/docker/prune-build-cache`), `POST /api/v1/servers/{id}/docker/build-cache` (`edit`), and hosted `prune_build_cache` (`edit`) run `docker builder prune -f` then `docker buildx prune -f`. One audit row, action `docker_build_cache_prune`. It is not part of `docker_prune`. No schedule on this action. The published adapter stays **0.4.3** |
| Should | **Prune on a schedule.** Per-host `docker_prune_schedule` (empty means off, and the host needs container patch) starts `docker_prune` with `both` (dangling images and exited containers). Volumes stay off. Build cache stays its own action. Scheduler id `docker_prune_{id}`. About **View schedule** lists it. A missed slot opens `docker_prune_missed` |
| Should | **Prune after an update.** At the end of a successful `container_patch` or redeploy, run `docker image prune -f` inline. Dangling images only. Not volumes, not exited containers, and not the build cache. A prune failure does not fail the job |
| Should | **Expire stuck inventory refresh.** If `docker_inventory_status` stays `refreshing` for 30 minutes from `docker_inventory_at`, reset it to stale when a snapshot exists and to never when it does not. A missing timestamp is not expired |
| Should | **Per-project `update_available` in the inventory API.** `GET /api/v1/inventory` and `GET /api/v1/servers/{id}/inventory` set `update_available` on each container, image, and compose project. Callers do not scrape `container_update_check` job logs for that |

### Alerts

| Tier | Lock |
|------|------|
| Should | **Stuck queue.** One `stuck_queue` warning when any job stays `pending` past 15 minutes and no worker answers. Resolve when that age drops to 15 minutes or less, or when a worker answers again. An unknown heartbeat leaves an open warning. `/metrics` exposes `piherder_queue_oldest_pending_seconds` and `piherder_stuck_queue` (1 only when the condition is proven) |
| Should | **Read-only alerts.** `GET /api/v1/alerts` (`read`, `limit`, `offset`) and hosted `list_alerts` return open notifications. Dismissed and resolved rows are omitted |

### MCP

| Tier | Lock |
|------|------|
| Should | **OAuth token names.** `mcp_oauth.py` `_token_name` appends `secrets.token_hex(3)` (6 hex characters) to the slugged `client_name`. The suffix is not the access token and not the `ph_oa_` prefix. The name is capped at 120 characters. This does not need an adapter tag |

### Security

| Tier | Lock |
|------|------|
| Must | **One-time token banner off the query string.** After create or rotate, the redirect URL does not carry `token_secret`. The secret is a one-shot HttpOnly cookie `api_token_once` (5 minutes, JWT claim `ats`) |

### Hosts

| Tier | Lock |
|------|------|
| Must | **Host tool bootstrap and feature preflight.** `run_host_deps_check` reads `/etc/os-release` and `dpkg --print-architecture` and maps `ubuntu`, `debian`, and `raspbian` to the official Docker apt repository. rsync is probed only when backups are on. Docker CE, Compose, the docker group, conflicting distro packages, and running containers are probed only when the Docker feature is on and the host is not HAOS. `POST /servers/{id}/ssh/bootstrap-tools` (operator, refused in demo) installs only those missing tools. The script checks `dists/<codename>/Release` before apt changes, uses `apt-get remove` (not purge), verifies Docker GPG fingerprint `9DC858229FC7DD38854AE2D88D81803C0EBFCD88`, and does not use `get.docker.com` or widen sudoers. Docker bootstrap needs `confirm_docker_bootstrap`. A one-time sudo password stays in that request. After `usermod -aG docker`, a fresh SSH session checks `docker info` and `docker compose version`. One `host_bootstrap` audit row. Not on `/api/v1` or `/mcp` |

### Internals

| Tier | Lock |
|------|------|
| Must | **Docs and release-sync sweep.** Fix `v1.12.1` version drift in `SECURITY.md`, `CONTRIBUTING.md`, `SPEC.md`, `docs/ADMIN.md`, `docs/ROADMAP_ECOSYSTEM.md`, `docs/API.md`, and `wiki/`. Fix broken relative links to `docs/archive/v0/`. Add public-repo hygiene rules in `AGENTS.md`. Extend `scripts/check_release_sync.py` so those version pins stay checked |
| Should | **Reconcile orphaned running jobs.** On worker startup or the host-facts fleet tick, fail a `running` job whose Celery worker is gone. The message is `Interrupted. The Celery worker is no longer alive.` Workers that answer with a missing task id wait 60 seconds. A failed inspect leaves jobs alone. No worker fails the job immediately. Web startup does not run this check |
| Should | **`get_engine()`.** One accessor for every session in `app/`. Callers stop importing the module-level engine. A test fails if anything outside `app/database.py` imports that engine. No operator-visible change |

| Topic | Lock |
|-------|------|
| Version bump | Package stays **1.12.1** until freeze. About stays **1.12.1** |
| Demo | The public demo is not pointed at this branch |
| Plugin | The Home Assistant card stays the separate HACS repo. Putting it in this image is not a backlog row |
| Adapter | Published adapter stays **0.4.3**. Hosted `/mcp` adds `list_alerts`, `prune_build_cache`, and `replace_stale_mac`. Build-cache prune is an action, not a new job type |
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
| 2026-10-23 | **Should built** on `v1.13.0-dev`. `get_engine()`, OAuth token suffix, backup minutes across all 1,440 slots, orphaned running jobs, a 30 minute inventory refresh reset, the stuck-queue alert and gauges, `GET /api/v1/alerts` and hosted `list_alerts`, inventory `update_available`, `docker image prune -f` after a successful container update or redeploy, a per-host `docker_prune` schedule (volumes off) in About and the missed-run watch, and build-cache prune on the Docker page, `POST /api/v1/servers/{id}/docker/build-cache`, and hosted `prune_build_cache`. Package stays `1.12.1`. Published adapter stays **0.4.3** so release sync matches `origin/main`. Hosted MCP adds `list_alerts` and `prune_build_cache` ahead of that adapter. |
| 2026-10-23 | **Docs.** Operator walks are written in [QA_v1.13.0.md](QA_v1.13.0.md) and are not signed. Screenshot names are reserved in the [v1.13 pack](../wiki/assets/screenshots/README.md#v113--pack-status). Wiki and [API.md](API.md) name `.piherder-ownership.json`, cookie `api_token_once`, `nic_change`, `POST /api/v1/discovery/{id}/devices/{device_id}/replace-stale-mac`, `docker_prune_schedule`, `POST /api/v1/servers/{id}/docker/build-cache`, inventory `update_available`, hosted `list_alerts`, `prune_build_cache`, and `replace_stale_mac`, the OAuth `client_name` suffix, and the Prometheus gauges including `piherder_queue_oldest_pending_seconds` and `piherder_stuck_queue`. Package stays `1.12.1`. Published adapter stays **0.4.3**. |
| 2026-10-23 | **Host tool bootstrap.** Must. Feature-gated preflight for Ubuntu, Debian, and Raspbian. UI route `POST /servers/{id}/ssh/bootstrap-tools` only. Fresh SSH check after `usermod`. Audit action `host_bootstrap`. Package stays `1.12.1`. |

---

## 3. Next

| # | Step | Status |
|---|------|--------|
| 1 | Open **`v1.13.0-dev`** | **Done** 2026-10-23 |
| 2 | Must, by area | **Done** on `v1.13.0-dev`, including documentation. Restore ownership, the possible NIC change notice and Replace stale MAC, the docs and release-sync sweep, the one-time token secret off the query string, and host tool bootstrap |
| 3 | Should, if it fits | **Done** on `v1.13.0-dev`, including documentation. Stagger ceiling, Docker prune follow-ups, stuck queue and read-only alerts, OAuth token suffix, orphaned running jobs, `get_engine()` |
| 4 | Freeze | Not set. Package stays **1.12.1** |
| 5 | Operator QA | Walks are written in [QA_v1.13.0.md](QA_v1.13.0.md). Screenshot names are reserved. Walks are not signed |
| 6 | Tag and Hub | Not cut |

---

*Opened 2026-10-23. Must and Should code and documentation are on `v1.13.0-dev`. Operator walks are written and not signed. Package stays 1.12.1. Not tagged.*
