# PiHerder v1.9.0 — one-service MCP, LAN share, Move on by default

**Status:** **Active** (train opened 2026-10-01). No product code yet.  
**Date opened:** 2026-10-01  
**Git branch:** `v1.9.0-dev` → `main` · tag `v1.9.0` at freeze  
**Package / image version:** stays **`1.8.0`** until freeze  
**Theme:** **MCP-svc**, then a selectable LAN share, then Move on by default  
**Baseline:** `v1.8.0` (tagged 2026-10-01; Hub digest `sha256:8ce50bbce758e622a996cd58557b0846e03cb02613b09471405222df620c2e89`)  
**Mode:** **Must → Should → Discover.** Must **MCP-svc** · **LAN NAS / SMB** · **Move default-on**. Should **OneDrive** · **HA Slice 3** · **CSP Slice 2**. Discover **Path C** · **MCP OAuth**.  
**QA:** [QA_v1.9.0.md](QA_v1.9.0.md) (maintainer stub — **not** the operator wiki)  
**Related:** [PLAN_v1.8.0.md](PLAN_v1.8.0.md) · [RELEASE_v1.8.0.md](RELEASE_v1.8.0.md) · [FEATURE_PLAN_BACKUP_DESTINATIONS.md](FEATURE_PLAN_BACKUP_DESTINATIONS.md) · [FEATURE_PLAN_SERVICE_MIGRATION.md](FEATURE_PLAN_SERVICE_MIGRATION.md) · wiki [Agents (MCP)](../wiki/operations/mcp.md) · wiki [Backups](../wiki/day-to-day/backups.md)

> **Train open 2026-10-01.** Production stays **v1.8.0** on `main`. Package stays **`1.8.0`** until freeze. The public demo stays the **1.7.0** image. Do not redeploy it onto this branch. The Home Assistant plugin stays a separate HACS repo.

---

## 0. Intent

v1.8.0 shipped hosted MCP for the existing jobs POST, a Google Drive copy of `/backups`, and one-service start, stop, restart, and update on the Home Assistant card. Those four job types are still refused by `trigger_job`. OneDrive and a LAN share are listed in Settings and cannot be selected. Move stays off unless `PIHERDER_SERVICE_MIGRATE=true`.

Wanted, in order:

1. **MCP-svc (Must).** `container_start`, `container_stop`, `container_restart`, and `container_redeploy` join `trigger_job`. All four. Hosted `MCP_JOB_TYPES` and the stdio `JOB_TYPES` list move in the same change. Then one annotated [piherder-mcp](https://github.com/bjorngluck/piherder-mcp) tag in that same turn, so `uvx` and the registry pick it up. Tagging the adapter first returns **400**. Adapter stays **0.2.0** until that turn. `source_filter` is the compose directory and `service` is required. An agent still has no confirm dialog.
2. **LAN NAS / SMB (Must).** Path B from the v1.8 destination write-up: a share the operator can select, as the dest root or as an rclone `smb` remote. The local rsync directory stays the default. Google Drive stays selectable.
3. **Move default-on (Must).** `PIHERDER_SERVICE_MIGRATE` defaults to true. The flag stays **false** until this slice lands. A finished Move still has no Undo. Move stays stop-first.

**Should, and may slip the tag:**

- **OneDrive.** The same rclone second hop as Google Drive. Listed today, not selectable.
- **HA Slice 3.** All four leftovers on the card: webhooks, Move from the card, Files from the card, and stop of a whole compose project. One-service start, stop, restart, and update already shipped and are not reopened here. Whole-project stop on the card is not `docker_stack_down`.
- **CSP Slice 2.** Rewrite inline `onclick` handlers. Slice 1 script nonces stay.

**Discover only. Write the note. No client until a later promotion:**

- **Path C.** Each host writes straight to Drive, OneDrive, or the NAS. Nothing lands on `/backups` first.
- **MCP OAuth.** How an agent would sign in to `POST /mcp` without only a `ph_` bearer token. Bearer stays the only path.

**Out.** AC-fg, Brand-3 (wiki skin), ACME-in-herder, NPM CRUD, a richer Files token API, N3c, and M-live (rsync while the stack is still running). The Home Assistant plugin is not a backlog row. It stays a separate HACS integration and is not shipped inside this image.

**Still refused after MCP-svc:** `docker_stack_down`, `docker_stack_remove`, undo, nmap, the console, and token admin. Move is not an MCP tool.

---

## 1. Decision lock (train open)

| Choice | Value |
|--------|--------|
| Integration branch | **`v1.9.0-dev`** |
| Production line | **`main` @ `v1.8.0`** — hotfixes → **`v1.8.x`**, port here |
| Git tag (freeze) | **`v1.9.0`** |
| Image tags (freeze) | `1.9.0` · `1.9` · `latest` (multi-arch); keep `1.8` / `1.8.x` and `1.7` / `1.7.x` pins valid |
| Must, in order | **MCP-svc** · **LAN NAS / SMB** · **Move default-on** |
| Should (may slip) | **OneDrive** · **HA Slice 3** (all four) · **CSP Slice 2** |
| Discover (no code) | **Path C** · **MCP OAuth** |
| Out | **AC-fg** · Brand-3 · ACME-in-herder · NPM CRUD · richer Files API · **N3c** · **M-live** |
| Not a backlog item | Home Assistant plugin stays HACS [piherder-ha](https://github.com/bjorngluck/piherder-ha). Not inside the image |
| Version bump | Freeze only. About / footer stay **1.8.0** until then |
| Demo | Stays the published **1.7.0** image. Do not redeploy this branch |
| Move flag | Stays **false** until the Move-default slice lands |
| Adapter | Stays **0.2.0** until the MCP-svc tag turn |
| Coverage | Fail-under stays **80**. Do not lower it |

```text
main @ v1.8.0 (+ v1.8.x patches)
  └─ v1.9.0-dev → merge → main → tag v1.9.0 → Hub
```

| Rule | Practice |
|------|----------|
| Must → then freeze | **MCP-svc** before the share. The share before the Move default. Do not start an Out item |
| Should may slip | OneDrive, HA Slice 3, and CSP Slice 2 do **not** block the tag |
| Discover is a write-up | Path C and MCP OAuth get a section in this plan. No schema, no client, no OAuth route |
| Both MCP clients together | Do not tag piherder-mcp until the herder accepts the four types |
| Demo | Do not point the public demo at this branch |

---

## 2. Capture log

| Date | Note |
|------|------|
| 2026-10-01 | Train opened from `main` after **v1.8.0** shipped. Must is **MCP-svc**, a selectable LAN share, and Move on by default. OneDrive, HA Slice 3, and CSP Slice 2 are Should. Path C and MCP OAuth are Discover. Package stays `1.8.0`. |

---

## 3. Next

| # | Step | Status |
|---|------|--------|
| 1 | Open **`v1.9.0-dev`** | **Done** 2026-10-01 |
| 2 | **MCP-svc** — four job types on hosted MCP and the adapter, then one adapter tag | Not started |
| 3 | **LAN NAS / SMB** — selectable path B | Not started |
| 4 | **Move default-on** — flag defaults true | Not started. Flag stays false until this row |
| 5 | Should, if they do not slip | OneDrive · HA Slice 3 · CSP Slice 2 |
| 6 | Discover write-ups | Path C · MCP OAuth. No code |
| 7 | Freeze · version bump · tag · Hub | Only when asked |

---

*Package stays `1.8.0` until freeze. Operator walks live in [QA_v1.9.0.md](QA_v1.9.0.md).*
