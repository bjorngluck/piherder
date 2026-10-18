# PiHerder v1.11.0 — a passkey counts as 2FA, then a host copies straight out

**Status:** **Released** 2026-10-18. Package **1.11.0**. Operator QA signed. Screenshots wired. Tag and Hub publish follow approval of PR #35.  
**Date opened:** 2026-10-06  
**Git branch:** `v1.11.0-dev` → `main` · tag `v1.11.0` after merge, only when asked  
**Package / image version:** package **`1.11.0`**. About / footer say **1.11.0**. Release image tags are `1.11.0` / `1.11` / `latest`. Pin `1.10.0` / `1.10` stays the previous image.  
**Theme:** a passkey counts as 2FA, then a host copies straight to Drive, OneDrive, or the NAS  
**Baseline:** `v1.10.0` (tagged 2026-10-05; Hub digest `sha256:0a1286cb0864e153ea1af6ba8b458175da051c8a54fc98d701539c08dcd99d04`)  
**Mode:** **Must → Should → Discover.** Must **passkey versus Force 2FA** · **Path C discovery** · **Path C build**. Should **MCP Move**, **MCP nmap**, **discovery devices**, and **git-rich onboard** have landed. Operator walks signed 2026-10-18.  
**QA:** [QA_v1.11.0.md](QA_v1.11.0.md) (maintainer stub — **not** the operator wiki)  
**Related:** [PLAN_v1.10.0.md](PLAN_v1.10.0.md) · [RELEASE_v1.10.0.md](RELEASE_v1.10.0.md) · [FEATURE_PLAN_BACKUP_DESTINATIONS.md](FEATURE_PLAN_BACKUP_DESTINATIONS.md) · wiki [Backups](../wiki/day-to-day/backups.md) · wiki [Agents (MCP)](../wiki/operations/mcp.md)

