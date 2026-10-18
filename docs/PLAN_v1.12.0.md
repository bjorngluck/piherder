# PiHerder v1.12.0: quieter lists, then honest backup and image signals

**Status:** **Open** 2026-10-18. Nothing on this train is built yet.  
**Date opened:** 2026-10-18  
**Git branch:** `v1.12.0-dev` → `main` · tag `v1.12.0` after merge, only when asked  
**Package / image version:** package stays **`1.11.0`** until freeze. About / footer say **1.11.0**. Release image tags stay `1.11.0` / `1.11` / `latest`. Pin `1.10.0` / `1.10` stays the previous image.  
**Theme:** quieter Jobs and Audit lists, then backup and image signals that stay honest  
**Baseline:** `v1.11.0` (tagged 2026-10-18; Hub digest `sha256:5b1a0ec8cfa5e2da26a486c5bc0f6d400ec86a8285e6eb9c2bbe64cfd30671b7`)  
**Mode:** **Must → Should → Discover.** Tiers were voted 2026-10-18. Operator walks are empty.  
**QA:** [QA_v1.12.0.md](QA_v1.12.0.md) (maintainer stub. **Not** the operator wiki.)  
**Related:** [PLAN_v1.11.0.md](PLAN_v1.11.0.md) · [RELEASE_v1.11.0.md](RELEASE_v1.11.0.md) · wiki [Jobs, audit, notifications](../wiki/day-to-day/jobs-audit-notifications.md) · wiki [Backups](../wiki/day-to-day/backups.md) · wiki [API tokens](../wiki/operations/api-tokens.md)

> Opened 2026-10-18 from `main` after **v1.11.0** shipped. Package stays `1.11.0`. The public demo is not pointed at this branch. Plugin **0.5.0** and adapter **0.4.2** stay. An adapter cut on this train ships with the herder, not ahead of it.

---

## 0. Intent

v1.11.0 shipped a passkey that counts as 2FA, a host that can copy straight to Drive, OneDrive, or a LAN share, and the Jobs and Audit rows that come with that work. This train makes the default lists quieter, and it keeps backup and image signals honest. The areas below are the vote. Must blocks the tag. Should is built on this train, and the tag can ship if that walk slips. Discover is capacity. Later stays a note.

---

## 1. Decision lock

### Jobs and audit

| Tier | Lock |
|------|------|
| Must | **Audit API and MCP reads.** An optional flag. Off unless the operator turns it on. When it is on, a `read` call from an API token or from hosted `/mcp` writes an audit row. The row stores the token id and name only, never the token value or prefix. Turning the flag on or off writes its own audit row. Read rows stay out of the default Audit list and have their own retention. Writes stay audited either way |
| Must | **Host facts clutter.** The 15-minute `host_facts` snapshot writes a Jobs row and an Audit row for every host. The default Jobs list and the default Audit list leave that routine snapshot out. A failed snapshot stays visible |
| Discover | **Read-only admin API.** A read-only admin API for users, tokens, and the audit log. Needs a design and a Security review before it is built |

### Backup

| Tier | Lock |
|------|------|
| Should | **Direct copy on a different CPU.** Grey out the straight-out tick when host facts say the host architecture is not the herder's. v1.11 already names the mismatch and keeps `/backups` for that host |
| Should | **Missed scheduled run.** Covers host backups and container update checks. Open a warning when a run does not start within N minutes of its slot. Resolve it on the next successful run. Add a Prometheus metric |
| Should | **Copy before the backups.** Warn when a destination copy is scheduled before the host backups it ships. A copy that runs before the backups is the case to catch |
| Discover | **Remote restore.** Restore from Drive, OneDrive, or a LAN share. Restore stays a reverse rsync from `/backups` until this is promoted |
| Discover | **Direct hop vs a stuck folder hop.** A folder hop left showing running holds that tick's direct hop until the next run. The direct hop should not wait on that row |
| Later | **Backup stagger ceiling.** The stagger reuses a minute past about 1,320 hosts on the default 02:00 base. That limit is documented |

### Alerts

