# PiHerder v1.9.0 — one-service MCP, LAN share, Move on by default

**Status:** **Shipped** 2026-10-02. Tag **v1.9.0**. Package **1.9.0**. Published adapter is **`0.3.1`**. Plugin **0.5.0**. End-user notes: [RELEASE_v1.9.0.md](RELEASE_v1.9.0.md).  
**Date opened:** 2026-10-01  
**Git branch:** `v1.9.0-dev` → `main` · tag `v1.9.0` at freeze  
**Package / image version:** stays **`1.8.1`** until freeze. Image tags at freeze: `1.9.0` / `1.9` / `latest`. Pins `1.8.1` / `1.8` and `1.8.0` stay valid.  
**Theme:** **MCP-svc**, a selectable LAN share, Move on by default, then the card and backup honesty that landed with them  
**Baseline:** `v1.8.0` (tagged 2026-10-01; Hub digest `sha256:8ce50bbce758e622a996cd58557b0846e03cb02613b09471405222df620c2e89`)  
**Mode:** Landed on this branch vs **freeze-only**. Landed: **MCP-svc** · **LAN NAS / SMB** · **Move default-on** · **Remove one backup dest** (#25) · **HA Slice 3** (plugin **0.5.0**, poll-only) · **CSP Slice 2**. Hygiene: **Supply-chain locks** (on `main` as **v1.8.1**, and on this branch). Freeze-only: herder **1.9.0** tag and image. **OneDrive** stays discovery-only. Discover (no client): **Path C** · **MCP OAuth**.  
**QA:** [QA_v1.9.0.md](QA_v1.9.0.md) (signed 2026-10-02, including SMB **Test**, the password check, and the 1.8 regression. **Copy now** and a full OneDrive copy test are **v1.10** — **not** the operator wiki). Screenshot pack captured the same day.  
**Related:** [DECISION_MCP_SVC.md](DECISION_MCP_SVC.md) · [PLAN_v1.8.0.md](PLAN_v1.8.0.md) · [RELEASE_v1.8.0.md](RELEASE_v1.8.0.md) · [FEATURE_PLAN_BACKUP_DESTINATIONS.md](FEATURE_PLAN_BACKUP_DESTINATIONS.md) · [FEATURE_PLAN_HOME_ASSISTANT.md](FEATURE_PLAN_HOME_ASSISTANT.md) · [FEATURE_PLAN_SERVICE_MIGRATION.md](FEATURE_PLAN_SERVICE_MIGRATION.md) · wiki [Agents (MCP)](../wiki/operations/mcp.md) · wiki [Backups](../wiki/day-to-day/backups.md) · wiki [Home Assistant](../wiki/integrations/home-assistant.md)

> **Shipped 2026-10-02.** Tag **v1.9.0**. Package **1.9.0**. Image `1.9.0` / `1.9` / `latest`. The public demo stays the **1.7.0** image. The Home Assistant plugin stays a separate HACS repo (**0.5.0**, not inside the image). End-user notes are [RELEASE_v1.9.0.md](RELEASE_v1.9.0.md).

---

## 0. Intent

v1.8.0 shipped hosted MCP for the jobs POST of that release, a Google Drive copy of `/backups`, and one-service start, stop, restart, and update on the Home Assistant card (plugin **0.4.4**). This train adds the slices below. They are in the tree. They are not a tagged **1.9.0** image.

Landed on this branch:

1. **MCP-svc.** `container_start`, `container_stop`, `container_restart`, and `container_redeploy` are on hosted `trigger_job` and on [piherder-mcp](https://github.com/bjorngluck/piherder-mcp) `JOB_TYPES`. `source_filter` is the compose directory and `service` is required. An agent has no confirm dialog. Published adapter **0.3.1** sends the four types. Herder image **1.8.1** already accepts them. `uvx` at **0.2.0** still refuses them. Operator walk signed 2026-10-02. Decision: [DECISION_MCP_SVC.md](DECISION_MCP_SVC.md).
2. **LAN NAS / SMB.** Path A: rclone `smb` from `/backups`, one share the operator can select. The local rsync directory stays the default. Google Drive stays selectable. Drive and SMB are two independent second hops: both can be saved and live at once. Each has its own `backup_destination` row and its own Fernet secret. Path B (a host CIFS mount as the dest root) stays out. OneDrive stays listed and unselectable (discovery only).
3. **Move default-on.** `PIHERDER_SERVICE_MIGRATE` defaults to true. Set it to **false** to turn Move off. A finished Move still has no Undo. Move stays stop-first. Surfaces that start a Move: the Docker UI, the Home Assistant card (plugin **0.5.0**), and `POST /api/v1/servers/{id}/moves` with `confirm: true`. Health field `service_migrate` is that surface. Still refused: an MCP Move tool, and `POST /api/v1/servers/{id}/jobs` with `service_migrate`.
4. **Remove one backup dest (#25).** Settings → PiHerder backup, admin, `confirm=remove`. Wipes that one provider's `BackupDestination` row and its Fernet secret. The other provider's row stays. Remote files on Drive or SMB are kept. Demo refuses. Not a job, not a token route, not an MCP tool.
5. **HA Slice 3 (landed).** Plugin **[0.5.0](https://github.com/bjorngluck/piherder-ha)** plus herder #24. Poll-only. There is no herder webhook and no webhook leftover. The card confirms **Stop project** (`docker compose stop` via `docker_stack_stop`, not `down` or remove), **Move** (`POST /moves`, leftover stopped), and **Files delete**. Files stay in the fleet jail (same `files` scope as the token API). The card does not install HAOS `/config` and does not open privileged paths. One-service start, stop, restart, and update already shipped and are not reopened. Console, token admin, and nmap stay in the PiHerder UI.
6. **CSP Slice 2 (landed).** Product templates use `data-ph-*` plus `/static/js/csp-events.js`. App CSP is `script-src-attr 'none'`. Slice 1 script nonces stay. `/docs` and `/redoc` still use `script-src 'unsafe-inline'`. Operator walk signed 2026-10-02.

**Freeze-only. Do not describe these as released:**

- Herder version **1.9.0**, image tags, and the Hub publish.
- Adapter **0.3.1** is already published. It is not waiting on the herder **1.9.0** tag.

**Discovery only. Not selectable. Not a slip of something that shipped:**

- **OneDrive.** Listed in Settings. Cannot be selected. No rclone hop on this train. A real copy test, with **Copy now**, is **v1.10** after a separate NAS is set up.

**Discover only. Write the note. No client until a later promotion:**

- **Path C.** Each host writes straight to Drive, OneDrive, or the NAS. Nothing lands on `/backups` first.
- **MCP OAuth.** How an agent would sign in to `POST /mcp` without only a `ph_` bearer token. Bearer stays the only path.

**Out.** AC-fg, Brand-3 (wiki skin), ACME-in-herder, NPM CRUD, a richer Files token API, N3c, and M-live (rsync while the stack is still running). The Home Assistant plugin is not a backlog row. It stays a separate HACS integration and is not shipped inside this image.

**Hygiene, in scope.** The locks on this branch pin **PyJWT 2.15.1** and **urllib3 2.8.0**. Dependabot alerts stay open until those locks are on the default branch. Detail is [§4](#4-supply-chain-locks-must-planned). It does not change the product order above.

**Still refused:** `docker_stack_down`, `docker_stack_remove`, undo, nmap, the console, token admin, and stale-data cleanup. Move is not an MCP tool. `POST /jobs` with `service_migrate` stays **400**. The new token route is only `POST /moves`.

**Capability split (locked):** the PiHerder UI owns the console, the full Move wizard (leftover choices, undo), privileged Files, token admin, and nmap. The Home Assistant card owns a narrow Move (confirm, leftover stopped), fleet-jail Files, and stop-project. MCP owns the jobs allowlist plus fleet-jail Files tools, and does not own the console, Move, token admin, or nmap.

---

## 1. Decision lock (train open)

| Choice | Value |
|--------|--------|
| Integration branch | **`v1.9.0-dev`**, rebased onto `main` |
| Production image | **`1.8.1`** until the tag. `main` has the herder allowlist |
| Git tag (freeze) | **`v1.9.0`** |
| Image tags (freeze) | `1.9.0` · `1.9` · `latest` (multi-arch); keep `1.8` / `1.8.x` and `1.7` / `1.7.x` pins valid |
| Product decision | **Option A**, locked 2026-10-01. [DECISION_MCP_SVC.md](DECISION_MCP_SVC.md) |
| Landed on this branch | **MCP-svc** · **LAN NAS / SMB** (Drive and SMB both live) · **Move default-on** · **Remove one dest** (#25) · **HA Slice 3** (plugin **0.5.0**, poll-only) · **CSP Slice 2** |
| Hygiene | **Supply-chain locks** on this branch: PyJWT **2.15.1**, urllib3 **2.8.0**. Alerts close after `main` has the locks. See [§4](#4-supply-chain-locks-must-planned) |
| Freeze-only | Herder **1.9.0** tag and image. Adapter **0.3.1** is already published |
| Not selectable | **OneDrive** (discovery only) |
| Discover (no code) | **Path C** · **MCP OAuth** |
| Out | **AC-fg** · Brand-3 · ACME-in-herder · NPM CRUD · richer Files API · **N3c** · **M-live** |
| Deferred follow-on | `dependabot.yml`, Dependabot security updates, CodeQL, Actions SHA pins, `CODEOWNERS`. Sibling repos in [§4](#4-supply-chain-locks-must-planned) |
| Not a backlog item | Home Assistant plugin stays HACS [piherder-ha](https://github.com/bjorngluck/piherder-ha). Not inside the image |
| Version bump | Freeze only. About / footer stay **1.8.1** until then |
| Demo | Stays the published **1.7.0** image. Do not redeploy this branch |
| Move flag | Defaults **true**. `false` still disables. Finished Move has no Undo. UI wizard, HA card, and `POST /moves`. Not MCP. Not `POST /jobs` |
| Adapter | Published **0.3.1**. Hosted `/mcp` on **1.8.1** already accepts the four types |
| Coverage | Fail-under stays **80**. Do not lower it |
| Confirm | None on MCP |

```text
main (herder allowlist landed, image 1.8.1)
  └─ v1.9.0-dev → merge → tag v1.9.0 → Hub
```

| Rule | Practice |
|------|----------|
| Must → then freeze | **MCP-svc** before the share. The share before the Move default. Do not start an Out item |
| OneDrive does not block the tag | It stays unselectable. Do not describe it as shipped |
| Discover is a write-up | Path C and MCP OAuth get a section in this plan. No schema, no client, no OAuth route |
| Both MCP clients together | Do not tag piherder-mcp until the herder that agents use accepts the four types |
| Demo | Do not point the public demo at this branch |

---

## 2. Capture log

| Date | Note |
|------|------|
| 2026-10-01 | Morning lock, superseded the same day for HA Slice 3 and CSP. Train opened from `main` after **v1.8.0** shipped. Must is **MCP-svc**, a selectable LAN share, and Move on by default. OneDrive, HA Slice 3, and CSP Slice 2 started as Should. Path C and MCP OAuth are Discover. Package stays `1.8.0`. |
| 2026-10-01 | **MCP-svc herder.** Pull request #22 merged the four types onto `main`. `source_filter` is the compose directory and `service` is required. |
| 2026-10-01 | **MCP-svc adapter source.** piherder-mcp `main` (`1cf5822`) accepts the same four types. Published tag **0.2.0** does not. |
| 2026-10-01 | **Supply-chain locks.** `pyproject.toml` floors are PyJWT **>= 2.15** and urllib3 **>= 2.8**. The three lockfiles pin **PyJWT 2.15.1** and **urllib3 2.8.0**. `pip-audit` on the runtime lock reported no known vulnerabilities. Dependabot alerts stay open until this reaches `main`. |
| 2026-10-01 | **Rebase.** `v1.9.0-dev` replayed onto `main` so the train keeps the merged allowlist and the rest of this plan. |
| 2026-10-01 | **LAN NAS / SMB.** Path A rclone `smb` on Settings → PiHerder backup. One share. Password in Fernet. Path B mount stays out. |
| 2026-10-01 | **Move default-on.** `PIHERDER_SERVICE_MIGRATE` defaults true (compose and settings). `false` still disables. Stop-first. No Undo on a finished Move. Same operator+ gate. MCP still has no Move tool. |
| 2026-10-01 | **CSP Slice 2.** Inline `onclick` / `onchange` / `onsubmit` / `onerror` removed from product templates. Clicks go through `data-ph-*` and `/static/js/csp-events.js`. Script nonces stay. App `script-src-attr` is `'none'`. Style stays `'unsafe-inline'`. OpenAPI `/docs` and `/redoc` unchanged. |
| 2026-10-01 | **Move route (#24).** `POST /api/v1/servers/{id}/moves` with `confirm: true`. Health `service_migrate`. `POST /jobs` with `service_migrate` stays **400**. MCP does not grow a Move tool. |
| 2026-10-01 | **HA Slice 3 landed.** Plugin **0.5.0**, poll-only. Card confirms Move, fleet-jail Files delete, and stop project (`docker compose stop`, not down or remove). No herder webhook. Not a slip. |
| 2026-10-01 | **Remove one backup dest (#25).** Admin Settings, `confirm=remove`. One provider's `BackupDestination` row and Fernet secret. The other dest stays. Remote Drive and SMB files stay. Demo refuses. Not a job, token route, or MCP tool. |
| 2026-10-01 | **Docs honesty.** LAN SMB is selectable on this train. Drive and SMB can both be live. OneDrive stays discovery-only. Herder **1.9.0** and adapter **0.3.0** stay freeze-only. |
| 2026-10-01 | **Backup Settings UX (#28).** Each saved Drive or SMB destination is its own row, with Edit, Folders, and Remove on that row. Empty SMB username and password together is guest access. A failed Save keeps the edit form. |
| 2026-10-01 | **Operator QA.** Walks and the shot list are in [QA_v1.9.0.md](QA_v1.9.0.md). Boxes are empty. The walks use the #28 row shape. HA Move shots wait until health exposes `service_migrate`. |
| 2026-10-02 | **Operator QA signed** except three SMB boxes (Test, Copy now, Fernet) and the 1.8 regression. Screenshot pack landed. Published adapter is **0.3.1**. |
| 2026-10-02 | **SMB follow-up.** Test and the password-at-rest check signed. **Copy now** deferred to **v1.10**, with a full copy test that includes OneDrive, after a separate NAS is set up. |
| 2026-10-02 | **1.8 regression signed.** **Code freeze.** End-user notes drafted in [RELEASE_v1.9.0.md](RELEASE_v1.9.0.md). Package stays **1.8.1**. Tag and Hub not cut. |
| 2026-10-02 | **v1.10.0 train opened** on `v1.10.0-dev` after this tag shipped. [PLAN_v1.10.0.md](PLAN_v1.10.0.md). |

---

## 3. Next

| # | Step | Status |
|---|------|--------|
| 1 | Open **`v1.9.0-dev`** | **Done** 2026-10-01 |
| 2 | **MCP-svc** — four job types on hosted MCP and the adapter, then one adapter tag | **Signed** 2026-10-02. Herder on `main` (#22) and image **1.8.1**. Published adapter **0.3.1** |
| 3 | **LAN NAS / SMB** — selectable path A (`rclone smb`). Drive and SMB can both be live | **Signed** 2026-10-02, including Test and the password check. **Copy now** is **v1.10**. Path B mount stays out |
| 4 | **Move default-on** — flag defaults true. UI, HA card, `POST /moves` | **Signed** 2026-10-02, including health `service_migrate`. MCP and `POST /jobs` still refuse `service_migrate` |
| 5 | **HA Slice 3** and **CSP Slice 2** | **Signed** 2026-10-02. Plugin **0.5.0**, poll-only, including card Move. OneDrive is not in this row |
| 5b | **Remove one backup dest** (#25) | **Signed** 2026-10-02, including the scheduler check. Remote files kept |
| 6 | Discover write-ups | Path C · MCP OAuth. No code |
| 7 | Freeze · version bump · tag · Hub | **Freeze set** 2026-10-02. Version bump, tag, and Hub are not this step |
| 8 | **Supply-chain locks**: refresh `uv.lock` and both requirements lockfiles, retest, close Dependabot alerts | Pins shipped on **v1.8.1**. Alerts still need closing. [§4](#4-supply-chain-locks-must-planned) |

---

## 4. Supply-chain locks (Must)

**Status:** **Locks on `main`** as **v1.8.1**, and on this branch. This section does not edit workflows or GitHub settings.

Previous pins were **PyJWT 2.13.0** and **urllib3 2.7.0**. Those matched multiple GitHub Security Advisories at critical and high severity. Advisory identifiers stay on the GitHub advisory pages.

**In scope before the v1.9.0 tag:**

| Item | What | Status |
|------|------|--------|
| **SC1** Lock refresh | Floors in `pyproject.toml` are `PyJWT[crypto]>=2.15` and `urllib3>=2.8`. `uv.lock`, `requirements.lock.txt`, and `requirements.runtime.lock.txt` pin **PyJWT 2.15.1** and **urllib3 2.8.0**. | **Done** on this branch |
| **SC2** Retest | `pip-audit -r requirements.runtime.lock.txt` reported no known vulnerabilities. The unit suite was 1772 passed and 1 failed: `test_first_admin_and_open_registration_are_audited` returns the register form because this machine's password policy requires a special character. That failure is not from the lock bump. | **Audit clean.** Suite has that one local failure |
| **SC3** Dependabot alerts | The pins are on `main` as **v1.8.1**. Close the matching Dependabot alerts for these two packages on this repository. | **Waiting** on the alert close |
| **SC4** Release | The pins shipped in **v1.8.1**. The **v1.9.0** tag carries the same pins. | **Shipped** on **v1.8.1** |

Product order stays **MCP-svc**, then the LAN share, then Move on by default. The lock refresh can land in its own commit any time before freeze. The product slices and the lock refresh do not block each other. The tag still has to include the fixed locks.

**Deferred follow-ons.** Listed so they stay visible. They are not part of SC1 through SC4, and they are not product work.

| Item | Priority | Where |
|------|----------|--------|
| `.github/dependabot.yml` and Dependabot security updates | P1 follow-on | This repository, after the lock refresh |
| CodeQL, GitHub Actions pinned to commit SHAs, `CODEOWNERS` | P2 follow-on | This repository |
| Dependabot alerts, `SECURITY.md`, `dependabot.yml`, and `main` branch protection | Ecosystem follow-up | [piherder-ha](https://github.com/bjorngluck/piherder-ha) and [piherder-mcp](https://github.com/bjorngluck/piherder-mcp). No file changes in this repository for those repos |

Tag honesty: **v1.9.0** tags only with **MCP-svc** on both clients. The herder allowlist is on **v1.8.1**. Adapter **0.3.1** is published and sends the four types.

---

*Code freeze 2026-10-02. Package stays `1.8.1` until the version bump. Operator walks live in [QA_v1.9.0.md](QA_v1.9.0.md).*
