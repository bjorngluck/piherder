# PiHerder v1.12.0 (operator QA / sign-off)

**Branch:** `v1.12.0-dev` → `main` · tag **`v1.12.0`** (cut after merge, only when asked)  
**Code freeze:** set 2026-10-20. No new product work on this branch.  
**Package:** stays **`1.11.0`** until the ship bump. About / footer say **1.11.0**. Release image tags stay `1.11.0` / `1.11` / `latest`.  
**Operator QA:** signed 2026-10-20. Screenshot boxes are ticked.  
**Release notes:** draft [RELEASE_v1.12.0.md](RELEASE_v1.12.0.md). Not a ship.  
**Screenshots:** captured 2026-10-22 and wired. Names, paths, and addresses are masked. [v1.12 pack](../wiki/assets/screenshots/README.md#v112--pack-status).  
**Pull request:** draft [#54](https://github.com/bjorngluck/piherder/pull/54). See [PR_v1.12.0.md](PR_v1.12.0.md).

This file is **maintainer-only** (repo `docs/`). It is **not** published on the operator wiki.

Plan: [PLAN_v1.12.0.md](PLAN_v1.12.0.md). v1.11 sign-off stays [QA_v1.11.0.md](QA_v1.11.0.md). Do not re-open those boxes.

Plugin work is [bjorngluck/piherder-ha](https://github.com/bjorngluck/piherder-ha). It is not inside the PiHerder image. Putting it in the image is not a backlog row. Do not redeploy the public demo onto this branch.

Walks below are signed 2026-10-20. Screenshot files were captured 2026-10-22. Later is not a walk on this train.

---

## Jobs and audit (Must)

The read-audit flag is off unless the operator turns it on. Writes stay audited either way. Read rows stay off the default Audit list and use their own retention.

- [x] With the flag off, a `read` call from an API token does not write an audit row
- [x] With the flag on, that read writes one row. The row has the token id and name, and the client IP. It has no token value and no prefix
- [x] Turning the flag on writes its own audit row. Turning it off writes one too
- [x] With the flag on, a read through hosted `/mcp` writes the same kind of row
- [x] With the flag off, a write from a token still writes an audit row
- [x] Read rows stay off the default Audit list until **Show routine rows** is on
- [x] Read rows older than their own retention are deleted. The general audit purge leaves them alone
- [x] The default Jobs list leaves a successful 15-minute `host_facts` snapshot out
- [x] The default Audit list leaves that routine snapshot out
- [x] A failed `host_facts` snapshot stays on both default lists
- [x] **Show routine host snapshots** on Jobs brings the successful snapshot back

## Alerts (Must)

- [x] An expired or rejected Google Drive credential opens one critical alert. The alert says reconnect. Setting the backup category lower does not lower it
- [x] The same alert, still critical, for OneDrive. It says reconnect
- [x] The same alert, still critical, for a LAN share
- [x] The alert and the log line may carry the provider error code. They never carry the token, the rclone config, or the raw response body
- [x] Docs say to publish the OAuth app to production, because a Testing-mode app loses its token after 7 days. The Drive scope stays `https://www.googleapis.com/auth/drive`, because `drive.file` cannot see a folder the operator created
- [x] A later successful copy resolves that alert
- [x] A failed self-backup alert shows the last successful self-backup time, or "never"
- [x] Creating a user, creating an API token, or changing a 2FA setting (force 2FA, template 2FA) opens an admin alert. The alert names the actor and the client IP

## Docker (Must)

Issue [#47](https://github.com/bjorngluck/piherder/issues/47).

- [x] After a pull, while the container still runs the old image, the update flag stays set
- [x] The flag clears once the running container is that image

## Hosts (Must)

Issue [#49](https://github.com/bjorngluck/piherder/issues/49).

- [x] A detected release outside the small supported list shows a warning next to the OS line
- [x] That warning does not start an upgrade

## Scheduler (Must)

Scheduled container checks stopped after 11 Oct. Apply cleared the per-host flag and left the cron. The scheduler then removed the job.

- [x] The capture log names why those update checks stopped
- [x] A test covers that cause

## Git (Must)

Repository refuses a folder or checkout the SSH user does not own, before git runs.

- [x] Repository refuses a checkout the SSH user does not own
- [x] That fix does not set `safe.directory=*`

## Backup (Should)

- [x] The image carries rclone for amd64 and arm64, and a direct copy sends the one that matches the host
- [x] The straight-out tick is greyed out when host facts name a CPU other than those two. An unknown architecture stays available
- [x] A missed scheduled run covers host backups and container update checks. A warning opens when a run does not start within 15 minutes of its slot, and the next successful run resolves it
- [x] `/metrics` exposes `piherder_schedule_missed` for that missed run
- [x] A destination copy scheduled earlier the same day than a ticked host backup raises a warning and names those hosts. A direct host is not part of that check. The warning clears when the copy is no longer earlier

## MCP and the adapter (Should)

The adapter cut ships with the herder.

- [x] `list_servers` and `get_server` return `effective_backup_schedule`, `next_backup_run`, and `backup_scheduled`, and the tool text names those fields
- [x] The GitHub piherder-mcp readme badges herder v1.12.0
- [x] Adapter **0.4.3** is tagged and on PyPI, including `prune_docker` and the backup schedule fields

The GitHub readme on adapter main badges herder v1.12.0. Adapter **[0.4.3](https://github.com/bjorngluck/piherder-mcp/releases/tag/v0.4.3)** is on PyPI and includes that readme. A cached **0.4.2** does not list `prune_docker`.

## Docker prune (Should)

Issue [#48](https://github.com/bjorngluck/piherder/issues/48). Volume prune is UI-only behind a confirm. The API and MCP need the `edit` scope.

- [x] The host Docker page can start a prune job
- [x] The token API can start that job, and it requires the `edit` scope
- [x] Hosted `/mcp` can start that job, and it requires the `edit` scope
- [x] Volumes stay in the UI until the operator confirms that part
- [x] Every prune writes an audit row

## Scheduler (Should)

- [x] About opens scheduled jobs by type and shows each host's status, last run, and next fire

The About card is a count. View schedule opens the modal. The type control sits above the list. Pick a type, then each host (or this instance) with its status. Last run is the latest matching job. A restart does not clear it. A missed slot is not a run. Next fire is the scheduler's next run. Times use the Settings timezone. Signed 2026-10-20.

## Hosts (Should)

- [x] An OS patch job keeps apt's error output, including when sudo drops `DEBIAN_FRONTEND`

The job log keeps an `E:` line, and a debconf or sudo line when that is what apt printed. The job summary names that line. A later progress rewrite does not replace it. Signed 2026-10-20.

## Supply chain and history (Should)

Published history was rewritten on 2026-10-19. The home path, the home subnet, and internal hostnames are gone from `main`, the release tags, and `gh-pages`. The public docs host, the public demo host, and the demo login stay. The map is [HISTORY_SWEEP_v1.12.md](HISTORY_SWEEP_v1.12.md). The denylist workflow is in. It checks only after the secret `PIHERDER_PUSH_DENYLIST` is set. That secret is not set. The manual grep remains. Signed 2026-10-20.

- [x] A grep of main and the wiki comes back clean
- [x] `main`, the release tags, and `gh-pages` no longer carry the home path, the home subnet, or an internal hostname
The capture log records the pass. `uv.lock` matches the exported locks at Mako 1.4.3 and multidict 6.9.1. Actions stay on major tags. The base image stays a tag. Open Dependabot alerts on main clear when this branch lands. This branch pins aiohttp 3.14.4, yarl 1.25.1, Mako 1.4.3, and multidict 6.9.1. Secret scanning has no open alerts. Signed 2026-10-20.

- [x] The capture log records the pass over pinned actions, the dependency lock, image digest pinning, Dependabot, and secret scanning
- [x] The denylist workflow is in. The secret is not set, so that check does not fail a push. The manual grep remains

## Discover

These rows are in. Signed 2026-10-20.

- [x] Restore can pull one selected folder from Google Drive, OneDrive, or a LAN share onto this PiHerder, then onto the host. Dry-run does not write the pull
- [x] A direct host only lists destinations it sends to

Those copies do not keep a Unix owner. A named volume can come back as the wrong account, and the container may then fail to start. This walk does not cover repairing ownership or mode. That is a v1.13 note.
- [x] A folder row left running does not hold the direct hop. A second folder copy still waits on that row
- [x] An admin can create, edit, and delete a proxy host. An operator cannot. There is no MCP tool. Move still only changes `forward_host`
- [x] A token with `admin` can GET users, tokens, and the audit log. A `read` token cannot. The response omits the password hash, the TOTP secret, and the token prefix. MCP sign-in cannot receive `admin`

## Later

Not a walk on this train. Issues stay open.

- NIC change, issue [#50](https://github.com/bjorngluck/piherder/issues/50). Device identity stays the MAC.
- Backup stagger past about 1,320 hosts.
- Stuck queue alert.
- OAuth token name suffix.
- `get_engine()` accessor.

## Screenshots

Captured 2026-10-22 and wired. Names, paths, and addresses are masked. Do not photograph the public demo. The file list is [v1.12 pack](../wiki/assets/screenshots/README.md#v112--pack-status).

- [x] `host-os-unsupported.png`
- [x] `docker-prune.png`
- [x] `about-scheduler.png`
- [x] `restore-from-copy.png`
- [x] `npm-proxy-host.png`
- [x] `settings-api-admin.png`
- [x] `alerts-backup-credential.png`

## 1.11 regression

- [x] A passkey still counts as 2FA when Force 2FA is on
- [x] A host that copies straight out still sends files to the ticked destination, and restore of that host still reads `/backups`
- [x] Jobs still label a direct hop **Direct copy** and a folder hop **Backup copy**
