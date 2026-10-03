# PiHerder v1.10.0 — operator QA / sign-off

**Branch:** `v1.10.0-dev` → `main` · tag **`v1.10.0`** (cut after merge)  
**Code freeze:** not set  
**Package:** stays **`1.9.0`** until freeze. About / footer **1.9.0**  
**Operator QA:** not started  
**Screenshots:** none yet  
**Pull request:** none. Open a draft only when asked

This file is **maintainer-only** (repo `docs/`). It is **not** published on the operator wiki.

Plan: [PLAN_v1.10.0.md](PLAN_v1.10.0.md). 1.9 sign-off stays [QA_v1.9.0.md](QA_v1.9.0.md). Do not re-open those boxes here. Do not retick SMB **Test** or the password-at-rest check.

Plugin work is [bjorngluck/piherder-ha](https://github.com/bjorngluck/piherder-ha). It is not inside the PiHerder image. Putting it in the image is not a backlog row. Do not redeploy the public demo onto this branch. The demo stays the **1.7.0** image until that Should is asked for.

Boxes stay empty until the slice has landed and you walk it. A Should that slips the tag stays unchecked and is noted as slipped. Do not tick a Discover row (Path C). Do not tick an Out row.

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

- [ ] A destination can receive the self-backup archive
- [ ] A failed copy of that archive does not delete the local DR file
- [ ] The public demo does not upload it

## MCP OAuth (Must)

Hosted `POST /mcp` only. `uvx piherder-mcp` still uses `PIHERDER_TOKEN` and does not open a browser. Token masked when a bearer is still used.

- [ ] An agent can sign in without only a `ph_` bearer token
- [ ] A token without the scope still cannot call the tool
- [ ] Move, undo, nmap, the console, token admin, down, and remove stay refused

## dependabot.yml (Must)

- [ ] `.github/dependabot.yml` is on this branch and turns on Dependabot security updates for this repository
- [ ] The GitHub alerts that match PyJWT **2.15.1** and urllib3 **2.8.0** are closed. The pins are already on `main`

## N3c (Should)

First slice is the **Cards** catalog on `/reports`: the same six history cards, show or hide from that row. Pin, ↑ / ↓, and per-card Hide stay. Out of this slice: new card types, Grafana, PromQL, iframes, SQL, and a Home marketplace. Do not tick the full picker.

- [ ] The write-up names the first slice and leaves the rest of the picker out
- [ ] The first slice is walked on `/reports`. Pin, hide, and reorder still work

## Public demo (Should)

- [ ] [piherder-demo.hacknow.info](https://piherder-demo.hacknow.info) runs the latest release image. Until this is asked for, it stays **1.7.0**

## Sibling repos (Should)

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
