# PiHerder v1.10.0 — NAS copy, OneDrive, agent sign-in

**Status:** **Released** 2026-10-05. Package **1.10.0**. Tag **v1.10.0**. Image `1.10.0` / `1.10` / `latest`.  
**Date opened:** 2026-10-02  
**Git branch:** `v1.10.0-dev` → `main` · tag `v1.10.0` after merge  
**Package / image version:** **`1.10.0`**. Image tags `1.10.0` / `1.10` / `latest`. Manifest `sha256:0a1286cb0864e153ea1af6ba8b458175da051c8a54fc98d701539c08dcd99d04`. Pins `1.9.0` / `1.9` stay valid.  
**Theme:** walk the NAS copy, make OneDrive selectable, then agent sign-in and the DR archive copy  
**Baseline:** `v1.9.0` (tagged 2026-10-02; Hub digest `sha256:8519ad53e7d0ad0636164966bb876c4774f76b10101bb3c1a74dc6b2d0945472`)  
**Mode:** **Must → Should → Discover.** Must **Copy now** · **OneDrive** · **MCP OAuth** · **DR copy** · **dependabot.yml** · close the matching Dependabot alerts.  
**QA:** [QA_v1.10.0.md](QA_v1.10.0.md) (maintainer stub — **not** the operator wiki)  
**Related:** [PLAN_v1.9.0.md](PLAN_v1.9.0.md) · [RELEASE_v1.9.0.md](RELEASE_v1.9.0.md) · [FEATURE_PLAN_BACKUP_DESTINATIONS.md](FEATURE_PLAN_BACKUP_DESTINATIONS.md) · wiki [Backups](../wiki/day-to-day/backups.md) · wiki [Agents (MCP)](../wiki/operations/mcp.md)

> **Released 2026-10-05.** Tag **v1.10.0**. Package **1.10.0**. Hub `1.10.0` / `1.10` / `latest`. The public demo still reports **1.9.0** from the earlier `main` build.

---

## 0. Intent

**At open (2026-10-02).** v1.9.0 had shipped a LAN share beside Google Drive, Move on by default, plugin **0.5.0**, and one-service jobs on hosted MCP. **Copy now**, the schedule, and the follow-up after a host backup were already in that release. The dedicated-NAS walk was not done. OneDrive was listed and could not be selected. An agent signed in with only a `ph_` bearer token. The herder self-backup stayed on its own local path.

**On this branch now.** OneDrive is selectable. A saved destination can copy the self-backup. Hosted `POST /mcp` can use browser sign-in. Dependabot security updates are configured. `/reports` has the six-card catalog. A template page lists its fleet. The public demo reports **1.9.0**. Operator QA is signed 2026-10-04, including **MCP OAuth**. Screenshots are wired. Package is **1.10.0**. Tag **v1.10.0** is cut. Image is published.

**Must:**

1. **Copy now.** Walk on-demand, the schedule, and the follow-up after a host backup on a dedicated NAS. That covers the SMB share and the existing Google Drive hop. Fix what the walk breaks. SMB **Test** and the password-at-rest check stay signed on 1.9 and are not reopened. **Signed 2026-10-04.**
2. **OneDrive.** Same card, selectable rclone hop, its own row, Fernet secret, then the same copy test. **Signed 2026-10-04.**
3. **MCP OAuth.** An agent can sign in to `POST /mcp` without only a `ph_` bearer token. **Signed 2026-10-04.** Cursor shows one HTTP server per URL, so the walk used one entry and no `Authorization` header. Tools listed. `health` answered.
4. **DR copy.** The herder self-backup archive can go to Drive, the NAS, or OneDrive. It is not only the separate local path. **Signed 2026-10-04**, including the 30-minute pending watch that landed with this copy.
5. **dependabot.yml.** Dependabot security updates for this repository. **Signed 2026-10-04.**
6. **Close Dependabot alerts.** Close the alerts that match PyJWT **2.15.1** and urllib3 **2.8.0**. Those pins are already on `main`. **Signed 2026-10-04.**

**Should. The tag can ship if one slips. Landed on this branch:**

