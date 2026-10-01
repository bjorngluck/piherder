# PiHerder v1.9.0 — operator QA / sign-off

**Branch:** `v1.9.0-dev` → `main` · tag **`v1.9.0`** (cut after merge)  
**Code freeze:** not set  
**Package:** stays **`1.8.0`** until freeze  
**Operator QA:** not started  
**Pull request:** none. Open a draft only when asked

This file is **maintainer-only** (repo `docs/`). It is **not** published on the operator wiki.

Plan: [PLAN_v1.9.0.md](PLAN_v1.9.0.md). 1.8 sign-off stays [QA_v1.8.0.md](QA_v1.8.0.md). Do not re-open those boxes here.

The Home Assistant plugin stays [bjorngluck/piherder-ha](https://github.com/bjorngluck/piherder-ha). It is not inside the PiHerder image. Do not redeploy the public demo onto this branch.

Boxes stay empty until the slice has landed and you walk it. A Should that slips the tag stays unchecked and is noted as slipped. Do not tick an Out row (AC-fg, Brand-3, ACME, NPM CRUD, a richer Files API, N3c, M-live). Supply-chain boxes stay empty until the locks have changed.

---

## MCP-svc (Must)

Hosted `POST /mcp` and the stdio adapter. Token masked. All four types, in the same train. Do not tick this from the Home Assistant card. The card already sends these jobs.

- [ ] `trigger_job` accepts `container_start`, `container_stop`, `container_restart`, and `container_redeploy`
- [ ] `source_filter` is the compose directory and `service` is required. The rest of the project stays up
- [ ] Hosted `MCP_JOB_TYPES` and piherder-mcp `JOB_TYPES` match. The adapter tag is cut in the same turn the herder accepts the types. Tagging the adapter first is not a pass
- [ ] `docker_stack_down`, `docker_stack_remove`, undo, nmap, the console, and token admin are still refused
- [ ] A token without `jobs` has no `trigger_job`

## LAN NAS / SMB (Must)

Settings → PiHerder backup. The local rsync directory stays the default. Demo is not the target.

- [ ] A LAN share can be selected and saved. Google Drive can still be selected
- [ ] **Test** checks the share and does not copy the tree
- [ ] Copy now, the schedule, or the follow-up after a host backup copies to that share. A failed copy does not change the host backup time
- [ ] The credential is not written to the job log

## Move default-on (Must)

Leave this unchecked while `PIHERDER_SERVICE_MIGRATE` is still false.

- [ ] A new install has Move available without setting the flag
- [ ] An install that sets the flag to false still hides Move
- [ ] The public demo is still the **1.7.0** image, or its compose still forces the flag off
- [ ] A finished Move still has no Undo. The copy is still stop-first

## OneDrive (Should)

Skip this section if OneDrive slips the tag.

- [ ] OneDrive can be selected. The copy is the same rclone hop as Google Drive
- [ ] A failed OneDrive copy fails the copy job, not the host rsync job
- [ ] The client secret is not shown again after save

## HA Slice 3 (Should)

Skip this section if the card leftovers slip the tag. One-service start, stop, restart, and update already shipped. Do not retick those.

- [ ] The card can call a webhook the operator configured
- [ ] Move can be started from the card. The herder flag still gates it
- [ ] Files on the card stay inside the fleet jail
- [ ] Stop of a whole compose project asks first and is not `docker compose down`

## CSP Slice 2 (Should)

Skip this section if the rewrite slips the tag.

- [ ] Inline `onclick` handlers that this slice rewrote no longer need the inline-event allowance
- [ ] A click that worked before the rewrite still works. Console, Move, and Settings included

## Path C (Discover)

Write-up only. No client on the hosts.

- [ ] The note says what each host would need in order to write straight to Drive, OneDrive, or the NAS, and why `/backups` stays the default until a later promotion

## MCP OAuth (Discover)

Write-up only. Bearer stays the only path.

- [ ] The note says how an agent would sign in to `POST /mcp` and what stays on the `ph_` token until a later promotion

## Supply-chain locks (Must)

Planned. Not started. Baseline on 2026-10-01 is PyJWT **2.13.0** and urllib3 **2.7.0**. Minimum fixed versions are PyJWT **2.15.0** and urllib3 **2.8.0**.

- [ ] `uv.lock`, `requirements.lock.txt`, and `requirements.runtime.lock.txt` resolve PyJWT >= 2.15.0 and urllib3 >= 2.8.0
- [ ] The unit suite passes on those locks. Fail-under stays **80**. `pip-audit` is clean for these two packages
- [ ] Matching Dependabot alerts on this repository are closed after the locks reach the default branch
- [ ] The **v1.9.0** tag carries the bump, or an earlier **v1.8.x** patch is already on this branch

## 1.8 regression

- [ ] About / footer still **1.8.0** until the version bump
- [ ] Google Drive copy still runs. OneDrive stays unselectable until that Should lands
- [ ] Hosted `trigger_job` accepts the four one-service types. Published adapter **0.2.0** does not. The MCP-svc boxes above stay empty until the adapter tag matches and the walk is done
- [ ] Until Move default-on lands, the flag still defaults to false
- [ ] Plugin install is still the separate HACS repo, not a file in this image
