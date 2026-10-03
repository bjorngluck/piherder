# PiHerder v1.10.0 — NAS copy, OneDrive, agent sign-in

**Status:** **Active** (train opened 2026-10-02). Package stays **1.9.0**.  
**Date opened:** 2026-10-02  
**Git branch:** `v1.10.0-dev` → `main` · tag `v1.10.0` at freeze  
**Package / image version:** stays **`1.9.0`** until freeze. Image tags at freeze: `1.10.0` / `1.10` / `latest`. Pins `1.9.0` / `1.9` stay valid.  
**Theme:** walk the NAS copy, make OneDrive selectable, then agent sign-in and the DR archive copy  
**Baseline:** `v1.9.0` (tagged 2026-10-02; Hub digest `sha256:8519ad53e7d0ad0636164966bb876c4774f76b10101bb3c1a74dc6b2d0945472`)  
**Mode:** **Must → Should → Discover.** Must **Copy now** · **OneDrive** · **MCP OAuth** · **DR copy** · **dependabot.yml** · close the matching Dependabot alerts.  
**QA:** [QA_v1.10.0.md](QA_v1.10.0.md) (maintainer stub — **not** the operator wiki)  
**Related:** [PLAN_v1.9.0.md](PLAN_v1.9.0.md) · [RELEASE_v1.9.0.md](RELEASE_v1.9.0.md) · [FEATURE_PLAN_BACKUP_DESTINATIONS.md](FEATURE_PLAN_BACKUP_DESTINATIONS.md) · wiki [Backups](../wiki/day-to-day/backups.md) · wiki [Agents (MCP)](../wiki/operations/mcp.md)

> **Train open 2026-10-02.** Production stays **v1.9.0** on `main`. Package stays **`1.9.0`** until freeze. The public demo stays the **1.7.0** image until the Should below is asked for. Do not redeploy the demo onto this branch.

---

## 0. Intent

v1.9.0 shipped a LAN share beside Google Drive, Move on by default, plugin **0.5.0**, and one-service jobs on hosted MCP. **Copy now**, the schedule, and the follow-up after a host backup are already in that release. The dedicated-NAS walk was not done. OneDrive is listed and cannot be selected. An agent still signs in with only a `ph_` bearer token. The herder self-backup stays on its own local path.

**Must:**

1. **Copy now.** Walk on-demand, the schedule, and the follow-up after a host backup on a dedicated NAS. That covers the SMB share and the existing Google Drive hop. Fix what the walk breaks. SMB **Test** and the password-at-rest check stay signed on 1.9 and are not reopened. A separate NAS is required before the walk.
2. **OneDrive.** Same card, selectable rclone hop, its own row, Fernet secret, then the same copy test.
3. **MCP OAuth.** An agent can sign in to `POST /mcp` without only a `ph_` bearer token.
4. **DR copy.** The herder self-backup archive can go to Drive, the NAS, or OneDrive. It is not only the separate local path.
5. **dependabot.yml.** Dependabot security updates for this repository.
6. **Close Dependabot alerts.** Close the alerts that match PyJWT **2.15.1** and urllib3 **2.8.0**. Those pins are already on `main`.

**Should. The tag can ship if one slips:**

- **N3c.** Discovery plus a first slice of a Reports picker. Not the full picker. Not Grafana. The slice boundary is named when that work starts.
- **Public demo.** Align [piherder-demo.hacknow.info](https://piherder-demo.hacknow.info) with the latest release image. It stays **1.7.0** until that work is asked for.
- **Sibling repos.** Dependabot, `SECURITY.md`, and `main` branch protection on [piherder-ha](https://github.com/bjorngluck/piherder-ha) and [piherder-mcp](https://github.com/bjorngluck/piherder-mcp). No file in this repository for those repos.

**Discover. A note only. No client and no page:**

- **Path C.** Each host writes straight to Drive, OneDrive, or the NAS. Nothing lands on `/backups` first.
- **Template fleet overview.** Which hosts and services came from a given template, beyond the badge on one stack.

**Out.** Path B (CIFS mount as the dest root). AC-fg. Brand-3. ACME-in-herder. NPM CRUD. A richer Files token API. M-live. A herder→Home Assistant webhook. Undo of a finished Move. MCP Move, undo, nmap, the console, token admin, `docker_stack_down`, and `docker_stack_remove`. restic, borg, kopia, and rclone crypt. Restore from Drive, SMB, or OneDrive. SMB Kerberos. Selectable hero stats. A templates catalog redesign. Git-rich onboard. Optional AI. Ansible / cloud-init. Discord / Discussions. Swarm / Kubernetes. A higher coverage fail-under (stays **80**). The console mobile Tab issue. Stricter command-audit redaction. CodeQL. Actions pinned to commit SHAs. `CODEOWNERS`.

The Home Assistant card stays the separate HACS repo. Putting the plugin inside this image is not a requirement and is not a backlog row.

---

## 1. Decision lock (train open)

| Choice | Value |
|--------|--------|
| Integration branch | **`v1.10.0-dev`** |
| Production line | **`main` @ `v1.9.0`** — hotfixes → **`v1.9.x`**, port here |
| Git tag (freeze) | **`v1.10.0`** |
| Image tags (freeze) | `1.10.0` · `1.10` · `latest` (multi-arch); keep `1.9` / `1.9.x` pins valid |
| Must | **Copy now** · **OneDrive** · **MCP OAuth** · **DR copy** · **dependabot.yml** · close matching Dependabot alerts |
| Should (may slip) | **N3c** first slice · public demo on the latest release image · sibling-repo Dependabot and branch protection |
| Discover (no code) | **Path C** · template fleet overview |
| Version bump | Freeze only. About / footer stay **1.9.0** until then |
| Demo | Stays the published **1.7.0** image until the Should is asked for |
| Adapter / plugin | Stay **0.3.1** and **0.5.0** until a slice needs a new tag |
| Coverage | Fail-under stays **80**. Do not lower it or raise it |
| Schema | None at open. A new Alembic revision only if a Must needs one |

```text
main (image 1.9.0)
  └─ v1.10.0-dev → merge → tag v1.10.0 → Hub
```

| Rule | Practice |
|------|----------|
| Must → then freeze | **Copy now** before OneDrive. Do not start an Out item |
| OneDrive does not stay listed-only | It is Must on this train. Do not describe it as shipped before the walk |
| Discover is a write-up | Path C and the template fleet overview get a section here. No schema, no client, no page |
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

---

## 3. Next

| # | Step | Status |
|---|------|--------|
| 1 | Open **`v1.10.0-dev`** | **Done** 2026-10-02 |
| 2 | **Copy now** on a dedicated NAS, then **OneDrive**, then the **DR copy** | **OneDrive** is selectable. **DR copy** is opt-in per destination. The NAS walk is not started |
| 3 | **MCP OAuth** | Browser sign-in for `POST /mcp`. A pasted `ph_` token still works |
| 4 | **dependabot.yml** and close the matching alerts | File is on this branch. Alerts for PyJWT **2.15.1** and urllib3 **2.8.0** are already **fixed** (0 open). The file applies on `main` after merge |
| 5 | Should, if it fits | **N3c** first slice · demo image · sibling repos |
| 6 | Discover write-ups | Path C · template fleet overview. No code |
| 7 | Freeze · version bump · tag · Hub | Only when asked |

---

*Train opened 2026-10-02. Package stays `1.9.0` until the version bump. Operator walks live in [QA_v1.10.0.md](QA_v1.10.0.md).*
