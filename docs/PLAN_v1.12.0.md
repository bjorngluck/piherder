# PiHerder v1.12.0: quieter lists, then honest backup and image signals

**Status:** **Open** 2026-10-18. Quiet lists, the backup credential alert, the self-backup last-success line, the admin alert, the image update flag, the OS end-of-life warning, the update-check gap, and git checkout ownership are in. Direct copy on a different CPU is in. A missed scheduled run opens a warning. A destination copy scheduled before its host backups opens a warning. `list_servers` and `get_server` return the backup time. The adapter package that names those fields ships with the herder. Docker prune is a job. The GitHub adapter readme badges herder 1.11.0. About lists each scheduled job's last fire and next fire. An OS patch job keeps apt's error output, including when sudo drops DEBIAN_FRONTEND. Published history was rewritten. The restore wizard can pull one selected folder from a copy destination. A folder row left running does not hold the direct hop. An admin can create, edit, and delete an NPM proxy host. A token with the admin scope can read users, tokens, and the audit log. Draft notes are written. The tag is not cut. The push denylist checks added lines when its secret is set. The three lockfiles pin Mako 1.4.3 and multidict 6.9.1. Actions stay on major tags. Six medium Dependabot alerts stay open on main until this branch lands. PyPI 0.4.2 still badges 1.10.0 until the adapter cut that ships with this herder.  
**Date opened:** 2026-10-18  
**Git branch:** `v1.12.0-dev` → `main` · tag `v1.12.0` after merge, only when asked  
**Package / image version:** package stays **`1.11.0`** until freeze. About / footer say **1.11.0**. Release image tags stay `1.11.0` / `1.11` / `latest`. Pin `1.10.0` / `1.10` stays the previous image.  
**Theme:** quieter Jobs and Audit lists, then backup and image signals that stay honest  
**Baseline:** `v1.11.0` (tagged 2026-10-18; Hub digest `sha256:5b1a0ec8cfa5e2da26a486c5bc0f6d400ec86a8285e6eb9c2bbe64cfd30671b7`)  
**Mode:** **Must → Should → Discover.** Tiers were voted 2026-10-18. Operator walks are empty.  
**QA:** [QA_v1.12.0.md](QA_v1.12.0.md) (maintainer stub. **Not** the operator wiki.)  
**Related:** [PLAN_v1.11.0.md](PLAN_v1.11.0.md) · [RELEASE_v1.11.0.md](RELEASE_v1.11.0.md) · wiki [Jobs, audit, notifications](../wiki/day-to-day/jobs-audit-notifications.md) · wiki [Backups](../wiki/day-to-day/backups.md) · wiki [API tokens](../wiki/operations/api-tokens.md)

> Opened 2026-10-18 from `main` after **v1.11.0** shipped. Package stays `1.11.0`. The public demo is not pointed at this branch. Plugin **0.5.1** is the current card (the www copy runs off the event loop). This herder train does not ship the plugin. Adapter **0.4.2** stays. An adapter cut on this train ships with the herder, not ahead of it.

---

## 0. Intent

v1.11.0 shipped a passkey that counts as 2FA, a host that can copy straight to Drive, OneDrive, or a LAN share, and the Jobs and Audit rows that come with that work. This train makes the default lists quieter, and it keeps backup and image signals honest. The areas below are the vote. Must blocks the tag. Should is built on this train, and the tag can ship if that walk slips. Discover is capacity. Later stays a note.

---

## 1. Decision lock

### Jobs and audit

| Tier | Lock |
|------|------|
| Must | **In. Audit API and MCP reads.** An optional flag. Off unless the operator turns it on. When it is on, a `read` call from an API token or from hosted `/mcp` writes an audit row. The row stores the token id and name only, never the token value or prefix. Turning the flag on or off writes its own audit row. Read rows stay out of the default Audit list and have their own retention. Writes stay audited either way |
| Must | **In. Host facts clutter.** The 15-minute `host_facts` snapshot writes a Jobs row and an Audit row for every host. The default Jobs list and the default Audit list leave that routine snapshot out. A failed snapshot stays visible |
| Should | **Promoted from Discover. In. Read-only admin API.** A token with the `admin` scope can GET users, API tokens, and the audit log. The scope is off by default and `read` does not include it. MCP sign-in cannot receive it. Responses omit the password hash, the TOTP secret, the token secret, the token hash, and the token prefix. These GETs write an `api_read` row when read audit is on, at most one per token and path every 5 minutes. There is no write route and no MCP tool |

