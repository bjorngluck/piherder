# PiHerder v1.9.0 — one-service MCP, LAN share, Move on by default

**Status:** **Active** (rebased onto `main` 2026-10-01). Hosted `MCP_JOB_TYPES` includes the four one-service jobs. Package stays **`1.8.0`** until freeze.  
**Date opened:** 2026-10-01  
**Git branch:** `v1.9.0-dev` → `main` · tag `v1.9.0` at freeze  
**Package / image version:** stays **`1.8.0`** until freeze. Image tags at freeze: `1.9.0` / `1.9` / `latest`. Pins `1.8.0` / `1.8` stay valid.  
**Theme:** **MCP-svc**, then a selectable LAN share, then Move on by default  
**Baseline:** `v1.8.0` (tagged 2026-10-01; Hub digest `sha256:8ce50bbce758e622a996cd58557b0846e03cb02613b09471405222df620c2e89`)  
**Mode:** **Must → Should → Discover.** Must **MCP-svc** · **LAN NAS / SMB** · **Move default-on**. Hygiene Must **Supply-chain locks** (refreshed on this branch). Should **OneDrive** · **HA Slice 3** · **CSP Slice 2**. Discover **Path C** · **MCP OAuth**.  
**QA:** [QA_v1.9.0.md](QA_v1.9.0.md) (maintainer stub — **not** the operator wiki)  
**Related:** [DECISION_MCP_SVC.md](DECISION_MCP_SVC.md) · [PLAN_v1.8.0.md](PLAN_v1.8.0.md) · [RELEASE_v1.8.0.md](RELEASE_v1.8.0.md) · [FEATURE_PLAN_BACKUP_DESTINATIONS.md](FEATURE_PLAN_BACKUP_DESTINATIONS.md) · [FEATURE_PLAN_SERVICE_MIGRATION.md](FEATURE_PLAN_SERVICE_MIGRATION.md) · wiki [Agents (MCP)](../wiki/operations/mcp.md) · wiki [Backups](../wiki/day-to-day/backups.md)

