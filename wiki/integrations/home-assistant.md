# Home Assistant → PiHerder (HACS)

## What this is

A **HACS integration that runs on Home Assistant** and **observes** your PiHerder fleet over `/api/v1`. It is **not** the same as [HAOS hosts](../day-to-day/haos-hosts.md) (PiHerder managing the appliance over SSH).

| Arrow | Where it lives | What it does |
|-------|----------------|--------------|
| Path 1 | This PiHerder image | SSH + `ha` CLI on an HAOS **server** |
| Path 2 (this page) | Separate HACS repo | HA polls PiHerder snapshots; dashboard + Open in PiHerder |

The plugin is **not** inside the PiHerder Docker image.

## Why it exists

YAML `rest` sensors against an API token already work. Path 2 is that, first-class: config flow, fleet sensors, one HA device per host, deep link back to the herder.

## Install (Slice 1)

1. In PiHerder: Settings → **API management** → create a token with **`read` only**. Set the **IP allowlist** to the HA host (HAOS ≈ appliance LAN IP; a container HA may egress as a Docker/bridge IP).  
2. HACS → custom repository → **Integration**. Lean repo name: `bjorngluck/piherder-ha` (confirm on GitHub when published).  
3. Add **PiHerder**: base URL (your herder origin, including scheme), token `ph_…`, TLS verify, poll interval.  
4. Check HA devices: fleet **Plugin** must be **0.1.4**. **Visit** is the host page. **Open host / Docker / backups / services / jobs / audit / alerts** are **buttons** (not sensors — sensors opened HA history). Press a button → HA notification with a PiHerder markdown link. Hardware is the stored Pi/DMI/HA chassis string; model is `os_pretty` (Ubuntu 24.04, HAOS). Recreate **web** so Alembic **044** runs and host facts persist.

Token without `read`, a bad secret, or a mismatched allowlist **fails closed**.

## What it reads

`GET /api/v1/health`, `GET /api/v1/summary` (when present), `GET /api/v1/servers`, `GET /api/v1/jobs?active_only=true`.

Poll is **database snapshots only**. It never SSH’s the fleet on the HA interval. “Host down” is **`last_seen` age**, not a live ping.

## What it will not do (Slice 1)

No start/stop, Move, compose write, Files, console, OS apply, or backup-from-HA. Those stay in PiHerder. YAML `rest:` remains possible.

## Related

- [API tokens](../operations/api-tokens.md) · [API.md](https://github.com/bjorngluck/piherder/blob/v1.6.0-dev/docs/API.md)  
- [HAOS hosts](../day-to-day/haos-hosts.md) (path 1)  
- Maintainer: [PLAN_v1.6.0.md](https://github.com/bjorngluck/piherder/blob/v1.6.0-dev/docs/PLAN_v1.6.0.md) · [FEATURE_PLAN_HOME_ASSISTANT.md](https://github.com/bjorngluck/piherder/blob/v1.6.0-dev/docs/FEATURE_PLAN_HOME_ASSISTANT.md) §7  