### Backup

| Tier | Lock |
|------|------|
| Should | **In. Direct copy on a different CPU.** The image carries rclone for amd64 and arm64. Direct copy sends the one that matches the host. The straight-out tick is greyed out when host facts name another CPU. An unknown architecture stays available. `/backups` stays for a host the binary cannot run on |
| Should | **In. Missed scheduled run.** Covers host backups and container update checks. A warning opens when a run does not start within 15 minutes of its slot. The next successful run resolves it. `/metrics` has `piherder_schedule_missed` |
| Should | **In. Copy before the backups.** Warn when a destination copy is scheduled earlier the same day than a host backup whose `/backups` folder it ships. The warning names those hosts and clears when the copy is no longer earlier. A direct host is not part of this check |
| Should | **Promoted from Discover. In. Remote restore.** The restore wizard can pull one selected folder from Google Drive, OneDrive, or a LAN share onto this PiHerder, then reverse-rsync it to the host. Dry-run does not write the pull. A direct host only lists destinations it sends to. The audit row names the destination and leaves out the sign-in. Those copies do not keep a Unix owner. A named volume can come back as the wrong account, and the container may then fail to start. This train does not repair ownership or mode. That is a [v1.13 note](PLAN_v1.13.0.md) |
| Discover | **In. Direct hop vs a stuck folder hop.** A folder row left running does not hold that tick's direct hop. The direct hop starts when the folder task ends, and a later copy enqueue starts it if the folder row is still running. A second folder copy still waits on that row. A cancel does not start the direct hop |
| Later | **Backup stagger ceiling.** The stagger reuses a minute past about 1,320 hosts on the default 02:00 base. That limit is documented |

### Alerts

| Tier | Lock |
|------|------|
| Must | **In. Backup credential failure.** Google Drive, OneDrive, and a LAN share (SMB or NAS) each open one critical alert when the saved credential expires or the destination rejects it. For Drive and OneDrive the alert says reconnect, not a generic failure. The alert and the log line may carry the provider error code (for example `invalid_grant`) and never the token, the rclone config, or the raw response body. Docs say to publish the OAuth app to production, because a Testing-mode app loses its token after 7 days. The copy keeps the full Drive scope, `https://www.googleapis.com/auth/drive`, because `drive.file` cannot see a folder the operator created. The alert stays critical even when the backup category is set lower. A later successful copy resolves it. v1.11 opens one alert, and that severity follows the backup category |
| Must | **In. Self-backup failed alert.** Include the last successful self-backup time, or "never". The time comes from the last successful job, or from the newest archive when that job row is gone |
| Must | **In. Admin alert.** An admin alert when a user or API token is created, or a 2FA setting changes (force 2FA, template 2FA). The alert names the actor and the client IP. Each event is its own open warning. A 2FA alert opens only when force 2FA or template 2FA changes. The body leaves out the password and the token |
| Later | **Stuck queue.** One alert when any job stays pending past N minutes (default 15) with no worker heartbeat. Resolve when the queue drains |

### MCP and the adapter

| Tier | Lock |
|------|------|
| Should | **In. Backup time on MCP.** `list_servers` and `get_server` return `effective_backup_schedule`, `next_backup_run`, and `backup_scheduled`. The tool text names those fields. The adapter package that carries that text ships with the herder. It is not published ahead of this train |
| Should | **In. Adapter readme badge.** The GitHub readme on piherder-mcp main badges herder 1.11.0. PyPI **0.4.2** still freezes the 1.10.0 readme. That page updates on the adapter cut that ships with this herder. The adapter stays **0.4.2** until then |
| Later | **OAuth token names.** MCP OAuth tokens take their name from the client name, so two agents that send the same name look the same. A short suffix stays a note |

The read-audit flag under Jobs and audit covers hosted `/mcp` as well as API tokens. It is not a second MCP row.

### Docker

