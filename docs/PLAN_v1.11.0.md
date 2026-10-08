# PiHerder v1.11.0 — a passkey counts as 2FA, then a host copies straight out

**Status:** **Code freeze** 2026-10-08. Package stays **1.10.0**. Tag and Hub publish stay for the ship step.  
**Date opened:** 2026-10-06  
**Git branch:** `v1.11.0-dev` → `main` · tag `v1.11.0` after merge, only when asked  
**Package / image version:** stays **`1.10.0`** until freeze. About / footer still say **1.10.0**. Image on Hub stays `1.10.0` / `1.10` / `latest`.  
**Theme:** a passkey counts as 2FA, then a host copies straight to Drive, OneDrive, or the NAS  
**Baseline:** `v1.10.0` (tagged 2026-10-05; Hub digest `sha256:0a1286cb0864e153ea1af6ba8b458175da051c8a54fc98d701539c08dcd99d04`)  
**Mode:** **Must → Should → Discover.** Must **passkey versus Force 2FA** · **Path C discovery** · **Path C build**. Should **MCP Move**, **MCP nmap**, **discovery devices**, and **git-rich onboard** have landed. Walks are still empty.  
**QA:** [QA_v1.11.0.md](QA_v1.11.0.md) (maintainer stub — **not** the operator wiki)  
**Related:** [PLAN_v1.10.0.md](PLAN_v1.10.0.md) · [RELEASE_v1.10.0.md](RELEASE_v1.10.0.md) · [FEATURE_PLAN_BACKUP_DESTINATIONS.md](FEATURE_PLAN_BACKUP_DESTINATIONS.md) · wiki [Backups](../wiki/day-to-day/backups.md) · wiki [Agents (MCP)](../wiki/operations/mcp.md)

