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

The write-up is in [PLAN_v1.11.0.md](PLAN_v1.11.0.md). Input is [PLAN_v1.10.0.md](PLAN_v1.10.0.md) §4. No shape is chosen at open.

- [ ] The plan names whether a host copies straight out, whether that replaces **Backups** or sits beside the pull, how rclone gets onto the host, and whether `/backups` stays for some hosts

## Path C build (Must)

Build the choice from the decision. There is no walk until that choice is written.

- [ ] The chosen copy is walked on a real destination

## MCP Move (Should)

Hosted `/mcp` can start a Move. Undo of a finished Move stays out. MCP undo stays out.

- [ ] An agent can start a Move through hosted `/mcp`

## MCP nmap (Should)

Hosted `/mcp` can start or read a LAN Discovery scan. The console stays off the tool.

- [ ] An agent can start or read a LAN Discovery scan through hosted `/mcp`

## Discover (notes, not walks)

- **NPM CRUD.** Creating and editing proxy hosts stays a note. Move can still retarget a backend.
- **Remote restore.** Restore from Drive, SMB, or OneDrive stays a note. Restore stays a reverse rsync from `/backups`.
- **Git-rich onboard.** Bringing a stack in from a git repo stays a note.

## 1.10 regression

Do not retick the 1.10 boxes. This row is the spot check that 1.10 still behaves.

- [ ] OneDrive, the herder self-backup copy, and hosted `/mcp` browser sign-in still work