| Tier | Lock |
|------|------|
| Must | **Backup credential failure.** Google Drive, OneDrive, and a LAN share (SMB or NAS) each open one critical alert when the saved credential expires or the destination rejects it. For Drive and OneDrive the alert says reconnect, not a generic failure. The alert and the log line may carry the provider error code (for example `invalid_grant`) and never the token, the rclone config, or the raw response body. Docs say to publish the OAuth app to production, because a Testing-mode app loses its token after 7 days, and to keep the Drive scope at `drive.file`. The alert stays critical even when the backup category is set lower. A later successful copy resolves it. v1.11 opens one alert, and that severity follows the backup category |
| Must | **Self-backup failed alert.** Include the last successful self-backup time, or "never". Not built |
| Must | **Admin alert.** An admin alert when a user or API token is created, or a 2FA setting changes (force 2FA, template 2FA). The alert names the actor and the client IP |
| Later | **Stuck queue.** One alert when any job stays pending past N minutes (default 15) with no worker heartbeat. Resolve when the queue drains |

### MCP and the adapter

| Tier | Lock |
|------|------|
| Should | **Backup time on MCP.** Hosted MCP and the adapter do not show the effective backup time. Expose `effective_backup_schedule`, `next_backup_run`, and `backup_scheduled` on the next adapter cut. The adapter ships with the herder |
| Should | **Adapter readme badge.** The piherder-mcp PyPI readme still badges an older herder. Point that badge at herder 1.11.0 on the same adapter cut |
| Later | **OAuth token names.** MCP OAuth tokens take their name from the client name, so two agents that send the same name look the same. A short suffix stays a note |

The read-audit flag under Jobs and audit covers hosted `/mcp` as well as API tokens. It is not a second MCP row.

### Docker

