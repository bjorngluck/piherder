# PiHerder v1.9.0 — operator QA / sign-off

**Branch:** `v1.9.0-dev` → `main` · tag **`v1.9.0`** (cut after merge)  
**Code freeze:** not set  
**Package:** stays **`1.8.0`** until freeze  
**Operator QA:** not started  
**Pull request:** none. Open a draft only when asked

This file is **maintainer-only** (repo `docs/`). It is **not** published on the operator wiki.

Plan: [PLAN_v1.9.0.md](PLAN_v1.9.0.md). 1.8 sign-off stays [QA_v1.8.0.md](QA_v1.8.0.md). Do not re-open those boxes here.

The Home Assistant plugin stays [bjorngluck/piherder-ha](https://github.com/bjorngluck/piherder-ha). It is not inside the PiHerder image. Do not redeploy the public demo onto this branch.

Boxes stay empty until you walk the slice. Landed code is not a signed walk. OneDrive stays unselectable; do not tick it. Do not tick an Out row (AC-fg, Brand-3, ACME, NPM CRUD, a richer Files API, N3c, M-live). Do not tick a version bump: herder stays **1.8.0** and the published adapter stays **0.2.0** until freeze. Supply-chain boxes stay empty until the locks are confirmed on `main`.

---

## MCP-svc (Must)

Hosted `POST /mcp` and the stdio adapter. Token masked. All four types, in the same train. Do not tick this from the Home Assistant card. The card already sends these jobs.

- [ ] `trigger_job` accepts `container_start`, `container_stop`, `container_restart`, and `container_redeploy`
- [ ] `source_filter` is the compose directory and `service` is required. The rest of the project stays up
- [ ] Hosted `MCP_JOB_TYPES` and piherder-mcp `JOB_TYPES` match. The adapter tag is cut in the same turn the herder accepts the types. Tagging the adapter first is not a pass
- [ ] `docker_stack_down`, `docker_stack_remove`, undo, nmap, the console, and token admin are still refused
- [ ] A token without `jobs` has no `trigger_job`

## LAN NAS / SMB (Must)

Settings → PiHerder backup. The local rsync directory stays the default. This walk is path A (rclone `smb` from `/backups`). A host CIFS mount as the dest root is not this slice. Demo is not the target.

- [ ] A LAN share can be selected and saved. Google Drive can still be selected. Both can be live at once (two hops). Removing one does not clear the other
- [ ] A share with no login saves when username and password are both empty. A blank password on an existing account keeps the saved password. One field filled and the other empty is refused
- [ ] A failed Save stays on the edit form with what was typed. Each saved destination has its own Edit, Remove, and folders row
- [ ] **Test** checks the share and does not copy the tree
- [ ] Copy now, the schedule, or the follow-up after a host backup copies to that share. A failed copy does not change the host backup time
- [ ] The credential is not written to the job log

## Move default-on (Must)

Unset env enables Move. `PIHERDER_SERVICE_MIGRATE=false` still hides it. Operator+ in the UI. The card and `POST /moves` also start a Move when the flag is on. Not an MCP tool. `POST /jobs` with `service_migrate` stays refused.

- [ ] A new install has Move available without setting the flag
- [ ] An install that sets the flag to false still hides Move, including the card (`service_migrate` on health is false; `POST /moves` is **404**)
- [ ] The public demo is still the **1.7.0** image, or its compose still forces the flag off
- [ ] A finished Move still has no Undo. The copy is still stop-first
- [ ] The Docker wizard, the Home Assistant card, and `POST /api/v1/servers/{id}/moves` with `confirm: true` can each start one. The card and that route leave the source stopped
- [ ] `POST /jobs` with `service_migrate` is **400**. Hosted MCP has no Move tool

## Remove one backup dest (#25)

Settings → PiHerder backup. Admin. `confirm=remove`. Not a job.

- [ ] Remove Google Drive deletes that row and its Fernet secret. A saved SMB share is still there. Remote Drive files are still in Drive
- [ ] Remove LAN NAS / SMB deletes that row and its Fernet secret. A saved Drive destination is still there. Remote share files are still on the share
- [ ] The public demo refuses the wipe
- [ ] A token and MCP cannot call this. There is no job type for it

## OneDrive (discovery only)

Do not tick these. OneDrive is not on this train.

- [ ] OneDrive stays listed and cannot be selected
- [ ] Saving Drive or SMB does not turn OneDrive into a destination

## HA Slice 3 (landed, plugin 0.5.0)

Poll-only. There is no herder webhook to walk. One-service start, stop, restart, and update already shipped. Do not retick those. Files are the fleet jail, not an HAOS `/config` install.

- [ ] The card does not register or call a herder webhook. Job-finished events still come from the plugin poll
- [ ] Move can be started from the card after a confirm. The herder flag still gates it. A finished Move has no Undo on the card
- [ ] Files on the card stay inside the fleet jail. Delete asks first and removes one file or an empty directory
- [ ] Stop project asks first and runs `docker compose stop`. It does not remove containers or volumes

## CSP Slice 2 (Should)

The rewrite is on this branch (`script-src-attr 'none'`, `data-ph-*` listeners, Slice 1 nonces kept). These boxes are the live walk.

- [ ] Inline `onclick` handlers that this slice rewrote no longer need the inline-event allowance
- [ ] A click that worked before the rewrite still works. Console, Move, and Settings included

## Path C (Discover)

Write-up only. No client on the hosts.

- [ ] The note says what each host would need in order to write straight to Drive, OneDrive, or the NAS, and why `/backups` stays the default until a later promotion

## MCP OAuth (Discover)

Write-up only. Bearer stays the only path.

- [ ] The note says how an agent would sign in to `POST /mcp` and what stays on the `ph_` token until a later promotion

## Supply-chain locks (Must)

Locks on this branch pin PyJWT **2.15.1** and urllib3 **2.8.0**. Tick these after you confirm the files and after the pins are on `main`. Dependabot alerts stay open until then.

- [ ] `uv.lock`, `requirements.lock.txt`, and `requirements.runtime.lock.txt` resolve PyJWT >= 2.15.0 and urllib3 >= 2.8.0
- [ ] The unit suite passes on those locks. Fail-under stays **80**. `pip-audit` is clean for these two packages
- [ ] Matching Dependabot alerts on this repository are closed after the locks reach the default branch
- [ ] The **v1.9.0** tag carries the bump, or an earlier **v1.8.x** patch is already on this branch

## 1.8 regression

- [ ] About / footer still **1.8.0** until the version bump
- [ ] Google Drive copy still runs. OneDrive stays unselectable. A LAN share can be saved beside Drive
- [ ] Hosted `trigger_job` accepts the four one-service types. Published adapter **0.2.0** does not. The MCP-svc boxes above stay empty until the adapter tag matches and the walk is done
- [ ] Unset `PIHERDER_SERVICE_MIGRATE` leaves Move on. Explicit `false` still hides it
- [ ] Plugin install is still the separate HACS repo, not a file in this image
