# PiHerder v1.13.0

**Draft on `v1.13.0-dev`.** Not tagged. Not released.

Must and Should code and docs are built. Maintainer walks in [QA_v1.13.0](QA_v1.13.0.md) are signed 2026-10-25. Code freeze is set 2026-10-25. About **View schedule** on Docker prune, and Docker **Cleanup…** with a saved prune schedule, are wired. The host bootstrap, NIC change, and stuck-queue frames are not in the pack. Package version on `main` and on `v1.13.0-dev` stays **1.12.1**. About and the footer stay **1.12.1**.

Baseline is [v1.12.1](RELEASE_v1.12.1.md) (2026-10-23) and [v1.12.0](RELEASE_v1.12.0.md) (2026-10-22). This draft is not an upgrade path. The published image stays `bjorngluck/piherder:1.12.1`.

Home Assistant plugin is **[0.5.3](https://github.com/bjorngluck/piherder-ha/releases/tag/v0.5.3)** (Home Assistant **2024.7** or newer). Published PyPI adapter is **[0.4.3](https://github.com/bjorngluck/piherder-mcp/releases/tag/v0.4.3)**. Hosted `/mcp` on this branch adds `list_alerts`, `prune_build_cache`, and `replace_stale_mac`. This release does not cut a new adapter tag.

Operator how-to stays on the wiki: [Add a server](https://piherder-docs.hacknow.info/day-to-day/add-server/) · [Backups](https://piherder-docs.hacknow.info/day-to-day/backups/) · [Discovery](https://piherder-docs.hacknow.info/integrations/lan-discovery/) · [Docker](https://piherder-docs.hacknow.info/docker/overview/) · [Jobs and audit](https://piherder-docs.hacknow.info/day-to-day/jobs-audit-notifications/) · [Alerts](https://piherder-docs.hacknow.info/operations/alerts-email-webhooks/) · [API tokens](https://piherder-docs.hacknow.info/operations/api-tokens/) · [Settings](https://piherder-docs.hacknow.info/operations/settings/). Technical record: [PLAN_v1.13.0](PLAN_v1.13.0.md). Maintainer QA: [QA_v1.13.0](QA_v1.13.0.md). Prior notes: [RELEASE_v1.12.1](RELEASE_v1.12.1.md) · [RELEASE_v1.12.0](RELEASE_v1.12.0.md).

---

## What's new

### Host tool bootstrap and feature preflight

The host check probes only features that are already on. Backups on checks `rsync`. Docker on, and the host is not HAOS, checks `docker`, `docker compose`, `docker` group membership, official `docker-ce`, and conflicting distro packages (`docker.io`, `docker-compose`, `docker-compose-v2`, `docker-doc`, `podman-docker`, `containerd`, `runc`). HAOS is not offered Docker bootstrap.

`POST /servers/{id}/host-deps/bootstrap` is on the SSH access card and the add-server wizard. An operator can copy the script or run it over SSH. It is refused in demo mode. It is not on `/api/v1` or hosted `/mcp`.

The script is for Ubuntu, Debian, and Raspbian when Docker has published that codename. It installs `rsync` only when `backup_enabled` is on. It installs official Docker CE (`docker-ce`, `docker-ce-cli`, `containerd.io`, `docker-buildx-plugin`, `docker-compose-plugin`) and adds the SSH user to the `docker` group only when `container_patch_enabled` is on and `confirm_docker_bootstrap` is ticked. A form field cannot turn Docker on for a backups-only host.

It installs `ca-certificates`, `curl`, and `gnupg` before it checks `https://download.docker.com/linux/<distro>/dists/<codename>/Release`. It verifies GPG fingerprint `9DC858229FC7DD38854AE2D88D81803C0EBFCD88` with `gpg --batch --show-keys` in an ephemeral `GNUPGHOME`. sudo is `sudo -H`, so that directory is not created in the SSH user's home. It removes only conflicting packages that `dpkg-query` reports as installed (`ii`), with `apt-get remove`, not purge. `/var/lib/docker` is left in place. It does not call `get.docker.com` and it does not widen sudoers.

After `usermod -aG docker`, a new SSH session checks `docker info` and `docker compose version` without sudo. The host check is saved again. The install is exclusive Celery job `host_bootstrap`, so it shows in Job history and on the server job list. The web request only queues it. An optional bootstrap SSH username logs in as an admin (`pi`, `ubuntu`, or a privileged identity) when the fleet user is already the restricted `piherder` account. Docker group membership is still applied to the fleet user. The one-time sudo password is Fernet-encrypted in the broker only until a worker picks it up, acked on receipt, expires after 120 seconds, is never retried or redelivered, and is never logged. A worker crash marks the job failed and the operator reruns with a fresh password. If no worker takes the message, the pending job fails with `expired before a worker picked it up, rerun with a fresh password`. A second bootstrap while one already holds the host fails at once with `another job holds this host, rerun when it finishes`, and the password is not queued. It is not written to the job row or the audit row. If that admin has passwordless sudo, the password is used for SSH only. The SSH access window stays open, polls the job, and shows the error in the card when the run fails.

### API token secret handoff and OAuth token suffixes

Creating or rotating an API token sets a 5-minute HttpOnly cookie, `api_token_once`. The cookie is a signed JWT with `sub` set to the user id and `ats` set. SameSite is Lax. Secure is set behind HTTPS. The redirect goes to the tokens tab and does not put the raw secret in the URL. The next settings page reads the cookie once, shows the banner, and clears the cookie.

OAuth dynamic token issuance names the token from the client name plus a 6-hex suffix (`secrets.token_hex(3)`), for example `mcp-oauth-claude-a1b2c3`. The suffix is not the access token and not the `ph_` prefix. The name is capped at 120 characters. Two clients with the same name stay distinct.

### Backup restore ownership sidecar

A backup writes `.piherder-ownership.json` beside the tree (mode `0600`). Entries are relative paths with numeric `uid`, `gid`, and mode. `setuid` and `setgid` are stripped (`mode & 0o1777`). Symlinks are not recorded. A copy of `/backups` carries that file. A straight-out copy uploads it into the remote folder after the sync, and the sync excludes that name so the next run does not delete it.

On a real restore, paths are checked and an ancestor symlink is not followed. `chown -h` sets the numeric owner without following a final symlink. Mode is applied only on a path that is not a symlink. The sidecar is excluded from the rsync onto the host, so it is not left in the restored tree. Dry-run does not apply it. An older archive with no sidecar still restores, and the result notes that the manifest was missing.

### LAN discovery NIC change and stale MAC replacement

Issue [#50](https://github.com/bjorngluck/piherder/issues/50). When a scan inserts a MAC, and another non-ignored row has the same hostname and the same IP and has been stale for longer than one scan interval, Discovery shows a possible NIC change (`nic_change`). One `lan_discovery` notification opens at `info`. Rows are not merged and are not ignored on their own. Device identity stays the MAC.

**Replace stale MAC** marks the old row `ignored` and copies a non-empty display name, kind override, and notes onto the new MAC row. It does not delete the new row and it does not rewrite the old MAC in place. The Discovery button, `POST /api/v1/discovery/{id}/devices/{device_id}/replace-stale-mac` (`edit`, body `stale_device_id`), and hosted `replace_stale_mac` do that step. A pair that is not that stale match returns 400, `That row is not a stale MAC for this device`. One audit row is written, `nmap_replace_stale_mac`, and the info alert is resolved. The issue stays open while this note is a draft.

### Docker image prune, scheduled prune, and build cache

After a successful container update or redeploy, PiHerder runs `docker image prune -f`. Dangling images only. A prune failure is a log line. It does not fail the job. Volumes, exited containers, and the build cache are left alone.

A per-host `docker_prune_schedule` (empty means off, and the host needs the Docker feature) starts `docker_prune` with `both` (dangling images and exited containers). Volumes stay off that schedule. About **View schedule** lists it. A missed slot opens `docker_prune_missed`.

Build cache is its own action: `docker builder prune -f`, then `docker buildx prune -f`. The Docker page, `POST /api/v1/servers/{id}/docker/build-cache` (`edit`), and hosted `prune_build_cache` (`edit`) run it in the request. One audit row, `docker_build_cache_prune`. It is not a `docker_prune` job and it is not scheduled. The UI prune routes return 400, `Enable the Docker feature on this server first`, when `container_patch_enabled` is off. Volume prune stays on the Docker page until the operator confirms it. The token API and hosted `/mcp` still cannot prune volumes.

`GET /api/v1/inventory` and `GET /api/v1/servers/{id}/inventory` set `update_available` on each container, each image, and each compose project. A Docker inventory left `refreshing` for 30 minutes resets to stale when a snapshot exists and to never when it does not. A missing timestamp is not treated as expired.

### Worker orphan recovery, stuck-queue alerts, and alerts API

A `running` job whose Celery worker is gone is failed with `Interrupted. The Celery worker is no longer alive.` The check runs on worker startup and on the host-facts fleet tick. It does not run from web startup. If workers answer but the task id is missing, the job waits 60 seconds. If the worker check itself fails, running jobs are left alone. If no worker answers, the job fails immediately.

One `stuck_queue` warning opens when the oldest `pending` job is older than 15 minutes and no worker answers. It resolves when that age drops to 15 minutes or less, or when a worker answers again. An unknown heartbeat leaves an open warning. `/metrics` exposes `piherder_queue_oldest_pending_seconds` and `piherder_stuck_queue` (1 only when the condition is proven).

`GET /api/v1/alerts` (`read`, `limit`, `offset`) and hosted `list_alerts` return open notifications. Dismissed and resolved rows are omitted. A read writes an `api_read` row when API and MCP read audit is on.

### Full-day backup stagger, `get_engine()`, and release-sync pins

On a daily backup base, the first 1,440 host ids each get a distinct minute of the day, from `00:00` through `23:59`. Past 1,440 hosts, two hosts can share a minute. One host still does not run two backups at once.

Sessions in `app/` take the database engine from `get_engine()`. `tests/test_get_engine.py` fails if anything outside `app/database.py` imports the module-level engine.

`scripts/check_release_sync.py` checks that `SECURITY.md`, `CONTRIBUTING.md`, `docs/ADMIN.md`, `docs/README.md`, and `wiki/index.md` contain the current herder version. `tests/test_release_sync_pins.py` covers that pin.

---

## Not in this release

Nothing was voted Out. These stay off this train:

- The push-denylist secret. The workflow is in. An empty secret does not pretend it checked.
- Volume prune on the API or hosted `/mcp`.
- borg, kopia, rclone crypt, fine-grained roles, and putting the Home Assistant plugin in the image.
- A pre-restore host rollback snapshot, backup manifest file-count and byte verification, a dry-run restore preview API, and a compose git checkout drift badge. Those are v1.14.0+.
- Another adapter tag. **[0.4.3](https://github.com/bjorngluck/piherder-mcp/releases/tag/v0.4.3)** stays the published adapter on this draft.

---

## Upgrade

Alembic **051** adds `docker_prune_schedule`. When this release is tagged, recreate **web** and **celery-worker**. `1.12.1` remains the published image until the tag. Do not point the public demo at this branch. Plugin **0.5.3** is the card. It needs Home Assistant **2024.7** or newer. The notes stay a draft until that tag.