| Tier | Lock |
|------|------|
| Must | **Image update flag.** Issue [#47](https://github.com/bjorngluck/piherder/issues/47). The image check clears the update flag after a pull while the container still runs the old image. The flag stays until the running container is that image |
| Should | **Docker prune.** Issue [#48](https://github.com/bjorngluck/piherder/issues/48). A prune job from the host Docker page, the token API, and MCP. The API and MCP need the `edit` scope. Volume prune is UI-only behind a confirm. Every prune writes an audit row |

### Scheduler

| Tier | Lock |
|------|------|
| Must | **Update-check gap.** Still live. No scheduled container check has run since about 14 Oct. Update checks stopped for about four days. Find the cause in that scheduler path and add a test |
| Should | **Scheduler visibility.** About shows each scheduled job's last fire time and next fire time |

### Hosts

| Tier | Lock |
|------|------|
| Must | **OS end of life.** Issue [#49](https://github.com/bjorngluck/piherder/issues/49). A warning next to the OS line when the detected release is outside a small supported list. No auto-upgrade |
| Should | **OS patch error output.** OS patch jobs keep apt's error output, including when sudo drops `DEBIAN_FRONTEND` |
| Later | **NIC change.** Issue [#50](https://github.com/bjorngluck/piherder/issues/50). A hint when a new MAC appears for a hostname that already has a stale row at that IP. Device identity stays the MAC. The issue stays open |

### Git

| Tier | Lock |
|------|------|
| Must | **Checkout ownership.** The Repository page can read a checkout the SSH user does not own. Refuse that checkout. Never set `safe.directory=*`. That closes the filter-driver and credential-helper gap |

### Nginx Proxy Manager

| Tier | Lock |
|------|------|
| Discover | **NPM CRUD.** Create, edit, and delete proxy hosts. Admin-only, audited, no MCP on this train. Move can still retarget a backend |

### Supply chain and history

| Tier | Lock |
|------|------|
| Should | **History sweep.** Older environment details are already on main. Done when a grep of main and the wiki comes back clean, plus a list of published commits that still hold old environment details, for the maintainer's yes or no. No force-push |
| Should | **Supply-chain baseline.** Re-check pinned actions, the dependency lock, image digest pinning, Dependabot, and secret scanning. The [#52](https://github.com/bjorngluck/piherder/pull/52) mako and multidict bump landed as the first commit on this train. [#52](https://github.com/bjorngluck/piherder/pull/52) closes once the branch is green |
| Should | **Push denylist in CI.** Fail a push that adds fleet names, the home subnet, internal domains, or home paths. The denylist stays in a secret. Lands with the history sweep. The manual grep remains until then |

### Internals

| Tier | Lock |
|------|------|
| Later | **Database engine accessor.** Callers import the module-level engine. One `get_engine()` for every session stays a note |

| Topic | Lock |
|-------|------|
| Version bump | Package stays **1.11.0** until freeze. This open does not bump About |
| Demo | Do not point the public demo at this branch |
| Plugin | The Home Assistant card stays the separate HACS repo. Putting it in this image is not a backlog row |
| Issues | [#47](https://github.com/bjorngluck/piherder/issues/47), [#48](https://github.com/bjorngluck/piherder/issues/48), [#49](https://github.com/bjorngluck/piherder/issues/49), and [#50](https://github.com/bjorngluck/piherder/issues/50) stay open. Close an issue with the release, only when asked |

Nothing was voted **Out**.

---

## 2. Capture log

| Date | What changed |
|------|----------------|
| 2026-10-08 | **Notes opened.** NPM CRUD and remote restore moved here from v1.11. Two notes: an optional flag for API and MCP read audit, and `host_facts` kept out of the default Jobs and Audit lists. Nothing was built. |
| 2026-10-09 | **Direct copy arch.** Grey out the straight-out tick when the host CPU does not match the herder. v1.11 names the mismatch in the job and tells the operator to keep `/backups` for that host. |
| 2026-10-10 | **EOL badge and NIC change.** Issues [#49](https://github.com/bjorngluck/piherder/issues/49) and [#50](https://github.com/bjorngluck/piherder/issues/50). Re-filed without environment details. |
| 2026-10-11 | **Docker prune.** Issue [#48](https://github.com/bjorngluck/piherder/issues/48). |
| 2026-10-11 | **Candidates.** Listed, then voted on 2026-10-18. |
| 2026-10-11 | **Backup credential failure.** An expired or rejected credential for Google Drive, OneDrive, or a LAN share must open one critical alert. The backup category cannot lower it. |
| 2026-10-18 | **Train opened** from `main` after **v1.11.0** shipped. Tiers voted and grouped by area. Must: read audit, host facts lists, backup credential alert, self-backup last success, image update flag ([#47](https://github.com/bjorngluck/piherder/issues/47)), OS end of life ([#49](https://github.com/bjorngluck/piherder/issues/49)), update-check gap, admin alert, git checkout ownership. Should: direct-copy CPU tick, missed scheduled run, copy before the backups, Docker prune ([#48](https://github.com/bjorngluck/piherder/issues/48)), scheduler visibility, OS patch error output, MCP backup time, adapter readme badge, history sweep, supply-chain pass, push denylist. Discover: remote restore, direct hop vs a stuck folder hop, NPM CRUD, read-only admin API. Package stays `1.11.0`. |
| 2026-10-18 | **Team review.** Update-check gap, admin alert, and git checkout ownership are Must. History sweep is Should, and the push denylist lands with it. NPM CRUD is Discover. Must: read audit, host facts lists, backup credential alert, self-backup last success, image update flag ([#47](https://github.com/bjorngluck/piherder/issues/47)), OS end of life ([#49](https://github.com/bjorngluck/piherder/issues/49)), update-check gap, admin alert, git checkout ownership. Should: direct-copy CPU tick, missed scheduled run, copy before the backups, Docker prune ([#48](https://github.com/bjorngluck/piherder/issues/48)), scheduler visibility, OS patch error output, MCP backup time, adapter readme badge, history sweep, supply-chain pass, push denylist. Discover: remote restore, direct hop vs a stuck folder hop, NPM CRUD, read-only admin API. The [#52](https://github.com/bjorngluck/piherder/pull/52) lock bump is the first commit. Product work is not built. |

---

## 3. Next

| # | Step | Status |
|---|------|--------|
| 1 | Open **`v1.12.0-dev`** | **Done** 2026-10-18 |
| 2 | Must, by area | Jobs and audit (read audit, host facts lists), alerts (backup credential, self-backup last success, admin alert), Docker image flag ([#47](https://github.com/bjorngluck/piherder/issues/47)), OS end of life ([#49](https://github.com/bjorngluck/piherder/issues/49)), update-check gap, git checkout ownership. Not started |
| 3 | Should, if it fits | Backup (direct-copy CPU tick, missed scheduled run, copy before the backups), MCP and the adapter, Docker prune, scheduler visibility, OS patch error output, history sweep, supply-chain pass, push denylist |
| 4 | Discover | Remote restore. Direct hop vs a stuck folder hop. NPM CRUD. Read-only admin API. Promote or defer in the release notes |
| 5 | Freeze | Not set. Package stays `1.11.0` until then |
| 6 | Operator QA | Boxes in [QA_v1.12.0.md](QA_v1.12.0.md) are empty |

---

*Opened 2026-10-18. Package stays `1.11.0`. Walks are empty in [QA_v1.12.0.md](QA_v1.12.0.md).*
