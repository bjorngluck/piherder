# PiHerder v1.11.0 — operator QA / sign-off

**Branch:** `v1.11.0-dev` → `main` · tag **`v1.11.0`** (cut after merge, only when asked)  
**Code freeze:** not set  
**Package:** stays **`1.10.0`** until the version bump. About / footer say **1.10.0**.  
**Operator QA:** not started  
**Screenshots:** none for this train yet  
**Pull request:** not opened

This file is **maintainer-only** (repo `docs/`). It is **not** published on the operator wiki.

Plan: [PLAN_v1.11.0.md](PLAN_v1.11.0.md). 1.10 sign-off stays [QA_v1.10.0.md](QA_v1.10.0.md). Do not re-open those boxes.

Plugin work is [bjorngluck/piherder-ha](https://github.com/bjorngluck/piherder-ha). It is not inside the PiHerder image. Putting it in the image is not a backlog row. Do not redeploy the public demo onto this branch.

A box stays empty until you walk that slice. A Should that slips the tag stays unchecked and is noted as slipped. Do not tick a Discover row. Do not tick an Out row.

---

## Passkey versus Force 2FA (Must)

Force 2FA is on. A passkey has to count as the second factor. App-based 2FA stays available. It is not required when a passkey is registered.

- [ ] With Force 2FA on, signup can register a passkey, and that passkey satisfies the enroll wall
- [ ] An account that already has a passkey is not sent to set up an authenticator app
- [ ] The account and the Users list treat a passkey as 2FA

## Path C decision (Must)

Opt in, beside the pull. The write-up is in [PLAN_v1.11.0.md](PLAN_v1.11.0.md). These boxes stay empty until you read it.

- [ ] A host can opt in. Hosts that do not opt in still rsync onto `/backups`
- [ ] The operator picks one or more saved destinations. None start selected. Save refuses the opt-in with no destination
- [ ] rclone reads the original files in place. The host does not store a second copy of the tree
- [ ] The least-privilege script allows `/var/lib/piherder/rclone` for that run, and the temp config is deleted when the run ends

## Path C build (Must)

The build is on this branch. These boxes stay empty until you walk a real destination.

- [ ] With no destination saved, the straight-out tick is unavailable
- [ ] With a destination saved, Save refuses the tick when no destination is selected
- [ ] **Backup now** sends the original files to each ticked destination. Nothing new from that host appears under `/backups`
- [ ] The finished job and its audit row name that destination. The sign-in is not in either row
- [ ] **Copy now** on one destination starts that push only
- [ ] Jobs labels that **Copy now** or schedule **Direct copy**. A folder on `/backups` stays **Backup copy**. **Backup now** on the host stays **Backup**
- [ ] A least-privilege host can read root-owned files (MySQL data, `/var/lib/docker/volumes`) after the sudoers script is applied again
- [ ] The original files keep their mode, owner, and modification time
- [ ] A follow-up copy succeeds while a log is still being written. That log is sent at the size it had when the copy started
- [ ] Restore of that host from Drive, OneDrive, or the NAS is not offered. Restore still reads a tree on this PiHerder

## MCP Move (Should)

Hosted `/mcp` `start_move` starts a stop-first Move. `confirm` must be true. The source is left stopped. Undo stays out. `trigger_job` still refuses `service_migrate`.

- [ ] An agent can start a Move through hosted `/mcp`
- [ ] Undo is not a tool, and `trigger_job` still refuses `service_migrate`

## MCP nmap (Should)

Hosted `/mcp` can read a LAN Discovery scan and can start one of the saved ranges. The agent does not choose the ranges. Vulnerability scripts stay off. The console stays off the tool.

- [ ] An agent can read a LAN Discovery scan through hosted `/mcp`
- [ ] An agent can start a scan of the saved ranges through hosted `/mcp`

## Discovery devices (Should)

Issue [#34](https://github.com/bjorngluck/piherder/issues/34). Same actions as the device page. A linked device cannot be purged. A one-device scan stays inside the saved ranges. Vulnerability scripts stay off.

- [ ] An agent can rename a device and mark it known
- [ ] An agent can link a device to a fleet server
- [ ] Purge of a linked device is refused, and purge of offline devices removes only those rows
- [ ] A one-device scan uses an address inside the saved ranges and does not run vulnerability scripts

## Discover (notes, not walks)

- **NPM CRUD.** Creating and editing proxy hosts stays a note. Move can still retarget a backend.
- **Remote restore.** Restore from Drive, SMB, or OneDrive stays a note. Restore stays a reverse rsync from `/backups`.
- **Git-rich onboard.** Bringing a stack in from a git repo stays a note.

## 1.10 regression

Do not retick the 1.10 boxes. This row is the spot check that 1.10 still behaves.

- [ ] OneDrive, the herder self-backup copy, and hosted `/mcp` browser sign-in still work