> Opened 2026-10-06 from `main` after **v1.10.0** shipped. The passkey fix has landed. Path C is opt-in beside the pull. Hosted `/mcp` can start a stop-first Move and can read or start a LAN Discovery scan of the saved ranges. Adapter **[0.4.0](https://github.com/bjorngluck/piherder-mcp/releases/tag/v0.4.0)** is on PyPI. A cached **0.3.1** does not send those three tools. Adapter **[0.4.1](https://github.com/bjorngluck/piherder-mcp/releases/tag/v0.4.1)** adds the device tools from issue [#34](https://github.com/bjorngluck/piherder/issues/34). Adapter **[0.4.2](https://github.com/bjorngluck/piherder-mcp/releases/tag/v0.4.2)** sends `confirm=true` on purge. `uvx piherder-mcp` installs **0.4.2**. A cached **0.4.1** omits that query. A cached **0.4.0** does not send those device tools. Production stays **v1.10.0**. The public demo is not pointed at this branch.

---

## 0. Intent

v1.10.0 shipped OneDrive beside Google Drive and a LAN share, a copy of the herder self-backup, and browser sign-in for hosted `/mcp`. A passkey now counts as 2FA when Force 2FA is on. A host backup is still a pull onto `/backups` unless that host opts in. Path C was discovered in [PLAN_v1.10.0.md](PLAN_v1.10.0.md) §4.

This train fixes the passkey gate and builds the Path C choice below. An agent can start a Move, and can start or read a LAN Discovery scan. Operator walks signed 2026-10-18.

---

## 1. Decision lock

| Topic | Lock |
|-------|------|
| Must, first | **Passkey versus Force 2FA.** On signup, with 2FA enforced, a passkey cannot be registered. A passkey already in place still forces app-based 2FA. A passkey is not treated as 2FA on the account. The tag waits on this |
| Must | **Path C discovery.** A host can opt in and pick one or more saved destinations. None start selected. Save refuses the opt-in with no destination. The host sends its files straight to those destinations. Nothing of that host lands on `/backups` first, and the host does not store a second copy. Hosts that do not opt in still rsync onto `/backups`, and the herder copy of that mirror stays. Copy now on a direct host starts that destination's push. rclone reads the original files in place for that run, as root when the backup user is not root, using /var/lib/piherder/rclone. The temp config is deleted when the run ends. Input was [PLAN_v1.10.0.md](PLAN_v1.10.0.md) §4 |
| Must | **Path C build.** That choice is on this branch. Operator walk signed 2026-10-18. The tag waits on the ship step. A finished **Backup** job and its audit row name the destination. The sign-in stays out. Restore from Drive, OneDrive, or the NAS is a note on [PLAN_v1.12.0.md](PLAN_v1.12.0.md) |
| Should | **MCP Move.** Hosted `/mcp` `start_move` starts a stop-first Move. `confirm` must be true. The source is left stopped. Undo stays out. `trigger_job` still refuses `service_migrate`. The tag can ship if the operator walk slips |
| Should | **MCP nmap.** Hosted `/mcp` can read a LAN Discovery scan and can start one of the saved ranges. The agent does not choose the ranges. Vulnerability scripts stay off. The tag can ship if the operator walk slips |
| Should | **Discovery devices.** Issue [#34](https://github.com/bjorngluck/piherder/issues/34). An agent can rename a device, mark it known, new, or ignored, link it to a server, and purge one device or the offline rows. A linked device cannot be purged. A one-device scan stays inside the saved ranges and keeps vulnerability scripts off. Kind and map role stay on the rename. Adapter **0.4.2** is on PyPI and sends `confirm=true` on purge. **0.4.1** omits that query. A cached **0.4.0** does not send the device tools |
| Moved | **NPM CRUD** and **remote restore** are notes on [PLAN_v1.12.0.md](PLAN_v1.12.0.md). They are not part of this train |
| Should | **Git-rich onboard.** Landed. Docker → Repository on an existing project. The operator attaches a remote, picks the branch (remote default starts selected) or a tag, and sees a tag that is ahead of the checkout. Update moves the checkout. A tracked local edit or a local commit stops that update until the operator keeps the local files or takes the remote copies. Untracked files stay. The stack is not deployed. Operator walk signed 2026-10-18 |
| Version bump | Package is **1.11.0**. Release image tags are `1.11.0` / `1.11` / `latest`. The tag and the Hub push follow approval of PR #35 |
| Demo | Do not point the public demo at this branch |
| Plugin | The Home Assistant card stays the separate HACS repo. Putting it in this image is not a backlog row |

**Out.** Path B. AC-fg. Brand-3. ACME-in-herder. A richer Files token API. M-live. A herder→Home Assistant webhook. Undo of a finished Move. MCP undo, the console, token admin, `docker_stack_down`, and `docker_stack_remove`. restic. SMB Kerberos. Selectable hero stats. A templates catalog redesign. Ansible / cloud-init. Discord / Discussions. Swarm / Kubernetes. A higher coverage fail-under (stays **80**). The console mobile Tab issue. Stricter command-audit redaction. CodeQL. Actions pinned to commit SHAs. `CODEOWNERS`.

**Not planned.** Dropped from the living backlog. borg. kopia. rclone crypt. Optional AI.

---

## 2. Capture log

| Date | Note |
|------|------|
| 2026-10-06 | Train opened from `main` after **v1.10.0** shipped. Must is the passkey gate, the Path C decision, and the Path C build. Should is MCP Move and MCP nmap. Discover is NPM CRUD, remote restore, and git-rich onboard. Package stays `1.10.0`. |
| 2026-10-06 | **Passkey versus Force 2FA.** Signup can add a passkey. A passkey satisfies the enroll wall. The account, the Users list, and `recover_admin list` treat a passkey as 2FA. The authenticator app stays optional. Not a version bump. |
| 2026-10-06 | **Path C.** Opt in, beside the pull. A direct host pushes with rclone copied for the run. The temp config is deleted when the run ends. Other hosts still rsync onto `/backups`. Copy now on a direct host starts that push. Restore from the remote copy is not built. Not a version bump. |
| 2026-10-06 | **Path C targets.** Backup now and the host schedule can send to one saved destination or to all of them. Copy now stays the destination that is open. Not a version bump. |
| 2026-10-06 | **Path C targets, again.** All configured is gone. None are selected until the operator picks. Save refuses a direct host with no destination. The opt-in stays unavailable until a destination is saved under Settings. Copy now stays the destination that is open. |
| 2026-10-06 | **Path C read.** A direct host reads the original files in place as root. No second copy on the host. The least-privilege script allows `/var/lib/piherder/rclone` for that run. |
| 2026-10-07 | **Docs.** Wiki and the living plans describe the in-place read, the required destination pick, and the sudoers path. QA boxes stay empty. Not a version bump. |
| 2026-10-07 | **Path C live files.** A file that grows during the copy, such as a live log, is sent at the size first seen. That does not fail the run. Not a version bump. |
| 2026-10-07 | **Path C jobs.** **Copy now** and the destination schedule for a direct host stay `backup_replicate` and show **Direct copy**. A herder-folder hop stays **Backup copy**. **Backup now** on the host stays a **Backup** job. The two copy hops share one destination slot. Not a version bump. |
| 2026-10-07 | **Path C where.** A finished direct **Backup** job and its audit row name the destination. The sign-in stays out. Not a version bump. |
| 2026-10-07 | **Docs.** The jobs table, the audit summary, and the backup notes describe that destination. QA stays empty. Not a version bump. |
| 2026-10-07 | **MCP Move and MCP nmap.** Hosted `/mcp` can start a stop-first Move and can read or start a LAN Discovery scan of the saved ranges. Undo stays out. `trigger_job` still refuses `service_migrate`. The agent does not choose scan ranges. Vulnerability scripts stay off. Adapter **0.4.0** lists `start_move`, `read_discovery`, and `start_discovery`. Adapter **0.3.1** does not. QA stays empty. Herder package stays `1.10.0`. |
| 2026-10-07 | **Adapter tag.** [v0.4.0](https://github.com/bjorngluck/piherder-mcp/releases/tag/v0.4.0) is on PyPI. `uvx piherder-mcp` installs **0.4.0**. A cached **0.3.1** does not send the three tools. Herder package stays `1.10.0`. |
| 2026-10-07 | **Discovery devices.** Issue [#34](https://github.com/bjorngluck/piherder/issues/34). The token API and hosted `/mcp` can rename, mark, link, and purge LAN Discovery devices. A linked device cannot be purged. A one-device scan stays inside the saved ranges. Tag **[v0.4.1](https://github.com/bjorngluck/piherder-mcp/releases/tag/v0.4.1)**. `uvx piherder-mcp` installs **0.4.1**. A cached **0.4.0** does not send the device tools. QA stays empty. Herder package stays `1.10.0`. |
| 2026-10-09 | **Adapter 0.4.2.** Purge calls send `confirm=true`. Tag **[v0.4.2](https://github.com/bjorngluck/piherder-mcp/releases/tag/v0.4.2)**. **0.4.1** omits that query and gets 400 on this train. Herder package stays `1.10.0`. |
| 2026-10-10 | **Fleet health.** The timezone, host-facts, backup-alert, Docker-off, direct-copy target, compose-name, and apt-log fixes are on this train. [#49](https://github.com/bjorngluck/piherder/issues/49) (end of life) and [#50](https://github.com/bjorngluck/piherder/issues/50) (NIC change) are notes on [PLAN_v1.12.0.md](PLAN_v1.12.0.md). Package stays `1.10.0`. |
| 2026-10-11 | **Backup schedule.** Issue [#51](https://github.com/bjorngluck/piherder/issues/51). The first note said Apply writes a staggered time onto a host with backups on and no schedule. Superseded the same day: Apply does not write a schedule. Package on that morning was `1.10.0`. |
| 2026-10-11 | **Draft notes.** [RELEASE_v1.11.0.md](RELEASE_v1.11.0.md) has a bug-fix table. QA boxes stay empty. |
| 2026-10-11 | **Backup schedule fallback.** A host with backups on and no saved schedule uses the fleet time without writing it. The minute comes from the host id, so a large fleet does not share one minute. About reported `1.11.0-dev` until the package bump later the same day. Hub tags stay `1.10.0`. |
| 2026-10-11 | **Inherited backup time.** Apply turns backups on and does not write a schedule. Adding a host and the wizard turn backups on and do not write a schedule. A saved schedule wins. Saving a new base clock re-registers inheriting jobs. Startup clears a schedule only when it is exactly the fleet minute for that host. A daily base does not wrap past 23:00. A list or step hour only changes the minute. A backups-off host shows no schedule label. |
| 2026-10-11 | **Package 1.11.0.** About, `/api/v1`, and hosted MCP report `1.11.0`. Hub tags stay `1.10.0`. |
| 2026-10-07 | **Git-rich onboard.** Still a note. A cloned project can show a newer commit on the remote, and the operator can update the checkout. No automatic deploy. A stack still comes from a compose file or a template. |
| 2026-10-07 | **Git-rich onboard.** The operator picks the branch (`main` or `master`, starting on the remote default) or a tag. The page lists tags and marks a newer tag than the checkout. Update follows that choice. No automatic deploy. |
| 2026-10-07 | **Git-rich onboard.** An existing project can be attached to a remote. A tracked file that differs locally stops the update. The operator keeps that file or takes the remote copy. Untracked files stay. |
| 2026-10-08 | **Git checkout sample.** One project was already a checkout of `main` at a tag. The remote branch and a newer tag were ahead. Local edits were the compose file and an example config. Certs and the live config were outside that diff. |
| 2026-10-08 | **Second checkout.** Another tree was `main` at `021e9b5`, level with `origin/main`, one commit after tag `v1.10.0`. No tracked local edits. Ignored env and cert files stayed out of git. |
| 2026-10-08 | **Git-rich onboard landed.** Docker → Repository attaches a remote, lists branches and tags, marks a tag ahead of the checkout, and updates only after a local edit is kept or replaced. Untracked files stay. The stack is not deployed. QA stays empty. Package stays `1.10.0`. |
| 2026-10-08 | **Discover moved.** NPM CRUD and remote restore are notes on [PLAN_v1.12.0.md](PLAN_v1.12.0.md). That plan also notes an optional flag for API and MCP read audit, and `host_facts` kept out of the default Jobs and Audit lists. Nothing there is built. |
| 2026-10-08 | **Code freeze.** Notes are [RELEASE_v1.11.0.md](RELEASE_v1.11.0.md). QA boxes stay empty. Screenshot names are `account-passkey-2fa.png`, `host-backup-direct.png`, `jobs-direct-copy.png`, and `docker-repository.png`. Package stays `1.10.0`. |
| 2026-10-18 | **Operator QA and screenshots.** The walks are signed. The four frames are wired, with names and paths masked. Release notes are written as the shipped **1.11.0** release, including the bug-fix table. Tag and Hub follow approval of PR #35. |
| 2026-10-18 | **Operator QA.** Passkey versus Force 2FA, Path C decision, Path C build, MCP Move, MCP nmap, discovery devices, git-rich onboard, and Fleet health signed. The 1.10 regression is verified. |

---

## 3. Next

| # | Step | Status |
|---|------|--------|
| 1 | Open **`v1.11.0-dev`** | **Done** 2026-10-06 |
| 2 | **Passkey versus Force 2FA** | **Signed** 2026-10-18 in [QA_v1.11.0.md](QA_v1.11.0.md) |
| 3 | **Path C** decision, then the build | **Choice written. Build landed. Signed** 2026-10-18. Opt in, beside the pull |
| 4 | Should, if it fits | **MCP Move**, **MCP nmap**, **discovery devices** ([#34](https://github.com/bjorngluck/piherder/issues/34)). **Signed** 2026-10-18 |
| 5 | Discover write-ups | **Git-rich onboard** signed 2026-10-18. **NPM CRUD** and remote restore moved to [PLAN_v1.12.0.md](PLAN_v1.12.0.md) |
| 6 | Freeze | **Set** 2026-10-08. Package is **1.11.0**. Tag and Hub follow approval of PR #35 |
| 7 | Operator QA and screenshots | **Signed** 2026-10-18. Four frames wired. Names in the frames are masked |

---

*Opened 2026-10-06. Package is `1.11.0`. Operator walks signed 2026-10-18 in [QA_v1.11.0.md](QA_v1.11.0.md). Release notes: [RELEASE_v1.11.0.md](RELEASE_v1.11.0.md).*