- **N3c.** The first slice is the **Cards** row on `/reports`: the same six history cards. Pin, ↑ / ↓, and per-card Hide stay. Not the full picker. Not Grafana. **Signed 2026-10-04.**
- **Public demo.** [piherder-demo.hacknow.info](https://piherder-demo.hacknow.info) reports **1.9.0**. That is a local build of `main`, not this branch. **Signed 2026-10-04.**
- **Sibling repos.** Dependabot, `SECURITY.md`, and `main` branch protection are on [piherder-ha](https://github.com/bjorngluck/piherder-ha) and [piherder-mcp](https://github.com/bjorngluck/piherder-mcp). No file in this repository for those repos. **Signed 2026-10-04.**

**Discover. A note only. No client and no page:**

- **Path C.** Discovery is written below. No build on this train. The decision and the build are Must on [PLAN_v1.11.0.md](PLAN_v1.11.0.md) (`v1.11.0-dev`). No shape is chosen there yet.
- **Template fleet** was pulled onto this train. The template page lists the hosts and stacks. It is not a Discover row anymore. **Signed 2026-10-04.**

**Out.** Path B (CIFS mount as the dest root). AC-fg. Brand-3. ACME-in-herder. NPM CRUD. A richer Files token API. M-live. A herder→Home Assistant webhook. Undo of a finished Move. MCP Move, undo, nmap, the console, token admin, `docker_stack_down`, and `docker_stack_remove`. restic, borg, kopia, and rclone crypt. Restore from Drive, SMB, or OneDrive. SMB Kerberos. Selectable hero stats. A templates catalog redesign. Git-rich onboard. Optional AI. Ansible / cloud-init. Discord / Discussions. Swarm / Kubernetes. A higher coverage fail-under (stays **80**). The console mobile Tab issue. Stricter command-audit redaction. CodeQL. Actions pinned to commit SHAs. `CODEOWNERS`.

The Home Assistant card stays the separate HACS repo. Putting the plugin inside this image is not a requirement and is not a backlog row.

---

## 1. Decision lock (train open)

| Choice | Value |
|--------|--------|
| Integration branch | **`v1.10.0-dev`** |
| Production line | **`main`** still serves image **1.9.0** until this branch merges and the image is published |
| Git tag | **`v1.10.0`** after merge. Not cut |
| Image tags (freeze) | `1.10.0` · `1.10` · `latest` (multi-arch); keep `1.9` / `1.9.x` pins valid |
| Must | **Copy now** · **OneDrive** · **MCP OAuth** · **DR copy** · **dependabot.yml** · close matching Dependabot alerts |
| Should (may slip) | **N3c** first slice · public demo on the latest release image · sibling-repo Dependabot and branch protection |
| Discover (no code) | **Path C** |
| Version bump | **Done** 2026-10-05. Package **1.10.0**. Tag **v1.10.0** |
| Demo | Reports **1.9.0** from `main` `cc950c6`. Do not point it at this branch |
| Adapter / plugin | Stay **0.3.1** and **0.5.0** until a slice needs a new tag |
| Coverage | Fail-under stays **80**. Do not lower it or raise it |
| Schema | None at open. A new Alembic revision only if a Must needs one |

```text
main (image 1.9.0)
  └─ v1.10.0-dev → merge → tag v1.10.0 → Hub
```

| Rule | Practice |
|------|----------|
| Must → then freeze | **Released** 2026-10-05. Operator QA signed 2026-10-04. Screenshots wired. Tag **v1.10.0**. Do not start an Out item |
| OneDrive does not stay listed-only | It is Must on this train. Do not describe it as shipped before the walk |
| Discover is a write-up | Path C stays a note. Template fleet is the host and stack list on the template page |
| Demo | Do not point the public demo at this branch as part of opening the train |

---

## 2. Capture log

| Date | Note |
|------|------|
| 2026-10-02 | Train opened from `main` after **v1.9.0** shipped. Must is the NAS **Copy now** walk, a selectable OneDrive hop, MCP OAuth, a copy of the herder self-backup, `dependabot.yml`, and closing the matching Dependabot alerts. Package stays `1.9.0`. |
| 2026-10-02 | **OneDrive.** Selectable rclone hop on the same card. Own row, Fernet client secret and refresh token. Default drive of the signed-in account. Not a version bump. The NAS **Copy now** walk is still open. |
| 2026-10-03 | **DR copy.** A saved destination can copy the herder self-backup when **Also copy each new self-backup** is on. The file goes to `herder/` on Drive, OneDrive, or the NAS. A failed copy leaves the local archive. Not a version bump. The NAS **Copy now** walk is still open. |
| 2026-10-03 | A self-backup that is still **pending** after **30 minutes** is failed. That raises the critical **PiHerder self-backup failed** alert and lets the next run start. A running archive is not timed out this way. |
| 2026-10-03 | **MCP OAuth.** An agent can sign in to `POST /mcp` in the browser (PKCE, admin consent). A pasted `ph_` token still works. The access token is an API token with the approved scopes. Move, undo, nmap, the console, token admin, down, and remove stay off the tool. `uvx piherder-mcp` does not use this sign-in. It still calls `/api/v1` with `PIHERDER_TOKEN`. Not a version bump. |
| 2026-10-03 | **dependabot.yml.** Security updates for uv, pip, GitHub Actions, Docker, and Compose. Version-update pull requests stay off. The PyJWT **2.15.1** and urllib3 **2.8.0** alerts are already **fixed** on GitHub (0 open). Not a version bump. |
| 2026-10-03 | **N3c first slice.** `/reports` has a **Cards** catalog of the six history cards. Show and hide from that row. Pin, ↑ / ↓, and the per-card Hide stay. Out of this slice: new card types, Grafana, PromQL, iframes, SQL, and a Home marketplace. Not a version bump. |
| 2026-10-03 | **Public demo.** [piherder-demo.hacknow.info](https://piherder-demo.hacknow.info) already reports **1.9.0**. The 05:15 UTC cron rebuilt `piherder:demo` from `main` `cc950c6`. Login returns 200. Not a Hub pull, and not this dev branch. |
| 2026-10-03 | **Sibling repos.** [piherder-ha](https://github.com/bjorngluck/piherder-ha) `4082131` and [piherder-mcp](https://github.com/bjorngluck/piherder-mcp) `ccd80f0` add `dependabot.yml` and `SECURITY.md`. Security updates are on. `main` requires one review plus the existing CI checks. No file in this repository for those repos. |
| 2026-10-03 | **Template fleet.** A template page lists every host and stack recorded from it. The catalog card shows the stack count. The badge on one Docker stack stays that host only. Not a version bump. |
| 2026-10-03 | **Doc sweep.** Living plan, QA header, wiki index, upgrade note, and admin backup paragraph match the branch. Path C is written as a note in §4. It is not built. |
| 2026-10-03 | **Path C discovery.** How a host backup works today, how Drive / OneDrive / SMB sit on that mirror, and what a direct copy would need on the host. No decision. Decision and any build are **v1.11.0**. Not this train. |
| 2026-10-03 | **QA and screenshot list.** [QA_v1.10.0.md](QA_v1.10.0.md) names the unsigned walks, including the 30-minute pending self-backup. [v1.10 pack](../wiki/assets/screenshots/README.md#v110--pack-status) is the shoot list. Nothing is captured. Nothing is ticked. |
| 2026-10-03 | **Copy hops.** A self-backup copy and **Copy now** on the same destination no longer share one active slot. Jobs labels the archive hop **Self-backup copy**. OAuth audience is [#31](https://github.com/bjorngluck/piherder/issues/31). OneDrive Graph scopes are [#32](https://github.com/bjorngluck/piherder/issues/32). Neither is changed here. |
| 2026-10-03 | **Audience and OneDrive scopes.** A `ph_oa_` token is accepted on `POST /mcp` and rejected on `/api/v1`. OneDrive consent is `User.Read`, `Files.ReadWrite`, and `offline_access`. An already connected account keeps the previous grant until **Connect Microsoft** is used again. |
| 2026-10-04 | **QA.** **Copy now** (SMB and Google Drive), **Copy the herder self-backup**, and **Self-backup left pending** are signed. OneDrive, MCP OAuth, Dependabot, the Should rows, template fleet, and the 1.9 regression stay empty. Screenshots stay uncaptured. |
| 2026-10-04 | **QA.** **dependabot.yml**, **N3c**, the public demo, the sibling repos, template fleet, and the 1.9 regression are signed. **OneDrive** and **MCP OAuth** stay empty. Screenshots stay uncaptured. |
| 2026-10-04 | **QA.** **OneDrive** is signed, including **Test**, **Copy now**, the schedule, and the host-backup follow-up. rclone receives the default drive id. **MCP OAuth** stays empty. Screenshots stay uncaptured. |
| 2026-10-04 | **QA.** **MCP OAuth** is signed in Cursor. One server entry, no pasted bearer, because Cursor keeps one HTTP server per URL. Tools listed. `health` answered. Operator QA is complete. Screenshots stay uncaptured. |
| 2026-10-04 | **Release notes drafted** in [RELEASE_v1.10.0.md](RELEASE_v1.10.0.md). Tag not cut. Package stays **1.9.0**. Screenshots stay uncaptured. |
| 2026-10-05 | **Code freeze.** Screenshot pack wired. Package **1.10.0**. Wiki banner and release badge point at **1.10.0**. Tag not cut. Image not published. |
| 2026-10-05 | **Released.** Tag **v1.10.0**. Hub `1.10.0` / `1.10` / `latest`, manifest `sha256:0a1286cb0864e153ea1af6ba8b458175da051c8a54fc98d701539c08dcd99d04`. Pin `1.9.0` left in place. |
| 2026-10-06 | **v1.11.0 train opened** on `v1.11.0-dev`. [PLAN_v1.11.0.md](PLAN_v1.11.0.md). |

---

## 3. Next

| # | Step | Status |
|---|------|--------|
| 1 | Open **`v1.10.0-dev`** | **Done** 2026-10-02 |
| 2 | **Copy now** on a dedicated NAS, then **OneDrive**, then the **DR copy** | **Copy now**, **OneDrive**, the herder self-backup copy, and the 30-minute pending watch are signed 2026-10-04 |
| 3 | **MCP OAuth** | **Signed 2026-10-04.** Browser sign-in for `POST /mcp`. A pasted `ph_` token still works. Cursor shows one server per URL |
| 4 | **dependabot.yml** and close the matching alerts | **Signed 2026-10-04.** The file applies on `main` after merge |
| 5 | Should, if it fits | **N3c**, the public demo, and the sibling repos are signed 2026-10-04 |
| 6 | Discover write-ups | **Path C** discovery is in §4. Decision and any build are **v1.11.0**. Template fleet is signed 2026-10-04 |
| 7 | Freeze · version bump · tag · Hub | Only when asked |

---

## 4. Path C discovery — a host copies straight out

**Parked for v1.11.0.** This section is the discovery. It does not choose a design. It does not build one. v1.11.0 is opened on `v1.11.0-dev` ([PLAN_v1.11.0.md](PLAN_v1.11.0.md)). The NAS **Copy now** walk on this train stays the pull onto `/backups`, then the herder copy.

### What is true today

Enabling **Backups** on a server does not send that host to Drive, OneDrive, or a NAS. It lets the herder **pull**.

1. The operator turns **Backups** on and adds source paths that exist on that host.
2. A manual run, the host cron, or a bulk Backup starts one job. The herder opens SSH and **rsyncs** those paths into `/backups/{host}/{dest}` on the herder.
3. The host needs an SSH login and `rsync`. A least-privilege user runs `sudo -n rsync`. Root and HAOS are probed and use plain `rsync` when sudo is not there. The host does not get rclone, a cloud token, or an SMB password.
4. Success sets `last_backup_at`. Failure does not. One host does not run two backups at once. Path allow/deny is checked on the herder. A busy tree can be retried (rsync code 24). Retention deletes old trees **on the herder**.
5. Restore is the reverse: rsync from that herder tree back to the host. It does not read Drive or a share.

The alternate copy is a **second hop, and it runs on the herder**. rclone **1.68.2** is in the herder image only. After a tree exists under `/backups`, a saved destination can send the ticked folders to Google Drive, OneDrive, or one SMB share. That is job `backup_replicate`. It starts from **Copy now**, from that destination’s schedule, or as a follow-up after a **successful** host backup. A failed copy does not change `last_backup_at`. The herder self-backup (`/herder_backups`, optional copy into `herder/` on the same destination) is not a server backup.

The alternate destination never sees the host. It sees the mirror the herder already pulled. If Backups is off, or the rsync failed, Copy now has nothing new from that host.

### What Path C would mean

The host’s own files would go straight to Drive, OneDrive, or the NAS. `/backups` would not hold that tree first.

Path B (mount the share as the herder’s dest root) stays out. restic, borg, kopia, rclone crypt, and restore-from-remote stay out. The shapes below are not a choice.

### Shapes that stay inside the current architecture

The herder still decides, the worker still runs the transfer, secrets still start in Fernet, the web process still does not upload, and the demo still does not upload. MCP still does not start a backup copy.

1. **Keep the pull.** Enabling Backups keeps meaning “rsync onto the herder”. Drive, OneDrive, and SMB stay a copy of that mirror. This is the product that exists.
2. **Herder SSH, rclone on the host, secret only for the run.** The worker still opens SSH. It runs rclone **on the host** against the source paths. The herder writes a temp rclone config over SSH, mode `0600`, and deletes it when the run ends. The Fernet secret does not stay on the host. The job row, the mutex, and the schedule still live on the herder.
3. **Same as 2, but only for hosts that opt in.** Other hosts keep the rsync mirror. Copy now and the follow-up still copy whatever is under `/backups`. A direct host would not fill `/backups`, so those controls would not move its files unless **Copy now** is redefined to mean “start the host push”.
4. **A standing rclone config on the Pi.** The secret lives on the host. That breaks the rule that copy secrets stay in the herder database.

Shape 4 is the weak fit. Shapes 2 and 3 can use the same job, Fernet, and demo refusal as today. Shape 1 is what is shipped.

### What the host would need

| | Today (pull) | Direct copy |
|---|---|---|
| On the host | `sshd`, `rsync`, and sudo for rsync unless root or HAOS | Those, plus **rclone**, plus a route to Google, Microsoft, or the NAS |
| On the herder | SSH client, rsync, rclone 1.68.2, `/backups` disk | SSH client. rclone on the herder does not help if the bytes never arrive |
| Network | The herder must reach the host on SSH. The host need not reach the cloud | The **host** must reach the cloud or the share. A Pi that can only be reached by the herder cannot do Path C |
| Secret | Stays in Fernet. Never written to the host | A temp config for one run, or a file that stays on the host |
| Disk | The mirror sits on the herder | The herder can stay small. A sync that deletes on the remote deletes the only copy |
| HAOS | Plain `rsync` is already the special case | No apt. A static rclone copied in for the run is the plausible install |

The least-privilege sudoers today allow `rsync`, not rclone. A direct copy would extend that allowlist, or run rclone as the SSH user without sudo. Without sudo it cannot read root-owned trees the rsync path can read.

### How the current backup and the alternate copy meet

- **Backups on, no destination saved.** The host is pulled to `/backups`. Nothing is uploaded. Restore uses that tree.
- **Backups on, Drive / OneDrive / SMB saved.** The pull still happens. The alternate copy is a later job on the herder. Copy now can upload an old mirror when the host is down, because the bytes are already on the herder.
- **Backups off.** There is no mirror. Copy now can still upload other hosts’ folders already under `/backups`. It cannot invent this host’s files.
- **Direct copy instead of the pull.** Enabling Backups would no longer fill `/backups`. Copy now, the destination schedule, and the follow-up would have no tree to send. Those controls either start the host push, or they do not apply to that host. Restore’s reverse rsync has no local tree.
- **Pull and direct together.** The host sends the files twice. The herder copy and the direct copy can disagree. Retention on the herder would not age the remote files.
- **Herder self-backup.** Unchanged. It is not a server source path. A destination that opts in can still receive `herder/<archive>`.

### Pros and cons, not a decision

Direct copy helps when the herder disk is the bottleneck, the herder should not hold the files, or the NAS and the host share a LAN and the herder is elsewhere.

It costs the local restore tree, retention on the herder, and Copy now from a powered-off host. Every host needs outbound reach the pull design does not need. HAOS and the current sudoers do not have rclone. A remote sync delete can remove the only copy. Two modes double the walk.

The pull plus the herder copy helps when one machine holds the credentials and rclone, a host only speaks SSH, Copy now must work from the mirror, and restore stays a reverse rsync. It costs herder disk and a second transfer after the pull.

### Not decided

Whether Path C is built. Whether it replaces **Backups** or sits beside it. Whether rclone is installed by the operator or copied for one run. Whether `/backups` remains for some hosts. Those questions are for **v1.11.0** ([PLAN_v1.11.0.md](PLAN_v1.11.0.md)). Restore from Drive, SMB, or OneDrive is a Discover note on that train. It is not built here.

---

*Released 2026-10-05. Package `1.10.0`. Tag `v1.10.0`. Operator walks live in [QA_v1.10.0.md](QA_v1.10.0.md).*