> Opened 2026-10-06 from `main` after **v1.10.0** shipped. The passkey fix has landed. Path C is opt-in beside the pull. Hosted `/mcp` can start a stop-first Move and can read or start a LAN Discovery scan of the saved ranges. Adapter **[0.4.0](https://github.com/bjorngluck/piherder-mcp/releases/tag/v0.4.0)** is on PyPI. `uvx piherder-mcp` installs it. A cached **0.3.1** does not send those three tools. Adapter **[0.4.1](https://github.com/bjorngluck/piherder-mcp/releases/tag/v0.4.1)** adds the device tools from issue [#34](https://github.com/bjorngluck/piherder/issues/34). `uvx piherder-mcp` installs **0.4.1**. A cached **0.4.0** does not send those device tools. Production stays **v1.10.0**. The public demo is not pointed at this branch.

---

## 0. Intent

v1.10.0 shipped OneDrive beside Google Drive and a LAN share, a copy of the herder self-backup, and browser sign-in for hosted `/mcp`. A passkey now counts as 2FA when Force 2FA is on. A host backup is still a pull onto `/backups` unless that host opts in. Path C was discovered in [PLAN_v1.10.0.md](PLAN_v1.10.0.md) §4.

This train fixes the passkey gate and builds the Path C choice below. An agent can start a Move, and can start or read a LAN Discovery scan. The operator walk is still empty.

---

## 1. Decision lock

| Topic | Lock |
|-------|------|
| Must, first | **Passkey versus Force 2FA.** On signup, with 2FA enforced, a passkey cannot be registered. A passkey already in place still forces app-based 2FA. A passkey is not treated as 2FA on the account. The tag waits on this |
| Must | **Path C discovery.** A host can opt in and pick one or more saved destinations. None start selected. Save refuses the opt-in with no destination. The host sends its files straight to those destinations. Nothing of that host lands on `/backups` first, and the host does not store a second copy. Hosts that do not opt in still rsync onto `/backups`, and the herder copy of that mirror stays. Copy now on a direct host starts that destination's push. rclone reads the original files in place for that run, as root when the backup user is not root, using /var/lib/piherder/rclone. The temp config is deleted when the run ends. Input was [PLAN_v1.10.0.md](PLAN_v1.10.0.md) §4 |
| Must | **Path C build.** That choice is on this branch. The tag waits on the operator walk. A finished **Backup** job and its audit row name the destination. The sign-in stays out. Restore from Drive, OneDrive, or the NAS is a note on [PLAN_v1.12.0.md](PLAN_v1.12.0.md) |
| Should | **MCP Move.** Hosted `/mcp` `start_move` starts a stop-first Move. `confirm` must be true. The source is left stopped. Undo stays out. `trigger_job` still refuses `service_migrate`. The tag can ship if the operator walk slips |
| Should | **MCP nmap.** Hosted `/mcp` can read a LAN Discovery scan and can start one of the saved ranges. The agent does not choose the ranges. Vulnerability scripts stay off. The tag can ship if the operator walk slips |
| Should | **Discovery devices.** Issue [#34](https://github.com/bjorngluck/piherder/issues/34). An agent can rename a device, mark it known, new, or ignored, link it to a server, and purge one device or the offline rows. A linked device cannot be purged. A one-device scan stays inside the saved ranges and keeps vulnerability scripts off. Kind and map role stay on the rename. Adapter **0.4.1** is on PyPI. A cached **0.4.0** does not send the device tools |
| Moved | **NPM CRUD** and **remote restore** are notes on [PLAN_v1.12.0.md](PLAN_v1.12.0.md). They are not part of this train |
| Should | **Git-rich onboard.** Landed. Docker → Repository on an existing project. The operator attaches a remote, picks the branch (remote default starts selected) or a tag, and sees a tag that is ahead of the checkout. Update moves the checkout. A tracked local edit or a local commit stops that update until the operator keeps the local files or takes the remote copies. Untracked files stay. The stack is not deployed. Walk still empty |
| Version bump | `1.11.0` **at freeze only** |
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
| 2026-10-07 | **Git-rich onboard.** Still a note. A cloned project can show a newer commit on the remote, and the operator can update the checkout. No automatic deploy. A stack still comes from a compose file or a template. |
| 2026-10-07 | **Git-rich onboard.** The operator picks the branch (`main` or `master`, starting on the remote default) or a tag. The page lists tags and marks a newer tag than the checkout. Update follows that choice. No automatic deploy. |
| 2026-10-07 | **Git-rich onboard.** An existing project can be attached to a remote. A tracked file that differs locally stops the update. The operator keeps that file or takes the remote copy. Untracked files stay. |
| 2026-10-08 | **hailo-frigate-standalone** on rpi5-4 is already a checkout of `main` at tag `v0.18.0`. Remote `main` and tag `0.18.0-5.40` are newer. Local edits are `compose.yaml`, `config/frigate/config.yml.example`, and a deleted `cache/frigate/.gitkeep`. Certs and the live `config.yml` are outside that diff. |
| 2026-10-08 | **Nomad** `/home/bjorn/docker/piherder` is a checkout of `main` at `021e9b5`, level with `origin/main`, one commit after tag `v1.10.0`. No tracked local edits. `.env` and `certs/` are ignored. |
| 2026-10-08 | **Git-rich onboard landed.** Docker → Repository attaches a remote, lists branches and tags, marks a tag ahead of the checkout, and updates only after a local edit is kept or replaced. Untracked files stay. The stack is not deployed. QA stays empty. Package stays `1.10.0`. |
| 2026-10-08 | **Discover moved.** NPM CRUD and remote restore are notes on [PLAN_v1.12.0.md](PLAN_v1.12.0.md). That plan also notes an optional flag for API and MCP read audit, and `host_facts` kept out of the default Jobs and Audit lists. Nothing there is built. |
| 2026-10-08 | **Code freeze.** Notes are [RELEASE_v1.11.0.md](RELEASE_v1.11.0.md). QA boxes stay empty. Screenshot names are `account-passkey-2fa.png`, `host-backup-direct.png`, `jobs-direct-copy.png`, and `docker-repository.png`. Package stays `1.10.0`. |

---

## 3. Next

| # | Step | Status |
|---|------|--------|
| 1 | Open **`v1.11.0-dev`** | **Done** 2026-10-06 |
| 2 | **Passkey versus Force 2FA** | **Landed.** Operator walk still empty in [QA_v1.11.0.md](QA_v1.11.0.md) |
| 3 | **Path C** decision, then the build | **Choice written. Build landed.** Opt in, beside the pull. Operator walk still empty in [QA_v1.11.0.md](QA_v1.11.0.md) |
| 4 | Should, if it fits | **MCP Move**, **MCP nmap**, **discovery devices** ([#34](https://github.com/bjorngluck/piherder/issues/34)). **Landed.** Operator walk still empty in [QA_v1.11.0.md](QA_v1.11.0.md) |
| 5 | Discover write-ups | **Git-rich onboard** landed. Walk still empty. **NPM CRUD** and remote restore moved to [PLAN_v1.12.0.md](PLAN_v1.12.0.md) |
| 6 | Freeze | **Set** 2026-10-08. Package stays **1.10.0**. Version bump, tag, and Hub stay for the ship step |

---

*Opened 2026-10-06. Package stays `1.10.0`. Operator walks live in [QA_v1.11.0.md](QA_v1.11.0.md).*
