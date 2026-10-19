# PiHerder v1.12.0 — operator QA / sign-off

**Branch:** `v1.12.0-dev` → `main` · tag **`v1.12.0`** (cut after merge, only when asked)  
**Code freeze:** not set  
**Package:** stays **`1.11.0`** until freeze. About / footer say **1.11.0**. Release image tags stay `1.11.0` / `1.11` / `latest`.  
**Operator QA:** not signed. Every box below is empty.  
**Screenshots:** none reserved.  
**Pull request:** not opened.

This file is **maintainer-only** (repo `docs/`). It is **not** published on the operator wiki.

Plan: [PLAN_v1.12.0.md](PLAN_v1.12.0.md). v1.11 sign-off stays [QA_v1.11.0.md](QA_v1.11.0.md). Do not re-open those boxes.

Plugin work is [bjorngluck/piherder-ha](https://github.com/bjorngluck/piherder-ha). It is not inside the PiHerder image. Putting it in the image is not a backlog row. Do not redeploy the public demo onto this branch.

Do not tick a box until that walk has been done. Do not tick a Discover row. Do not tick a Later row. Later is not a walk on this train.

---

## Jobs and audit (Must)

The read-audit flag is off unless the operator turns it on. Writes stay audited either way. Read rows stay off the default Audit list and use their own retention.

- [ ] With the flag off, a `read` call from an API token does not write an audit row
- [ ] With the flag on, that read writes one row. The row has the token id and name, and the client IP. It has no token value and no prefix
- [ ] Turning the flag on writes its own audit row. Turning it off writes one too
- [ ] With the flag on, a read through hosted `/mcp` writes the same kind of row
- [ ] With the flag off, a write from a token still writes an audit row
- [ ] Read rows stay off the default Audit list until **Show routine rows** is on
- [ ] Read rows older than their own retention are deleted. The general audit purge leaves them alone
- [ ] The default Jobs list leaves a successful 15-minute `host_facts` snapshot out
- [ ] The default Audit list leaves that routine snapshot out
- [ ] A failed `host_facts` snapshot stays on both default lists
- [ ] **Show routine host snapshots** on Jobs brings the successful snapshot back

## Alerts (Must)

- [ ] An expired or rejected Google Drive credential opens one critical alert. The alert says reconnect. Setting the backup category lower does not lower it
- [ ] The same alert, still critical, for OneDrive. It says reconnect
- [ ] The same alert, still critical, for a LAN share
- [ ] The alert and the log line may carry the provider error code. They never carry the token, the rclone config, or the raw response body
- [ ] Docs say to publish the OAuth app to production, because a Testing-mode app loses its token after 7 days. The Drive scope stays `https://www.googleapis.com/auth/drive`, because `drive.file` cannot see a folder the operator created
- [ ] A later successful copy resolves that alert
- [ ] A failed self-backup alert shows the last successful self-backup time, or "never"
- [ ] Creating a user, creating an API token, or changing a 2FA setting (force 2FA, template 2FA) opens an admin alert. The alert names the actor and the client IP

## Docker (Must)

Issue [#47](https://github.com/bjorngluck/piherder/issues/47).

- [ ] After a pull, while the container still runs the old image, the update flag stays set
- [ ] The flag clears once the running container is that image

## Hosts (Must)

Issue [#49](https://github.com/bjorngluck/piherder/issues/49).

- [ ] A detected release outside the small supported list shows a warning next to the OS line
- [ ] That warning does not start an upgrade

## Scheduler (Must)

Scheduled container checks stopped after 11 Oct. Apply cleared the per-host flag and left the cron. The scheduler then removed the job.

- [ ] The capture log names why those update checks stopped
- [ ] A test covers that cause

## Git (Must)

Repository refuses a folder or checkout the SSH user does not own, before git runs.

- [ ] Repository refuses a checkout the SSH user does not own
- [ ] That fix does not set `safe.directory=*`

## Backup (Should)

- [ ] The image carries rclone for amd64 and arm64, and a direct copy sends the one that matches the host
- [ ] The straight-out tick is greyed out when host facts name a CPU other than those two. An unknown architecture stays available
- [ ] A missed scheduled run covers host backups and container update checks. A warning opens when a run does not start within 15 minutes of its slot, and the next successful run resolves it
- [ ] `/metrics` exposes `piherder_schedule_missed` for that missed run
- [ ] A destination copy scheduled earlier the same day than a ticked host backup raises a warning and names those hosts. A direct host is not part of that check. The warning clears when the copy is no longer earlier

## MCP and the adapter (Should)

The adapter cut ships with the herder.

- [ ] `list_servers` and `get_server` return `effective_backup_schedule`, `next_backup_run`, and `backup_scheduled`, and the tool text names those fields
- [ ] The published piherder-mcp readme badges herder 1.11.0

The GitHub readme on adapter main already badges herder 1.11.0. PyPI 0.4.2 still freezes the 1.10.0 readme. This box stays open until the published package shows 1.11.0. That upload ships with the herder. The adapter stays 0.4.2 until then.

## Docker prune (Should)

Issue [#48](https://github.com/bjorngluck/piherder/issues/48). Volume prune is UI-only behind a confirm. The API and MCP need the `edit` scope.

- [ ] The host Docker page can start a prune job
- [ ] The token API can start that job, and it requires the `edit` scope
- [ ] Hosted `/mcp` can start that job, and it requires the `edit` scope
- [ ] Volumes stay in the UI until the operator confirms that part
- [ ] Every prune writes an audit row

## Scheduler (Should)

- [ ] About shows each scheduled job's last fire time and next fire time

## Hosts (Should)

- [ ] An OS patch job keeps apt's error output, including when sudo drops `DEBIAN_FRONTEND`

## Supply chain and history (Should)

Done when a grep of main and the wiki comes back clean, plus a list of published commits that still hold old environment details. No force-push. The push denylist lands with that sweep.

- [ ] A grep of main and the wiki comes back clean
- [ ] The list of published commits is ready for a yes or no. This walk does not force-push
- [ ] The capture log records the pass over pinned actions, the dependency lock, image digest pinning, Dependabot, and secret scanning
- [ ] The push denylist stays in a secret. Until it lands, the manual grep remains

## Discover

Not a walk until the row is promoted in [PLAN_v1.12.0.md](PLAN_v1.12.0.md).

- Remote restore from Drive, OneDrive, or a LAN share. Restore still reads `/backups`.
- A direct hop does not wait on a folder hop that is still showing running.
- NPM CRUD. Admin-only, audited, no MCP on this train. Move can still retarget a backend.
- A read-only admin API for users, tokens, and the audit log. Needs a design and a Security review before it is built.

## Later

Not a walk on this train. Issues stay open.

- NIC change, issue [#50](https://github.com/bjorngluck/piherder/issues/50). Device identity stays the MAC.
- Backup stagger past about 1,320 hosts.
- Stuck queue alert.
- OAuth token name suffix.
- `get_engine()` accessor.

## 1.11 regression

- [ ] A passkey still counts as 2FA when Force 2FA is on
- [ ] A host that copies straight out still sends files to the ticked destination, and restore of that host still reads `/backups`
- [ ] Jobs still label a direct hop **Direct copy** and a folder hop **Backup copy**
