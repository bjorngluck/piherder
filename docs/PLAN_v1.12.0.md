# PiHerder v1.12.0 — notes for the next train

**Status:** **Notes.** Written 2026-10-08. Branch not cut. **v1.11.0** on `v1.11.0-dev` is still the active train.  
**Package / image version:** **`1.11.0`**. Hub image stays `1.10.0`. This file does not bump a later package.  
**Theme:** two notes, plus the two Discover items moved off v1.11  
**QA:** [QA_v1.12.0.md](QA_v1.12.0.md) (notes — **not** a walk yet)  
**Related:** [PLAN_v1.11.0.md](PLAN_v1.11.0.md) · wiki [Jobs, audit, notifications](../wiki/day-to-day/jobs-audit-notifications.md) · wiki [API tokens](../wiki/operations/api-tokens.md)

> Written while v1.11 is still open. Nothing here is built. The branch `v1.12.0-dev` is not cut.

---

## 1. Decision lock

| Topic | Lock |
|-------|------|
| Note | **Audit API and MCP reads.** An optional flag. Off unless the operator turns it on. When it is on, a `read` call from an API token or from hosted `/mcp` writes an audit row. Writes stay audited either way. The row stores the token and the client IP, and it does not store a secret |
| Note | **Host facts clutter.** The 15-minute `host_facts` snapshot writes a Jobs row and an Audit row for every host. Both views are messy. The default Jobs list and the default Audit list leave that routine snapshot out. A failed snapshot stays visible |
| Discover, moved from v1.11 | **NPM CRUD.** A note only. Proxy hosts stay read-only. Move can still retarget a backend |
| Discover, moved from v1.11 | **Remote restore.** A note only. Restore stays a reverse rsync from `/backups`. Drive, OneDrive, and the NAS are not a restore source |
| Note | **Direct copy on a different CPU.** Grey out the straight-out tick when host facts say the host architecture is not the herder's. Until then the run fails and names the mismatch, and that host keeps the `/backups` path |
| Note | **OS end of life.** Issue [#49](https://github.com/bjorngluck/piherder/issues/49). A warning next to the OS line when the detected release is outside a small supported list. No auto-upgrade |
| Note | **NIC change.** Issue [#50](https://github.com/bjorngluck/piherder/issues/50). A hint when a new MAC appears for a hostname that already has a stale row at that IP. Device identity stays the MAC |
| Note | **Docker prune.** Issue [#48](https://github.com/bjorngluck/piherder/issues/48). A prune job from the host Docker page, the token API, and MCP. Volumes stay behind an explicit confirm. Not built |
| Version bump | Package is `1.11.0` on `v1.11.0-dev`. Hub image stays `1.10.0`. v1.12 stays unopened |
| Demo | Do not point the public demo at a branch that does not exist |

---

## 2. Capture log

| Date | What changed |
|------|----------------|
| 2026-10-08 | **Notes opened.** NPM CRUD and remote restore move here from v1.11. Two new notes: an optional flag for API and MCP read audit, and `host_facts` kept out of the default Jobs and Audit lists. Nothing is built. |
| 2026-10-09 | **Direct copy arch.** Grey out the straight-out tick when the host CPU does not match the herder. Not built. v1.11 names the mismatch in the job and tells the operator to keep `/backups` for that host. |
| 2026-10-10 | **EOL badge and NIC change.** Issues [#49](https://github.com/bjorngluck/piherder/issues/49) and [#50](https://github.com/bjorngluck/piherder/issues/50). Re-filed without environment details. Not built. |
| 2026-10-11 | **Docker prune.** Issue [#48](https://github.com/bjorngluck/piherder/issues/48). Not built. |
| 2026-10-11 | **Candidates.** Listed below. Not tiered. Nothing is built. |

---

## Candidates

Tiers are picked when the train opens. These are not Must or Should. The notes above stay as they are: [#49](https://github.com/bjorngluck/piherder/issues/49) end-of-life OS badge, [#50](https://github.com/bjorngluck/piherder/issues/50) MAC change hint, remote restore, and NPM create, edit, and delete.

- The Git Repository page can read a checkout the SSH user does not own. Refuse that checkout, which closes the filter-driver and credential-helper gap.
- A folder hop left showing running holds that tick's direct hop until the next run. The direct hop should not wait on that row.
- A missed scheduled backup is only a stale warning. Add a direct check that the expected run did not happen, and a Prometheus metric for Grafana.
- [#47](https://github.com/bjorngluck/piherder/issues/47): the image check clears the update flag after a pull while the container still runs the old image. The flag should stay until the running container is that image.
- Hosted MCP and the adapter do not show the effective backup time. Expose `effective_backup_schedule`, `next_backup_run`, and `backup_scheduled` on the next adapter cut.
- The supply-chain baseline is old. Re-check pinned actions, the dependency lock, image digest pinning, Dependabot, and secret scanning.
- A push can still add fleet names, the home subnet, internal domains, or home paths. Fail that push in CI. Keep the denylist in a secret, not in the repo.
- Older environment details are already on main. Sweep them out of the public history that is still easy to edit.
- The backup stagger reuses a minute past about 1,320 hosts on the default 02:00 base. That limit is documented. Revisit it only if a fleet needs more unique minutes.
- The piherder-mcp PyPI readme still badges an older herder. Point that badge at herder 1.11.0 on the next adapter cut.
- Stuck queue: open one alert when any job stays pending past N minutes (default 15) with no worker heartbeat, and resolve when the queue drains.
- Callers import the module-level database engine. Add one get_engine() accessor and use it for every session.

---

## 3. Next

| # | Step | Status |
|---|------|--------|
| 1 | Keep these as notes until v1.11 ships and someone asks to open `v1.12.0-dev` | **Notes only** |
