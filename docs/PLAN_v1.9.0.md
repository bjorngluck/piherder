# PiHerder v1.9.0 — one-service MCP, LAN share, Move on by default

**Status:** **Active** on `v1.9.0-dev` (tip includes #25). Product slices below are **on this branch**. Package stays **`1.8.0`**. Adapter stays published **`0.2.0`**. Neither bump is this train's docs pass.  
**Date opened:** 2026-10-01  
**Git branch:** `v1.9.0-dev` → `main` · tag `v1.9.0` at freeze  
**Package / image version:** stays **`1.8.0`** until freeze. Image tags at freeze: `1.9.0` / `1.9` / `latest`. Pins `1.8.0` / `1.8` stay valid.  
**Theme:** **MCP-svc**, a selectable LAN share, Move on by default, then the card and backup honesty that landed with them  
**Baseline:** `v1.8.0` (tagged 2026-10-01; Hub digest `sha256:8ce50bbce758e622a996cd58557b0846e03cb02613b09471405222df620c2e89`)  
**Mode:** Landed on this branch vs **freeze-only**. Landed: **MCP-svc** · **LAN NAS / SMB** · **Move default-on** · **Remove one backup dest** (#25) · **HA Slice 3** (plugin **0.5.0**, poll-only) · **CSP Slice 2**. Hygiene: **Supply-chain locks** (refreshed on this branch). Freeze-only: herder **1.9.0**, adapter **0.3.0**, tags and images. **OneDrive** stays discovery-only. Discover (no client): **Path C** · **MCP OAuth**.  
**QA:** [QA_v1.9.0.md](QA_v1.9.0.md) (maintainer stub — **not** the operator wiki; walks still open)  
**Related:** [DECISION_MCP_SVC.md](DECISION_MCP_SVC.md) · [PLAN_v1.8.0.md](PLAN_v1.8.0.md) · [RELEASE_v1.8.0.md](RELEASE_v1.8.0.md) · [FEATURE_PLAN_BACKUP_DESTINATIONS.md](FEATURE_PLAN_BACKUP_DESTINATIONS.md) · [FEATURE_PLAN_HOME_ASSISTANT.md](FEATURE_PLAN_HOME_ASSISTANT.md) · [FEATURE_PLAN_SERVICE_MIGRATION.md](FEATURE_PLAN_SERVICE_MIGRATION.md) · wiki [Agents (MCP)](../wiki/operations/mcp.md) · wiki [Backups](../wiki/day-to-day/backups.md) · wiki [Home Assistant](../wiki/integrations/home-assistant.md)

> **Train open 2026-10-01.** The image tag stays **v1.8.0**. `main` already includes the herder allowlist (pull request #22). This branch is that `main` plus the rest of the train, through Remove (#25). The public demo stays the **1.7.0** image. The Home Assistant plugin stays a separate HACS repo (**0.5.0** on this train, not inside the image). Release notes (`RELEASE_v1.9.0.md`) are written at freeze. Do not treat this plan as a tagged release.

---

## 0. Intent

v1.8.0 shipped hosted MCP for the jobs POST of that release, a Google Drive copy of `/backups`, and one-service start, stop, restart, and update on the Home Assistant card (plugin **0.4.4**). This train adds the slices below. They are in the tree. They are not a tagged **1.9.0** image.

Landed on this branch:

1. **MCP-svc.** `container_start`, `container_stop`, `container_restart`, and `container_redeploy` are on hosted `trigger_job` and on [piherder-mcp](https://github.com/bjorngluck/piherder-mcp) `JOB_TYPES` in the same train. `source_filter` is the compose directory and `service` is required. An agent has no confirm dialog. The annotated adapter tag is **not** cut. Published **0.2.0** and `uvx` still refuse the types. Tag **0.3.0** only at freeze, with the herder image agents call. The image tag is still **1.8.0**. Decision: [DECISION_MCP_SVC.md](DECISION_MCP_SVC.md).
2. **LAN NAS / SMB.** Path A: rclone `smb` from `/backups`, one share the operator can select. The local rsync directory stays the default. Google Drive stays selectable. Drive and SMB are two independent second hops: both can be saved and live at once. Each has its own `backup_destination` row and its own Fernet secret. Path B (a host CIFS mount as the dest root) stays out. OneDrive stays listed and unselectable (discovery only).
3. **Move default-on.** `PIHERDER_SERVICE_MIGRATE` defaults to true. Set it to **false** to turn Move off. A finished Move still has no Undo. Move stays stop-first. Surfaces that start a Move: the Docker UI, the Home Assistant card (plugin **0.5.0**), and `POST /api/v1/servers/{id}/moves` with `confirm: true`. Health field `service_migrate` is that surface. Still refused: an MCP Move tool, and `POST /api/v1/servers/{id}/jobs` with `service_migrate`.
4. **Remove one backup dest (#25).** Settings → PiHerder backup, admin, `confirm=remove`. Wipes that one provider's `BackupDestination` row and its Fernet secret. The other provider's row stays. Remote files on Drive or SMB are kept. Demo refuses. Not a job, not a token route, not an MCP tool.
5. **HA Slice 3 (landed).** Plugin **[0.5.0](https://github.com/bjorngluck/piherder-ha)** plus herder #24. Poll-only. There is no herder webhook and no webhook leftover. The card confirms **Stop project** (`docker compose stop` via `docker_stack_stop`, not `down` or remove), **Move** (`POST /moves`, leftover stopped), and **Files delete**. Files stay in the fleet jail (same `files` scope as the token API). The card does not install HAOS `/config` and does not open privileged paths. One-service start, stop, restart, and update already shipped and are not reopened. Console, token admin, and nmap stay in the PiHerder UI.
6. **CSP Slice 2 (landed).** Product templates use `data-ph-*` plus `/static/js/csp-events.js`. App CSP is `script-src-attr 'none'`. Slice 1 script nonces stay. `/docs` and `/redoc` still use `script-src 'unsafe-inline'`. The live walk is still open in QA.

**Freeze-only. Do not describe these as released:**

- Herder version **1.9.0**, image tags, and the Hub publish.
- Adapter version **0.3.0** (published adapter stays **0.2.0** until that tag).

**Discovery only. Not selectable. Not a slip of something that shipped:**

- **OneDrive.** Listed in Settings. Cannot be selected. No rclone hop.

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
| Production image | **`1.8.0`** until the tag. `main` has the herder allowlist |
| Git tag (freeze) | **`v1.9.0`** |
| Image tags (freeze) | `1.9.0` · `1.9` · `latest` (multi-arch); keep `1.8` / `1.8.x` and `1.7` / `1.7.x` pins valid |
| Product decision | **Option A**, locked 2026-10-01. [DECISION_MCP_SVC.md](DECISION_MCP_SVC.md) |
| Landed on this branch | **MCP-svc** · **LAN NAS / SMB** (Drive and SMB both live) · **Move default-on** · **Remove one dest** (#25) · **HA Slice 3** (plugin **0.5.0**, poll-only) · **CSP Slice 2** |
| Hygiene | **Supply-chain locks** on this branch: PyJWT **2.15.1**, urllib3 **2.8.0**. Alerts close after `main` has the locks. See [§4](#4-supply-chain-locks-must-planned) |
| Freeze-only | Herder **1.9.0** · adapter **0.3.0** · tags and images. Published adapter stays **0.2.0** |
| Not selectable | **OneDrive** (discovery only) |
| Discover (no code) | **Path C** · **MCP OAuth** |
| Out | **AC-fg** · Brand-3 · ACME-in-herder · NPM CRUD · richer Files API · **N3c** · **M-live** |
| Deferred follow-on | `dependabot.yml`, Dependabot security updates, CodeQL, Actions SHA pins, `CODEOWNERS`. Sibling repos in [§4](#4-supply-chain-locks-must-planned) |
| Not a backlog item | Home Assistant plugin stays HACS [piherder-ha](https://github.com/bjorngluck/piherder-ha). Not inside the image |
| Version bump | Freeze only. About / footer stay **1.8.0** until then |
| Demo | Stays the published **1.7.0** image. Do not redeploy this branch |
| Move flag | Defaults **true**. `false` still disables. Finished Move has no Undo. UI wizard, HA card, and `POST /moves`. Not MCP. Not `POST /jobs` |
| Adapter | Published **0.2.0** until freeze (**0.3.0** is freeze-only). Hosted `/mcp` already accepts the four types |
| Coverage | Fail-under stays **80**. Do not lower it |
| Confirm | None on MCP |

```text
main (herder allowlist landed, image still 1.8.0)
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

---

## 3. Next

| # | Step | Status |
|---|------|--------|
| 1 | Open **`v1.9.0-dev`** | **Done** 2026-10-01 |
| 2 | **MCP-svc** — four job types on hosted MCP and the adapter, then one adapter tag | Herder on `main` (#22). Adapter source on piherder-mcp `main`. Published tag **0.2.0**. Walk open |
| 3 | **LAN NAS / SMB** — selectable path A (`rclone smb`). Drive and SMB can both be live | Built on this branch. Path B mount stays out. OneDrive stays unselectable |
| 4 | **Move default-on** — flag defaults true. UI, HA card, `POST /moves` | **Done** on this branch. Unset env is on; `false` disables. MCP and `POST /jobs` still refuse `service_migrate` |
| 5 | **HA Slice 3** and **CSP Slice 2** | **Landed** on this branch. Plugin **0.5.0**, poll-only. CSP walk still open in QA. OneDrive is not in this row |
| 5b | **Remove one backup dest** (#25) | **Landed.** Settings wipe of one provider. Remote files kept |
| 6 | Discover write-ups | Path C · MCP OAuth. No code |
| 7 | Freeze · version bump · tag · Hub | Only when asked |
| 8 | **Supply-chain locks**: refresh `uv.lock` and both requirements lockfiles, retest, close Dependabot alerts | Locks refreshed on this branch. Alerts wait for `main`. [§4](#4-supply-chain-locks-must-planned) |

---

## 4. Supply-chain locks (Must)

**Status:** **Locks refreshed** on `v1.9.0-dev`. Not on the default branch yet. This section does not edit workflows or GitHub settings.

Previous pins were **PyJWT 2.13.0** and **urllib3 2.7.0**. Those matched multiple GitHub Security Advisories at critical and high severity. Advisory identifiers stay on the GitHub advisory pages.

**In scope before the v1.9.0 tag:**

| Item | What | Status |
|------|------|--------|
| **SC1** Lock refresh | Floors in `pyproject.toml` are `PyJWT[crypto]>=2.15` and `urllib3>=2.8`. `uv.lock`, `requirements.lock.txt`, and `requirements.runtime.lock.txt` pin **PyJWT 2.15.1** and **urllib3 2.8.0**. | **Done** on this branch |
| **SC2** Retest | `pip-audit -r requirements.runtime.lock.txt` reported no known vulnerabilities. The unit suite was 1772 passed and 1 failed: `test_first_admin_and_open_registration_are_audited` returns the register form because this machine's password policy requires a special character. That failure is not from the lock bump. | **Audit clean.** Suite has that one local failure |
| **SC3** Dependabot alerts | After the locks are on the default branch (this train's merge, or a `v1.8.x` patch), close the matching Dependabot alerts for these two packages on this repository. | **Waiting** on `main` |
| **SC4** Release | Ship the locks in the **v1.9.0** tag. A **v1.8.x** patch on `main` may carry the same bump first if production should not wait for freeze. Port that patch onto this branch. This plan does not open the patch. | **Planned** |

Product order stays **MCP-svc**, then the LAN share, then Move on by default. The lock refresh can land in its own commit any time before freeze. The product slices and the lock refresh do not block each other. The tag still has to include the fixed locks.

**Deferred follow-ons.** Listed so they stay visible. They are not part of SC1 through SC4, and they are not product work.

| Item | Priority | Where |
|------|----------|--------|
| `.github/dependabot.yml` and Dependabot security updates | P1 follow-on | This repository, after the lock refresh |
| CodeQL, GitHub Actions pinned to commit SHAs, `CODEOWNERS` | P2 follow-on | This repository |
| Dependabot alerts, `SECURITY.md`, `dependabot.yml`, and `main` branch protection | Ecosystem follow-up | [piherder-ha](https://github.com/bjorngluck/piherder-ha) and [piherder-mcp](https://github.com/bjorngluck/piherder-mcp). No file changes in this repository for those repos |

Tag honesty: **v1.9.0** tags only with **MCP-svc** on both clients. The herder allowlist has landed. The adapter tag waits until that herder is what agents call.

---

*Package stays `1.8.0` until freeze. Operator walks live in [QA_v1.9.0.md](QA_v1.9.0.md).*
