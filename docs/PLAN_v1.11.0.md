# PiHerder v1.11.0 — a passkey counts as 2FA, then a host copies straight out

**Status:** **Active.** Opened 2026-10-06. Not frozen. Package stays **1.10.0**.  
**Date opened:** 2026-10-06  
**Git branch:** `v1.11.0-dev` → `main` · tag `v1.11.0` after merge, only when asked  
**Package / image version:** stays **`1.10.0`** until freeze. About / footer still say **1.10.0**. Image on Hub stays `1.10.0` / `1.10` / `latest`.  
**Theme:** a passkey counts as 2FA, then a host copies straight to Drive, OneDrive, or the NAS  
**Baseline:** `v1.10.0` (tagged 2026-10-05; Hub digest `sha256:0a1286cb0864e153ea1af6ba8b458175da051c8a54fc98d701539c08dcd99d04`)  
**Mode:** **Must → Should → Discover.** Must **passkey versus Force 2FA** · **Path C discovery** · **Path C build**.  
**QA:** [QA_v1.11.0.md](QA_v1.11.0.md) (maintainer stub — **not** the operator wiki)  
**Related:** [PLAN_v1.10.0.md](PLAN_v1.10.0.md) · [RELEASE_v1.10.0.md](RELEASE_v1.10.0.md) · [FEATURE_PLAN_BACKUP_DESTINATIONS.md](FEATURE_PLAN_BACKUP_DESTINATIONS.md) · wiki [Backups](../wiki/day-to-day/backups.md) · wiki [Agents (MCP)](../wiki/operations/mcp.md)

> Opened 2026-10-06 from `main` after **v1.10.0** shipped. The passkey fix has landed. Path C is opt-in beside the pull. Production stays **v1.10.0**. The public demo is not pointed at this branch.

---

## 0. Intent

v1.10.0 shipped OneDrive beside Google Drive and a LAN share, a copy of the herder self-backup, and browser sign-in for hosted `/mcp`. A passkey now counts as 2FA when Force 2FA is on. A host backup is still a pull onto `/backups` unless that host opts in. Path C was discovered in [PLAN_v1.10.0.md](PLAN_v1.10.0.md) §4.

This train fixes the passkey gate and builds the Path C choice below. An agent may start a Move, and may start or read a LAN Discovery scan, if those fit before the tag.

---

## 1. Decision lock

| Topic | Lock |
|-------|------|
| Must, first | **Passkey versus Force 2FA.** On signup, with 2FA enforced, a passkey cannot be registered. A passkey already in place still forces app-based 2FA. A passkey is not treated as 2FA on the account. The tag waits on this |
| Must | **Path C discovery.** A host can opt in and pick one or more saved destinations. None start selected. Save refuses the opt-in with no destination. The host sends its files straight to those destinations. Nothing of that host lands on `/backups` first, and the host does not store a second copy. Hosts that do not opt in still rsync onto `/backups`, and the herder copy of that mirror stays. Copy now on a direct host starts that destination's push. rclone reads the original files in place for that run, as root when the backup user is not root, using /var/lib/piherder/rclone. The temp config is deleted when the run ends. Input was [PLAN_v1.10.0.md](PLAN_v1.10.0.md) §4 |
| Must | **Path C build.** That choice is on this branch. The tag waits on the operator walk. Restore from Drive, OneDrive, or the NAS stays a Discover note |
| Should | **MCP Move.** An agent can start a Move. The tag can ship if it slips |
| Should | **MCP nmap.** An agent can start or read a LAN Discovery scan. The tag can ship if it slips |
| Discover | **NPM CRUD.** A note only. Move can still retarget a backend |
| Discover | **Remote restore.** A note only. Restore stays a reverse rsync from `/backups` |
| Discover | **Git-rich onboard.** A note only. A stack still comes from a compose file or a template |
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

---

## 3. Next

| # | Step | Status |
|---|------|--------|
| 1 | Open **`v1.11.0-dev`** | **Done** 2026-10-06 |
| 2 | **Passkey versus Force 2FA** | **Landed.** Operator walk still empty in [QA_v1.11.0.md](QA_v1.11.0.md) |
| 3 | **Path C** decision, then the build | **Choice written. Build landed.** Opt in, beside the pull. Operator walk still empty in [QA_v1.11.0.md](QA_v1.11.0.md) |
| 4 | Should, if it fits | **MCP Move**, **MCP nmap** |
| 5 | Discover write-ups | **NPM CRUD**, remote restore, git-rich onboard. Notes only |
| 6 | Freeze · version bump · tag · Hub | Only when asked |

---

*Opened 2026-10-06. Package stays `1.10.0`. Operator walks live in [QA_v1.11.0.md](QA_v1.11.0.md).*
