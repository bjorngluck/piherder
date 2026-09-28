# PiHerder v1.8.0 — operator QA / sign-off

**Branch:** `v1.8.0-dev` → `main` · tag **`v1.8.0`** (cut after merge)  
**Code freeze:** not set  
**Package:** stays **`1.7.0`** until freeze  
**Operator QA:** not started  
**Pull request:** none. Open a draft only when asked

This file is **maintainer-only** (repo `docs/`). It is **not** published on the operator wiki.

Plan: [PLAN_v1.8.0.md](PLAN_v1.8.0.md). 1.7 sign-off stays [QA_v1.7.0.md](QA_v1.7.0.md). Do not re-open those boxes here.

Plugin work is [bjorngluck/piherder-ha](https://github.com/bjorngluck/piherder-ha). It is not inside the PiHerder image. Do not redeploy the public demo onto this branch.

Boxes stay empty until the slice has landed and you walk it. A Should that slips the tag stays unchecked and is noted as slipped. Do not tick a parked row (AC-fg, OneDrive, SMB, Slice 3).

---

## MCP-jobs (Must)

Hosted `POST /mcp` and, if you use it, the stdio adapter. Token masked. Same bearer token as 1.7.

- [ ] `trigger_job` accepts `host_reboot`, `docker_stack_check`, `docker_stack_deploy`, `docker_stack_stop`, `docker_stack_start`, `docker_stack_restart`, `template_deploy`, and `template_redeploy`, plus the six types from 1.7  
- [ ] A stack or template call uses the project path the jobs POST already takes. The job row appears on the herder  
- [ ] Restart host names the machine. It is refused (**409**) while an OS patch, a container patch, or a backup is already running. The agent polls `get_job` and does not start another  
- [ ] `service_migrate`, undo, nmap, and console are still refused by the tool  
- [ ] A token without `jobs` has no `trigger_job`. Feature flags and `feature:*` still gate the call  

## Bak-alt discovery (Must)

Reading pass against [PLAN_v1.8.0.md](PLAN_v1.8.0.md) §3.1. No client required for this box.

- [ ] The write-up keeps the local rsync directory as the default and leaves the Settings DR backup on its own path  
- [ ] Google Drive is the only destination this train may build  
- [ ] OneDrive and a LAN NAS / SMB share are named for a later release and have no client  

## Google Drive (Should)

Skip this section if the destination slips the tag. Demo is not the target.

- [ ] A per-server backup can target Google Drive. The local directory still works for a host that does not  
- [ ] The credential is not shown back in the UI and is not written to the job log  
- [ ] A failed upload fails the job. Audit shows the attempt  
- [ ] The public demo does not upload  

## HA-vis (Must)

Walk on a Home Assistant that has plugin **0.4.0**. Hard-refresh after the Lovelace resource URL changes. Token masked in every screenshot.

- [ ] Host card gauges and stats are readable without opening PiHerder. Confirm actions still match the token (`jobs` / `edit` / feature flags)  
- [ ] Host card shows bars (absolute memory and disk) and one 24-hour sparkline from Home Assistant history of the snapshot sensors  
- [ ] Resources card 24-hour series draws for memory %, disk %, and CPU load. It does not SSH. Empty state only when the recorder has no points  
- [ ] Updates card is one compact row per host and still shows OS and container counts from the stored snapshot  
- [ ] A `read` token shows sensors and no buttons  
- [ ] Fleet card from 0.3.0 still loads  

## HA bus (Should)

- [ ] A finished job the plugin was watching fires `piherder_job_completed` on the HA bus  
- [ ] No new herder route and no webhook  

## Mux-2 (Should)

- [ ] SSH access lists leftover `ph-u*` sessions for that host and can kill one  
- [ ] Hide still detaches. Closing the console still kills that session. There is no automatic reattach  
- [ ] Removing the server does not kill every Unix user’s mux  

## Undo-2 (Should)

- [ ] A Move that dies during `dest_up` offers inspect, then stop dest and start source, without reverting DNS or NPM  
- [ ] A green Move still has no Undo. Dest `down -v` is not offered  

## Jr-web (Should)

- [ ] Recycle **web** while `retention`, `herder_backup`, or `host_facts` is running. The job continues or stays pending. It is not failed because the web process restarted  
- [ ] Recycle the worker while one of those is **running**. The job fails honest and is not resumed mid-flight  
- [ ] `herder_backup` does not take a host exclusive slot  

## 1.7 regression

- [ ] Hosted `POST /mcp` still answers with the same bearer token. The original six job types still enqueue  
- [ ] Exclusive jobs from 1.7 still run on Celery. About / footer still **1.7.0** until the version bump  
- [ ] Move stays off. Console mux stays opt-in and off on HAOS and the demo  
- [ ] Plugin **0.3.0** cards still load until you switch the resource URL to **0.4.0**  