| Tier | Lock |
|------|------|
| Must | **In. Image update flag.** Issue [#47](https://github.com/bjorngluck/piherder/issues/47). A check still flags an image when the pull changes its id. It also keeps the flag when a running container is still on the previous id. The flag clears once every running container for that image uses the id on disk. The issue stays open |
| Should | **In. Docker prune.** Issue [#48](https://github.com/bjorngluck/piherder/issues/48). The host Docker page, the token API, and hosted `/mcp` start a `docker_prune` job. The API and MCP need the `edit` scope. `both` is dangling images and exited containers. Networks are their own type. Volume prune stays on the Docker page until the operator confirms it. Every prune writes an audit row. The issue stays open. Build cache and buildx cache are a [v1.13 note](PLAN_v1.13.0.md), not this train |

### Scheduler

| Tier | Lock |
|------|------|
| Must | **In. Update-check gap.** A saved container check cron still registers while fleet container checks are on. Apply had cleared the per-host flag and left the cron, so the scheduler removed the job. Applying with container checks off now clears that cron |
| Should | **In. Scheduler visibility.** About shows each scheduled job's last fire time and next fire time. Last fire is the last time this web process ran the job. A restart clears it |

### Hosts

| Tier | Lock |
|------|------|
| Must | **In. OS end of life.** Issue [#49](https://github.com/bjorngluck/piherder/issues/49). A warning next to the OS line when the detected release is outside a small supported list. The list is Ubuntu 22.04, 24.04, and 26.04 LTS, and Debian or Raspbian 12 and 13. No auto-upgrade |
| Should | **In. OS patch error output.** OS patch jobs keep apt's error output, including when sudo drops `DEBIAN_FRONTEND`. The job log and the summary keep an `E:` line, and a debconf or sudo line when that is the error |
| Later | **NIC change.** Issue [#50](https://github.com/bjorngluck/piherder/issues/50). A hint when a new MAC appears for a hostname that already has a stale row at that IP. Device identity stays the MAC. The issue stays open |

### Git

| Tier | Lock |
|------|------|
| Must | **In. Checkout ownership.** Repository refuses a folder or checkout the SSH user does not own, before git runs. `safe.directory` is not set. That closes the filter-driver and credential-helper gap |

### Nginx Proxy Manager

| Tier | Lock |
|------|------|
| Should | **Promoted from Discover. In. NPM CRUD.** An admin can create, edit, and delete a proxy host from the NPM integration page. The form sets domains, forward scheme, host, and port, enabled, an existing certificate, and force SSL. It does not request a certificate. Each change writes an audit row and leaves out the NPM password. There is no MCP tool. Move still only changes `forward_host` |

### Supply chain and history

| Tier | Lock |
|------|------|
| Should | **In. History sweep.** Published history was rewritten on 2026-10-19. The home path, the home subnet, and internal hostnames are gone from `main`, the release tags, `gh-pages`, and the open side branches. The public docs host, the public demo host, and the demo login stay. The map is [HISTORY_SWEEP_v1.12.md](HISTORY_SWEEP_v1.12.md). `main` rejects force-push again |
| Should | **In. Supply-chain baseline.** `uv.lock` now matches the exported locks: Mako **1.4.3** and multidict **6.9.1**. PyJWT **2.15.1** and urllib3 **2.8.0** stay. The release action `softprops/action-gh-release` is pinned to `3bb12739c298aeb8a4eeaf626c5b8d85266b0e65` (v2.6.2). Other actions stay on major tags. The image base stays `python:3.12-slim-bookworm`. Dependabot security updates stay on, and version-update pull requests stay off. Six medium alerts for Mako and multidict stay open on `main` until this branch lands. Secret scanning has no open alerts. [#52](https://github.com/bjorngluck/piherder/pull/52) closes once the branch is green |
| Should | **In. Push denylist in CI.** Added lines fail when they contain a term from the secret `PIHERDER_PUSH_DENYLIST`. The patterns are not in the repo. An empty secret prints that the manual grep remains and exits 0. It does not echo the secret or the matched line. The secret is not set yet |

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
| 2026-10-18 | **Quiet lists.** Optional API and MCP read audit is off until Security policy turns it on. A row stores the token id and name, and the client IP, never the token. Turning the flag on or off writes its own audit row. Read rows stay off the default Audit list and keep their own retention. The default Jobs and Audit lists leave a successful `host_facts` snapshot out. A failed snapshot stays. Package stays `1.11.0`. |
| 2026-10-18 | **Backup credential alert.** One critical alert per Drive, OneDrive, or LAN destination when the sign-in is rejected. The Backups category cannot lower it. Drive and OneDrive say reconnect. The alert and the job line may carry the provider error code and do not carry the token, the rclone config, or the raw body. A later successful copy resolves it. Docs say to publish the Google app to production, because Testing drops the token after 7 days. The copy still requests the full Drive scope. `drive.file` cannot see a folder the operator created. |
| 2026-10-18 | **Self-backup alert.** A failed self-backup still opens one critical alert. The body includes the last successful self-backup time, or "never". The time is the later of the last successful job and the newest archive. A failed job does not count. The scheduler path uses the same line. |
| 2026-10-18 | **Admin alert.** Creating a user, creating an API token, or changing force 2FA or template 2FA opens one warning. The body names the actor and the client IP. It does not include a password or a token. Each event stays its own open row. Registration, SSO provisioning, the Settings form, `POST /api/v1/tokens`, and a browser MCP sign-in are included. An MCP refresh is not. Grace days do not open it. |
| 2026-10-18 | **Image update flag.** A check still flags an image when the pull changes its id. It also keeps the flag when a running container is still on the previous id. The flag clears once every running container for that image uses the id on disk. Issue [#47](https://github.com/bjorngluck/piherder/issues/47) stays open. |
| 2026-10-18 | **OS end of life.** The host page shows **Unsupported** next to the OS line when Ubuntu, Debian, or Raspbian is outside Ubuntu 22.04, 24.04, and 26.04 LTS, or Debian and Raspbian 12 and 13. HAOS is not in this check. The warning does not start an upgrade. Issue [#49](https://github.com/bjorngluck/piherder/issues/49) stays open. |
| 2026-10-18 | **Update-check gap.** Scheduled container checks stopped after 11 Oct because Apply cleared the per-host flag and left the cron. The scheduler only registered a job when that flag was on, so the next start removed it. OS checks stayed on and kept running. A saved container cron now registers while fleet container checks are on. Applying with that check off clears the cron. |
| 2026-10-18 | **Checkout ownership.** Repository refuses a folder or checkout the SSH user does not own, before git runs. `safe.directory` is not set. Repo hooks, fsmonitor, and a repo sshCommand stay disabled. |
| 2026-10-18 | **Direct copy CPU.** The image carries rclone for amd64 and arm64, and the copy sends the one that matches the host. The straight-out tick is greyed out when host facts name another CPU. An unknown architecture stays available. A host the binary cannot run on keeps `/backups`. |
| 2026-10-19 | **Missed scheduled run.** A host backup or a container update check that does not start within 15 minutes of its slot opens one warning. The next successful run of that kind resolves it. Turning the schedule off resolves it too. `/metrics` exposes `piherder_schedule_missed`. |
| 2026-10-19 | **Copy before the backups.** A destination copy scheduled earlier the same day than a ticked host backup opens one warning and names those hosts. It clears when the copy is no longer earlier. A direct host is not included. The Settings copy list shows the same sentence. |
| 2026-10-19 | **Backup time on MCP.** `list_servers` and `get_server` return `effective_backup_schedule`, `next_backup_run`, and `backup_scheduled`. The tool text names those fields. `backup_scheduled` is false only when backups are on, the scheduler is running, and no job is registered. Null means that state is unknown. The adapter package that carries that text ships with the herder. It stays **0.4.2** until then. |
| 2026-10-19 | **Docker prune.** The host Docker page, `POST /api/v1/servers/{id}/docker/prune`, and hosted `prune_docker` start a `docker_prune` job. The API and MCP need the `edit` scope. Volume prune stays on the Docker page until the operator confirms it. Every run writes an audit row. Issue [#48](https://github.com/bjorngluck/piherder/issues/48) stays open. No schedule and no prune-after-update on this train. Build cache and buildx cache are a [v1.13 note](PLAN_v1.13.0.md). |
| 2026-10-19 | **Adapter readme badge.** The GitHub readme on piherder-mcp main badges herder 1.11.0. PyPI **0.4.2** still freezes the 1.10.0 readme. That page updates on the adapter cut that ships with this herder. The adapter stays **0.4.2** and is not published ahead of it. |
| 2026-10-19 | **Scheduler visibility.** About lists each scheduled job with its last fire and its next fire. Last fire is the last time this web process ran the job. A restart clears it. A missed slot is not a fire. |
| 2026-10-19 | **OS patch error output.** An OS patch job keeps apt's error output when sudo drops `DEBIAN_FRONTEND`. A carriage-return rewrite no longer replaces a debconf, sudo, or `E:` line. The job summary names that line. |
| 2026-10-19 | **History sweep.** The working tree no longer carries the home path, the home subnet, or an internal hostname. The public docs host, the public demo, and the demo login stay. Published tags are listed in [HISTORY_SWEEP_v1.12.md](HISTORY_SWEEP_v1.12.md). The push denylist workflow reads secret `PIHERDER_PUSH_DENYLIST`. An empty secret does not pretend it checked. |
| 2026-10-19 | **NPM CRUD.** An admin can create, edit, and delete a proxy host from the NPM page. No new certificate request. Audit rows leave out the NPM password. No MCP tool. Move still only changes `forward_host`. |
| 2026-10-19 | **Direct hop.** A folder row left running does not hold that tick's direct hop. The direct hop starts when the folder task ends, and the next copy enqueue starts it if the folder row is still running. A second folder copy still waits. A cancel does not start the direct hop. |
| 2026-10-19 | **Remote restore.** The restore wizard can pull one selected folder from Google Drive, OneDrive, or a LAN share onto this PiHerder, then reverse-rsync it to the host. Dry-run does not write the pull. A direct host only lists destinations it sends to. The audit row names the destination and leaves out the sign-in. Those copies do not keep a Unix owner. Repairing volume ownership and mode is a [v1.13 note](PLAN_v1.13.0.md), not this train. |
| 2026-10-19 | **History rewrite.** Published history was rewritten and force-pushed. `main` is `086400e`. `v1.12.0-dev` is `6e7ffb6` and its tree matches the pre-rewrite tip. Tags `v0.2.0` through `v1.11.0`, `gh-pages`, and the open side branches moved with it. `main` rejects force-push again. |
| 2026-10-19 | **Supply-chain pass.** `uv.lock` was behind the exported locks. All three now pin Mako **1.4.3** and multidict **6.9.1**. Actions stay on major tags. The base image stays a tag. Dependabot still has six medium alerts on `main` for those two packages. They close when this branch lands. Secret scanning has no open alerts. |
| 2026-10-20 | **Draft release notes.** [RELEASE_v1.12.0.md](RELEASE_v1.12.0.md) is a draft. Package stays `1.11.0`. Walks stay empty. Screenshots are reserved and not captured. Draft pull request [#54](https://github.com/bjorngluck/piherder/pull/54). |
| 2026-10-20 | **Read-only admin API.** Scope `admin` is off by default and is not implied by `read`. GET `/api/v1/admin/users`, `/tokens`, and `/audit` (default 50, max 100). Responses omit the password hash, the TOTP secret, the token secret, the token hash, and the token prefix. These GETs write an `api_read` row when read audit is on, at most one per token and path every 5 minutes. MCP sign-in cannot receive the scope. No write route and no MCP tool. |
| 2026-10-20 | **Release action pin.** `softprops/action-gh-release` is pinned to `3bb12739c298aeb8a4eeaf626c5b8d85266b0e65` (v2.6.2). Other actions stay on major tags. |
| 2026-10-20 | **Promoted from Discover.** Remote restore, NPM CRUD, and the read-only admin API. |

---

## 3. Next

| # | Step | Status |
|---|------|--------|
| 1 | Open **`v1.12.0-dev`** | **Done** 2026-10-18 |
| 2 | Must, by area | **Done.** Jobs and audit, the backup credential alert, the self-backup last-success line, the admin alert, the image update flag, the OS end-of-life warning, the update-check gap, and git checkout ownership are in |
| 3 | Should, if it fits | Direct copy on a different CPU is in. Missed scheduled run is in. Copy before the backups is in. Backup time is on `list_servers` and `get_server`. Docker prune is a job. The GitHub adapter readme badges herder 1.11.0. About lists each scheduled job's last fire and next fire. An OS patch job keeps apt's error output, including when sudo drops DEBIAN_FRONTEND. Published history was rewritten. The restore wizard can pull one selected folder from a copy destination. A folder row left running does not hold the direct hop. An admin can create, edit, and delete an NPM proxy host. The push denylist checks added lines when its secret is set. The three lockfiles pin Mako 1.4.3 and multidict 6.9.1. PyPI 0.4.2 still badges herder 1.10.0 until the adapter cut that ships with this herder |
| 4 | Discover | A direct hop no longer waits on a folder row left running. Remote restore, NPM CRUD, and the read-only admin API were promoted from Discover. Draft notes are [RELEASE_v1.12.0.md](RELEASE_v1.12.0.md). The tag is not cut |
| 5 | Freeze | Not set. Package stays `1.11.0` until then |
| 6 | Operator QA | Boxes in [QA_v1.12.0.md](QA_v1.12.0.md) are empty |

---

*Opened 2026-10-18. Package stays `1.11.0`. Walks are empty in [QA_v1.12.0.md](QA_v1.12.0.md).*