> **Train open 2026-10-01.** The image tag stays **v1.8.0**. `main` already includes the herder allowlist (pull request #22). This branch is that `main` plus the rest of the train. The public demo stays the **1.7.0** image. The Home Assistant plugin stays a separate HACS repo. Release notes (`RELEASE_v1.9.0.md`) are written at freeze.

---

## 0. Intent

v1.8.0 shipped hosted MCP for the existing jobs POST, a Google Drive copy of `/backups`, and one-service start, stop, restart, and update on the Home Assistant card. Hosted `trigger_job` now accepts those four job types. OneDrive is listed in Settings and cannot be selected. A LAN share can be saved on this branch (path A). Move stays off unless `PIHERDER_SERVICE_MIGRATE=true`.

Wanted, in order:

1. **MCP-svc (Must).** `container_start`, `container_stop`, `container_restart`, and `container_redeploy` join `trigger_job`. All four. The herder side is on `main`. The stdio `JOB_TYPES` list is on [piherder-mcp](https://github.com/bjorngluck/piherder-mcp) `main`. The annotated tag is not cut. Published **0.2.0** and `uvx` still refuse the types. Tag the adapter only when the herder image agents call accepts them. The image tag is still **1.8.0**. `source_filter` is the compose directory and `service` is required. An agent still has no confirm dialog. Decision: [DECISION_MCP_SVC.md](DECISION_MCP_SVC.md).
2. **LAN NAS / SMB (Must).** Path A from the v1.8 destination write-up: rclone `smb` from `/backups`, one share the operator can select. The local rsync directory stays the default. Google Drive stays selectable. Path B (a host CIFS mount as the dest root) stays out.
3. **Move default-on (Must).** `PIHERDER_SERVICE_MIGRATE` defaults to true. Set it to **false** to turn Move off. A finished Move still has no Undo. Move stays stop-first. Move stays UI-only.

**Should, and may slip the tag:**

- **OneDrive.** The same rclone second hop as Google Drive. Listed today, not selectable.
- **HA Slice 3.** All four leftovers on the card: webhooks, Move from the card, Files from the card, and stop of a whole compose project. One-service start, stop, restart, and update already shipped and are not reopened here. Whole-project stop on the card is not `docker_stack_down`.
- **CSP Slice 2.** Rewrite inline `onclick` handlers. Slice 1 script nonces stay.

**Discover only. Write the note. No client until a later promotion:**

- **Path C.** Each host writes straight to Drive, OneDrive, or the NAS. Nothing lands on `/backups` first.
- **MCP OAuth.** How an agent would sign in to `POST /mcp` without only a `ph_` bearer token. Bearer stays the only path.

**Out.** AC-fg, Brand-3 (wiki skin), ACME-in-herder, NPM CRUD, a richer Files token API, N3c, and M-live (rsync while the stack is still running). The Home Assistant plugin is not a backlog row. It stays a separate HACS integration and is not shipped inside this image.

**Hygiene, in scope.** The locks on this branch pin **PyJWT 2.15.1** and **urllib3 2.8.0**. Dependabot alerts stay open until those locks are on the default branch. Detail is [§4](#4-supply-chain-locks-must-planned). It does not change the product order above.

**Still refused after MCP-svc:** `docker_stack_down`, `docker_stack_remove`, undo, nmap, the console, token admin, and stale-data cleanup. Move is not an MCP tool. No new `/api/v1` route.

---

## 1. Decision lock (train open)

| Choice | Value |
|--------|--------|
| Integration branch | **`v1.9.0-dev`**, rebased onto `main` |
| Production image | **`1.8.0`** until the tag. `main` has the herder allowlist |
| Git tag (freeze) | **`v1.9.0`** |
| Image tags (freeze) | `1.9.0` · `1.9` · `latest` (multi-arch); keep `1.8` / `1.8.x` and `1.7` / `1.7.x` pins valid |
| Product decision | **Option A**, locked 2026-10-01. [DECISION_MCP_SVC.md](DECISION_MCP_SVC.md) |
| Must, in order | **MCP-svc** · **LAN NAS / SMB** · **Move default-on** |
| Hygiene Must | **Supply-chain locks** on this branch: PyJWT **2.15.1**, urllib3 **2.8.0**. Alerts close after `main` has the locks. See [§4](#4-supply-chain-locks-must-planned) |
| Should (may slip) | **OneDrive** · **HA Slice 3** (all four) · **CSP Slice 2** |
| Discover (no code) | **Path C** · **MCP OAuth** |
| Out | **AC-fg** · Brand-3 · ACME-in-herder · NPM CRUD · richer Files API · **N3c** · **M-live** |
| Deferred follow-on | `dependabot.yml`, Dependabot security updates, CodeQL, Actions SHA pins, `CODEOWNERS`. Sibling repos in [§4](#4-supply-chain-locks-must-planned) |
| Not a backlog item | Home Assistant plugin stays HACS [piherder-ha](https://github.com/bjorngluck/piherder-ha). Not inside the image |
| Version bump | Freeze only. About / footer stay **1.8.0** until then |
| Demo | Stays the published **1.7.0** image. Do not redeploy this branch |
| Move flag | Defaults **true**. `false` still disables. Finished Move has no Undo. UI-only |
| Adapter | Published **0.2.0** until the MCP-svc tag. Hosted `/mcp` already accepts the four types |
| Coverage | Fail-under stays **80**. Do not lower it |
| Confirm | None on MCP |

```text
main (herder allowlist landed, image still 1.8.0)
  └─ v1.9.0-dev → merge → tag v1.9.0 → Hub
```

| Rule | Practice |
|------|----------|
| Must → then freeze | **MCP-svc** before the share. The share before the Move default. Do not start an Out item |
| Should may slip | OneDrive, HA Slice 3, and CSP Slice 2 do **not** block the tag |
| Discover is a write-up | Path C and MCP OAuth get a section in this plan. No schema, no client, no OAuth route |
| Both MCP clients together | Do not tag piherder-mcp until the herder that agents use accepts the four types |
| Demo | Do not point the public demo at this branch |

---

## 2. Capture log

| Date | Note |
|------|------|
| 2026-10-01 | Train opened from `main` after **v1.8.0** shipped. Must is **MCP-svc**, a selectable LAN share, and Move on by default. OneDrive, HA Slice 3, and CSP Slice 2 are Should. Path C and MCP OAuth are Discover. Package stays `1.8.0`. |
| 2026-10-01 | **MCP-svc herder.** Pull request #22 merged the four types onto `main`. `source_filter` is the compose directory and `service` is required. |
| 2026-10-01 | **MCP-svc adapter source.** piherder-mcp `main` (`1cf5822`) accepts the same four types. Published tag **0.2.0** does not. |
| 2026-10-01 | **Supply-chain locks.** `pyproject.toml` floors are PyJWT **>= 2.15** and urllib3 **>= 2.8**. The three lockfiles pin **PyJWT 2.15.1** and **urllib3 2.8.0**. `pip-audit` on the runtime lock reported no known vulnerabilities. Dependabot alerts stay open until this reaches `main`. |
| 2026-10-01 | **Rebase.** `v1.9.0-dev` replayed onto `main` so the train keeps the merged allowlist and the rest of this plan. |
| 2026-10-01 | **LAN NAS / SMB.** Path A rclone `smb` on Settings → PiHerder backup. One share. Password in Fernet. Path B mount stays out. |
| 2026-10-01 | **Move default-on.** `PIHERDER_SERVICE_MIGRATE` defaults true (compose and settings). `false` still disables. Stop-first. No Undo on a finished Move. Same operator+ gate. Not on MCP, the token API, or the HA card. |

---

## 3. Next

| # | Step | Status |
|---|------|--------|
| 1 | Open **`v1.9.0-dev`** | **Done** 2026-10-01 |
| 2 | **MCP-svc** — four job types on hosted MCP and the adapter, then one adapter tag | Herder on `main` (#22). Adapter source on piherder-mcp `main`. Published tag **0.2.0**. Walk open |
| 3 | **LAN NAS / SMB** — selectable path A (`rclone smb`) | Built on this branch. Path B mount stays out |
| 4 | **Move default-on** — flag defaults true | **Done** on this branch. Unset env is on; `false` disables |
| 5 | Should, if they do not slip | OneDrive · HA Slice 3 · CSP Slice 2 |
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
