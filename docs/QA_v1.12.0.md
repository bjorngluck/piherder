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

The read-audit flag is off unless the operator turns it on. Writes stay audited either way.

- [ ] With the flag off, a `read` call from an API token does not write an audit row
- [ ] With the flag on, that read writes one row. The row has the token and the client IP. It has no secret
- [ ] With the flag on, a read through hosted `/mcp` writes the same kind of row
- [ ] With the flag off, a write from a token still writes an audit row
- [ ] The default Jobs list leaves a successful 15-minute `host_facts` snapshot out
- [ ] The default Audit list leaves that routine snapshot out
- [ ] A failed `host_facts` snapshot stays on both default lists

## Alerts (Must)

- [ ] An expired or rejected Google Drive credential opens one critical alert. Setting the backup category lower does not lower it
- [ ] The same alert, still critical, for OneDrive
- [ ] The same alert, still critical, for a LAN share
- [ ] A later successful copy resolves that alert
- [ ] A failed self-backup alert shows the last successful self-backup time, or "never"

## Docker (Must)

Issue [#47](https://github.com/bjorngluck/piherder/issues/47).

- [ ] After a pull, while the container still runs the old image, the update flag stays set
- [ ] The flag clears once the running container is that image

## Hosts (Must)

Issue [#49](https://github.com/bjorngluck/piherder/issues/49).

- [ ] A detected release outside the small supported list shows a warning next to the OS line
- [ ] That warning does not start an upgrade

## Supply chain and history (Must)

A rewrite of published commits is a separate ask. This walk does not force-push.

- [ ] Environment details that are still easy to edit are gone from the public tree
- [ ] Published commits were not rewritten for this walk

## Backup (Should)

- [ ] The straight-out tick is greyed out when host facts say the host architecture is not the herder's
- [ ] A missed scheduled backup is its own check, not only a stale warning
- [ ] `/metrics` exposes a series for that missed run

## MCP and the adapter (Should)

The adapter cut ships with the herder.

- [ ] An agent can read `effective_backup_schedule`, `next_backup_run`, and `backup_scheduled`
- [ ] The published piherder-mcp readme badges herder 1.11.0

## Docker prune (Should)

Issue [#48](https://github.com/bjorngluck/piherder/issues/48). Volumes stay behind an explicit confirm.

- [ ] The host Docker page can start a prune job
- [ ] The token API can start that job
- [ ] Hosted `/mcp` can start that job
- [ ] Volumes stay until the operator confirms that part

## Scheduler (Should)

- [ ] The capture log names why container update checks stopped for about three days after the 1.11 deploy
- [ ] A test covers that cause
- [ ] The same note explains the host backup that missed one run that week

## Git (Should)

- [ ] Repository refuses a checkout the SSH user does not own

## Nginx Proxy Manager (Should)

Move can still retarget a backend.

- [ ] An operator can create a proxy host
- [ ] An operator can edit a proxy host
- [ ] An operator can delete a proxy host

## Supply-chain pass (Should)

- [ ] The capture log records the pass over pinned actions, the dependency lock, image digest pinning, Dependabot, and secret scanning

## Discover

Not a walk until the row is promoted in [PLAN_v1.12.0.md](PLAN_v1.12.0.md).

- Remote restore from Drive, OneDrive, or a LAN share. Restore still reads `/backups`.
- A direct hop does not wait on a folder hop that is still showing running.

## Later

Not a walk on this train. Issues stay open.

- NIC change, issue [#50](https://github.com/bjorngluck/piherder/issues/50). Device identity stays the MAC.
- Push denylist in CI.
- Backup stagger past about 1,320 hosts.
- Stuck queue alert.
- OAuth token name suffix.
- `get_engine()` accessor.

## 1.11 regression

- [ ] A passkey still counts as 2FA when Force 2FA is on
- [ ] A host that copies straight out still sends files to the ticked destination, and restore of that host still reads `/backups`
- [ ] Jobs still label a direct hop **Direct copy** and a folder hop **Backup copy**
