# PiHerder v1.13.0 (operator QA / sign-off)

**Branch:** `v1.13.0-dev`  
**Code freeze:** set 2026-10-25. No new product work on this branch. Package bump, merge, tag, and Hub stay a separate ask.  
**Package:** stays **`1.12.1`**. About / footer stay **1.12.1**.  
**Operator QA:** Backups, Discovery, Docker, Alerts, MCP, Security, Internals, and Host tools signed 2026-10-25. The 1.12 regression walk is signed 2026-10-25. About Docker prune and Docker cleanup frames are wired 2026-10-26. Host bootstrap is wired. NIC change and the stuck-queue frames are wired.  
**Release notes:** draft [RELEASE_v1.13.0.md](RELEASE_v1.13.0.md). Not tagged.  
**Screenshots:** reserved in [v1.13 pack](../wiki/assets/screenshots/README.md#v113--pack-status).  
**Pull request:** draft [#60](https://github.com/bjorngluck/piherder/pull/60).

This file is **maintainer-only** (repo `docs/`). It is **not** published on the operator wiki.

Plan: [PLAN_v1.13.0.md](PLAN_v1.13.0.md). v1.12 sign-off stays [QA_v1.12.0.md](QA_v1.12.0.md). Do not re-open those boxes.

Plugin work is [bjorngluck/piherder-ha](https://github.com/bjorngluck/piherder-ha). It is not inside the PiHerder image. Putting it in the image is not a backlog row. Do not redeploy the public demo onto this branch.

Must and Should code and documentation are on `v1.13.0-dev`. Backups, Discovery, Docker, Alerts, MCP, Security, Internals, and Host tools are signed 2026-10-25. The 1.12 regression walk is signed 2026-10-25. About Docker prune and Docker cleanup frames are wired 2026-10-26. Host bootstrap is wired. NIC change and the stuck-queue frames are wired. Later is not a walk on this train. Do not photograph the public demo. Mask names, paths, addresses, and secrets.

---

## Backups (Must & Should)

A backup writes `.piherder-ownership.json` beside the tree. Restore applies numeric `uid`, `gid`, and octal `mode` on the host and strips `setuid` and `setgid` (`mode & 0o1777`). An archive with no sidecar still restores. On a daily base, the first 1,440 host ids each get a distinct minute of the day.

Signed 2026-10-25.

- [x] A successful backup of a tree writes `.piherder-ownership.json` with numeric `uid`, `gid`, and octal `mode` for the recorded paths
- [x] A failure to write that sidecar is a warning. The backup itself is kept
- [x] Restore of that archive applies the numeric owner, group, and mode on the host
- [x] A mode with `setuid` or `setgid` is stored, then applied with those bits cleared (`mode & 0o1777`)
- [x] A named volume that must belong to the app user does not come back as the rclone account
- [x] An archive from before this sidecar (no `.piherder-ownership.json`) restores the files and does not fail on ownership
- [x] The audit row for the restore is still `backup_restore` and does not include the destination sign-in
- [x] On a daily base such as `0 2 * * *`, host ids below 60 stay on that hour, and the first 1,440 ids use 1,440 distinct minutes
- [x] Host id 60 on an hour-23 daily base wraps to `0 0 * * *`
- [x] Past 1,440 hosts, two hosts can share a minute. A list, step, or weekday base only changes the minute
- [x] One host still does not run two backups at once

## Discovery (Must)

Issue [#50](https://github.com/bjorngluck/piherder/issues/50). Device identity stays the MAC. Rows are not merged and are not auto-ignored.

Signed 2026-10-25.

- [x] A new MAC for a hostname that already has a non-ignored row at that IP, stale for longer than one scan interval, shows a **possible NIC change** badge on Discovery
- [x] The same event opens one `lan_discovery` notification at `info`
- [x] A recent row, or a row at a different IP or hostname, does not show the badge and does not open that notification
- [x] `GET /api/v1/discovery/{id}/devices` (`read`) sets `nic_change` on the new row. The integrations list `GET /api/v1/discovery` does not
- [x] **Replace stale MAC** marks the old row `ignored` and copies a non-empty display name, kind override, and notes onto the new MAC row
- [x] That click writes one audit row, `nmap_replace_stale_mac`
- [x] `POST /api/v1/discovery/{id}/devices/{device_id}/replace-stale-mac` (`edit`) with body `stale_device_id` does the same step. The path id is the new MAC row
- [x] A body that is not that stale pair returns **400** and the text `That row is not a stale MAC for this device`
- [x] A `read` token cannot call that route
- [x] Hosted `/mcp` `replace_stale_mac` (`read` and `edit`) calls that route and writes the same audit row
- [x] The two rows stay separate. Nothing is merged, and the stale row is not ignored until the operator asks

## Docker (Should)

Build cache is its own action. The prune schedule form is inside the **Cleanup…** modal (`#cleanup-modal`). The saved prune schedule starts `docker_prune` with `both`. Volumes stay off that schedule.

Signed 2026-10-25.

- [x] The host Docker page can prune the Docker build cache and the buildx cache (`docker builder prune -f`, then `docker buildx prune -f`)
- [x] `POST /api/v1/servers/{id}/docker/build-cache` (`edit`) does that prune in the request. It is not a job and not scheduled
- [x] A `read` token cannot call that route
- [x] Hosted `/mcp` `prune_build_cache` (`edit`) does the same prune
- [x] Each of those three writes one audit row, `docker_build_cache_prune`, and does not write a `docker_prune` job
- [x] Saving a per-host prune cron in the **Cleanup…** modal (`#cleanup-modal`) stores `docker_prune_schedule`. Empty means off. The host needs container patch enabled. The Docker page itself does not show that form
- [x] That cron starts `docker_prune` with source `both` (dangling images and exited containers). Volumes are not included. Build cache is not included
- [x] Saving the cron writes audit action `server_docker_prune_schedule`
- [x] About **View schedule** lists that job under `docker_prune`
- [x] A slot that does not start within 15 minutes opens `docker_prune_missed`. The next successful prune of that host resolves it. Clearing the cron resolves it too
- [x] A successful container update or redeploy then runs `docker image prune -f`. Dangling images only
- [x] A failed update does not run that prune. A prune that fails does not fail the update job
- [x] Volumes, exited containers, and the build cache are left alone by that inline prune
- [x] `docker_inventory_status` left at `refreshing` for 30 minutes, measured from `docker_inventory_at`, resets to stale when a snapshot exists and to never when it does not
- [x] A refreshing host with no `docker_inventory_at` is not treated as expired
- [x] `GET /api/v1/inventory` and `GET /api/v1/servers/{id}/inventory` (`read`) set `update_available` on each container, each image, and each compose project
- [x] Those flags match the stored inventory. The call does not SSH and does not require a `container_update_check` job log

## Alerts (Should)

Signed 2026-10-25.

- [x] One `stuck_queue` warning opens when the oldest `pending` job is older than 15 minutes and no Celery worker answers
- [x] It resolves when that age drops to 15 minutes or less
- [x] It resolves when a worker answers again, even if old pending rows remain
- [x] A worker check that cannot tell leaves an existing warning as it is and does not open a new one
- [x] Mute **PiHerder stack** stops the warning
- [x] `/metrics` exposes `piherder_queue_oldest_pending_seconds` as that age, or 0 when nothing is pending
- [x] `piherder_stuck_queue` is 1 only when the age is over 15 minutes and no worker answered. An unknown heartbeat leaves the gauge at 0
- [x] The gauges do not probe Celery when the oldest pending job is 15 minutes old or newer
- [x] `GET /api/v1/alerts` (`read`) returns open notifications with `limit` and `offset`. Dismissed and resolved rows are omitted
- [x] The payload has `id`, `type`, `severity`, `title`, `body`, `server_id`, `link_url`, `status`, `created_at`, and `updated_at`. It has no token secret
- [x] A token without `read` cannot call it
- [x] Hosted `/mcp` `list_alerts` (`read`) returns the same open set
- [x] A `lan_discovery` info row for a possible NIC change is in that list while it is open

## MCP (Should)

Published adapter **0.4.3** does not list `list_alerts`, `prune_build_cache`, or `replace_stale_mac`. Hosted `/mcp` on this branch does. Do not expect `uvx` at 0.4.3 to show them.

Signed 2026-10-25.

- [x] Two browser sign-ins that send the same OAuth `client_name` get different token names
- [x] The extra part is 6 hex characters. It is not the access token and not the `ph_oa_` prefix
- [x] The stored name stays within 120 characters
- [x] Hosted `list_alerts` is listed for a token with `read` and is absent without it
- [x] Hosted `prune_build_cache` is listed for a token with `edit` and is absent without it
- [x] Hosted `replace_stale_mac` is listed only when the token has `read` and `edit`
- [x] `uvx piherder-mcp` at **0.4.3** still does not list those three tools
- [x] `prune_docker` is still a `docker_prune` job. It is not the build-cache action

## Security (Must)

Signed 2026-10-25.

- [x] After create, the redirect URL has no `token_secret` query parameter
- [x] After rotate, the redirect URL has no `token_secret` query parameter
- [x] The new secret is shown once from the HttpOnly cookie `api_token_once`
- [x] That cookie expires in 5 minutes and is not readable from page script
- [x] A later load of the settings page does not show the secret again
- [x] Browser history and a copied redirect link do not contain the secret
- [x] The admin alert for token create still names the actor and the client IP and still omits the secret and the token prefix

## Internals (Must & Should)

Signed 2026-10-25.

- [x] `SECURITY.md`, `CONTRIBUTING.md`, `SPEC.md`, `docs/ADMIN.md`, `docs/ROADMAP_ECOSYSTEM.md`, `docs/API.md`, and `wiki/` agree the package is `1.12.1`
- [x] About and the footer still say **1.12.1**
- [x] `python3 scripts/check_release_sync.py` passes. Published adapter stays **0.4.3**. Plugin stays **0.5.3**
- [x] `mkdocs build --strict` passes
- [x] A `running` job whose Celery worker is gone is failed with `Interrupted. The Celery worker is no longer alive.`
- [x] That check runs on worker startup and on the host-facts fleet tick. It does not run from web startup
- [x] When workers answer but the task id is missing, the job waits 60 seconds before it fails
- [x] A failed worker inspect leaves running jobs alone
- [x] When no worker answers, the job fails immediately
- [x] Sessions in `app/` take the engine from `get_engine()`. A test fails if anything outside `app/database.py` imports the module-level engine
- [x] No new operator control appears for that engine change

## Host tools (Must)

Ubuntu, Debian (including 64-bit Raspberry Pi OS), and Raspbian (32-bit). `run_host_deps_check` probes enabled features. `build_host_bootstrap_script` and `bootstrap_host_tools` in `app/services/ssh_onboarding.py` install only those missing tools. The route is `POST /servers/{id}/host-deps/bootstrap`. It queues exclusive Celery job `host_bootstrap` and does not run SSH inside the web request. It is not on `/api/v1` or hosted `/mcp`. A host with only Backups on is checked and bootstrapped for `rsync` only. Audit action `host_bootstrap`. Optional `bootstrap_username` is the SSH login when the fleet user is already restricted `piherder`. The SSH access window stays open and shows job progress and errors in the card.

Signed 2026-10-25.

- [x] With only Backups on, the host check probes rsync and does not probe Docker packages or offer Docker CE
- [x] With only Docker on, and the host is not HAOS, the check reports `docker-ce`, `docker compose version`, docker group membership, conflicting distro packages, and `docker ps -q`
- [x] `/etc/os-release` maps `ubuntu`, `debian`, and `raspbian` to `https://download.docker.com/linux/<id>`. Another OS is not offered bootstrap
- [x] The copied script installs `ca-certificates`, `curl`, and `gnupg` before `dists/<codename>/Release`. It then removes only conflicting packages that `dpkg-query` shows as installed (`apt-get remove`, not `purge`), checks fingerprint `9DC858229FC7DD38854AE2D88D81803C0EBFCD88` under an ephemeral `GNUPGHOME`, and does not call `get.docker.com`. sudo is `sudo -H`
- [x] A form field that asks for Docker CE is ignored when the Docker feature is off
- [x] Docker bootstrap stays refused until **confirm** is ticked. The confirm text says the docker group is root-equivalent, and names package replacement when conflicts or running containers were seen
- [x] The one-time sudo password is Fernet-encrypted in the broker only until a worker picks it up, acked on receipt, expires after 120 seconds, is never retried or redelivered, and is never logged. A worker crash marks the job failed and the operator reruns with a fresh password. If no worker takes the message, the pending job fails with `expired before a worker picked it up, rerun with a fresh password`. A second bootstrap while one already holds the host fails at once with `another job holds this host, rerun when it finishes`, and the password is not queued. It is not in the `host_bootstrap` audit row or `Job.details`
- [x] Run on host creates a `host_bootstrap` job visible in Job history. The SSH access window stays open. A failure is shown in the card and the button can be used again
- [x] On a host whose fleet user is already `piherder`, an admin bootstrap username (and its password) can log in, and the docker group change still targets `piherder`
- [x] When that admin has passwordless sudo, the password is not passed to `bash -s`
- [x] After `usermod -aG docker`, a new SSH session can run `docker info` and `docker compose version` without sudo
- [x] Demo mode refuses the route
- [x] Turning Backups or Docker on from the wizard or server edit refreshes the host check, and a failed SSH probe does not undo the save

## Later

Not a walk on this train. These stay on v1.14.0+.

- Pre-restore host rollback snapshot
- Backup manifest file-count and byte verification
- Dry-run restore preview API
- Compose git checkout drift badge

Volume prune on the API, borg, kopia, rclone crypt, fine-grained roles, and putting the Home Assistant plugin in the image are not on this train either.

## Screenshots

All five frames are wired. NIC change and the stuck-queue frames use example names. Names, paths, addresses, and secrets must be masked. Do not photograph the public demo. The file list is [v1.13 pack](../wiki/assets/screenshots/README.md#v113--pack-status).

- [x] `discovery-nic-change.png`
- [x] `docker-build-cache-prune.png`
- [x] `alerts-stuck-queue.png`
- [x] `about-scheduler-prune.png`
- [x] `host-bootstrap.png`

## 1.12 regression

Signed 2026-10-25.

- [x] Restore from Google Drive, OneDrive, or a LAN share still pulls the selected folder onto this PiHerder, then onto the host. Dry-run still does not write the pull
- [x] In this release, unused volumes needed a confirm on the host page. The token API and hosted `/mcp` did not prune volumes
- [x] About **View schedule** still groups scheduled jobs by type and shows each host's status, last run, and next fire
- [x] A token with `admin` can still GET users, tokens, and the audit log. A `read` token cannot. Responses still omit the password hash, the TOTP secret, and the token prefix
- [x] With **API and MCP read audit** off, a `read` call still writes no audit row. With it on, that call writes one `api_read` row and the row has no token value
