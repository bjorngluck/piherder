# PiHerder v1.10.0 — operator QA / sign-off

**Branch:** `v1.10.0-dev` → `main` · tag **`v1.10.0`** (cut after merge)  
**Code freeze:** not set  
**Package:** stays **`1.9.0`** until freeze. About / footer **1.9.0**  
**Operator QA:** not signed. Every box below stays empty until you walk it.  
**Screenshots:** not captured. Shoot list: [v1.10 pack](../wiki/assets/screenshots/README.md#v110--pack-status).  
**Pull request:** [#30](https://github.com/bjorngluck/piherder/pull/30) is open and not a draft. It is not ready to merge. QA boxes stay empty.

This file is **maintainer-only** (repo `docs/`). It is **not** published on the operator wiki.

Plan: [PLAN_v1.10.0.md](PLAN_v1.10.0.md). 1.9 sign-off stays [QA_v1.9.0.md](QA_v1.9.0.md). Do not re-open those boxes here. Do not retick SMB **Test** or the password-at-rest check.

Plugin work is [bjorngluck/piherder-ha](https://github.com/bjorngluck/piherder-ha). It is not inside the PiHerder image. Putting it in the image is not a backlog row. Do not redeploy the public demo onto this branch. The demo reports **1.9.0** from `main`.

Boxes stay empty until the slice has landed and you walk it. A Should that slips the tag stays unchecked and is noted as slipped. Do not tick a Discover row. Path C is not a walk on this train. The decision is **v1.11.0**. Do not tick an Out row.

---

## Copy now (Must)

Settings → PiHerder backup → **Copy the backup drive**. A separate NAS is required. This walk is the copy, not **Test**. Drive and SMB are both in the walk. Demo is not the target.

- [ ] **Copy now** on the SMB row copies the ticked folders onto the share and does not change the local rsync job
- [ ] **Copy now** on the Google Drive row still copies the ticked folders
- [ ] A saved schedule runs the copy. Removing the destination removes that schedule
- [ ] The follow-up after one host backup copies only the checked paths under that host folder
- [ ] A failed copy fails that copy job. `last_backup_at` on the host backup stays as it was
- [ ] The SMB password and the Drive token stay out of the job log

## OneDrive (Must)

Same card. OneDrive can be selected. It is its own row.

- [ ] Saving OneDrive leaves the Drive row and the SMB row in place
- [ ] The secret is Fernet. It is not written to the job log
- [ ] **Test** checks the folder and does not copy
- [ ] **Copy now** copies the ticked folders. The schedule and the host-backup follow-up can use this row
- [ ] **Remove** deletes that row only. Files already in OneDrive stay there

## Copy the herder self-backup (Must)

The DR archive (`/herder_backups`), not the host rsync tree.

- [ ] **Also copy each new self-backup** is on the Drive, OneDrive, and SMB forms. It is off until checked
- [ ] After a successful self-backup, only a destination with that box and saved credentials is queued
- [ ] **Copy off this host** on an archive row sends that existing file. The local `.tar.gz` stays
- [ ] A running self-backup copy does not stop **Copy now** on that destination. A running host-folder copy does not stop **Copy off this host**. Jobs shows **Self-backup copy** for the archive and **Backup copy** for the folders
- [ ] A failed copy of that archive does not delete the local DR file and does not fail the local self-backup
- [ ] The public demo does not upload it

## Self-backup left pending (landed with the DR copy)

One self-backup at a time. A row that stays **pending** is not a running archive.

- [ ] A `herder_backup` still **pending** after 30 minutes is marked failed
- [ ] That raises the critical **PiHerder self-backup failed** alert, linked to the job
- [ ] A **running** self-backup is not failed by that watch
- [ ] The next **Run backup** can start after the pending row is failed

## MCP OAuth (Must)

Hosted `POST /mcp` only. `uvx piherder-mcp` still uses `PIHERDER_TOKEN` and does not open a browser. Token masked when a bearer is still used.

- [ ] An agent can sign in without only a `ph_` bearer token
- [ ] A token without the scope still cannot call the tool
- [ ] Move, undo, nmap, the console, token admin, down, and remove stay refused

## dependabot.yml (Must)

The file is on this branch. Security updates are enabled on the GitHub repo. Version-update pull requests stay off. Checked 2026-10-03: PyJWT **2.15.1** and urllib3 **2.8.0** are in `uv.lock`, and GitHub showed **0** open Dependabot alerts. The boxes stay empty until you sign them.

- [ ] `.github/dependabot.yml` is on this branch and turns on Dependabot security updates for this repository
- [ ] The GitHub alerts that match PyJWT **2.15.1** and urllib3 **2.8.0** are closed. The pins are already on `main`

## N3c (Should)

First slice is the **Cards** catalog on `/reports`: the same six history cards, show or hide from that row. Pin, ↑ / ↓, and per-card Hide stay. Out of this slice: new card types, Grafana, PromQL, iframes, SQL, and a Home marketplace. Do not tick the full picker.

- [ ] The write-up names the first slice and leaves the rest of the picker out
- [ ] The first slice is walked on `/reports`. Pin, hide, and reorder still work

## Public demo (Should)

- [ ] [piherder-demo.hacknow.info](https://piherder-demo.hacknow.info) runs the latest release. Checked 2026-10-03: the container reports **1.9.0** from `main` `cc950c6`. Not this branch. Walk still unsigned.

## Sibling repos (Should)

Checked on those repos, not in this tree: `piherder-ha` `4082131`, `piherder-mcp` `ccd80f0`. Each `main` asks for one review plus the CI that already runs. The box stays empty until you sign it.

- [ ] [piherder-ha](https://github.com/bjorngluck/piherder-ha) and [piherder-mcp](https://github.com/bjorngluck/piherder-mcp) have Dependabot, a `SECURITY.md`, and `main` branch protection

## Template fleet

Pulled in from Discover. The template page, not a new client.

- [ ] A template page lists the hosts and stacks recorded from that template
- [ ] A stack from a different template is not on that list
- [ ] The catalog card shows how many stacks use the template

## 1.9 regression

- [ ] About / footer still **1.9.0** until the version bump
- [ ] Move stays on unless `PIHERDER_SERVICE_MIGRATE=false`
- [ ] A LAN share and Google Drive can both be saved. **Test** still does not copy
- [ ] Hosted `POST /mcp` still accepts the four one-service jobs and still refuses Move
