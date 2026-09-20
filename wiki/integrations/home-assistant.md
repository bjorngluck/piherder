# Home Assistant → PiHerder (HACS)

## What this is

A **HACS integration that runs on Home Assistant** and **observes** your PiHerder fleet over `/api/v1`. It is **not** the same as [HAOS hosts](../day-to-day/haos-hosts.md) (PiHerder managing the appliance over SSH).

| Arrow | Where it lives | What it does |
|-------|----------------|--------------|
| Path 1 | This PiHerder image | SSH + `ha` CLI on an HAOS **server** |
| Path 2 (this page) | Separate HACS repo | HA polls PiHerder snapshots; **Visit** opens the herder |

The plugin is **not** inside the PiHerder Docker image. GitHub: [bjorngluck/piherder-ha](https://github.com/bjorngluck/piherder-ha). Current plugin **0.1.5**.

## Why it exists

YAML `rest` sensors against an API token already work. Path 2 is that, first-class: config flow, fleet sensors, one HA device per host. The door back to PiHerder is HA’s **Visit** on the device (the host page). Status sensors are status only — not fake “open” / “press here” buttons (those opened HA history, not PiHerder).

## Install (Slice 1)

1. In PiHerder: Settings → **API management** → create a token with **`read` only**. Set the **IP allowlist** to the HA host (HAOS ≈ appliance LAN IP; a container HA may egress as a Docker/bridge IP).  
2. HACS → custom repository → **Integration** → `https://github.com/bjorngluck/piherder-ha`.  
3. Add **PiHerder**: base URL (your herder origin, including scheme), token `ph_…`, TLS verify, poll interval. A bad URL or token keeps the fields filled (plugin ≥ 0.1.3).  
4. Confirm fleet **Plugin** is **0.1.5**. On a host device, **Visit** opens `{origin}/servers/{id}`.

HACS does **not** auto-refresh custom repos. New GitHub Release: HACS → PiHerder → **⋮ → Redownload** (pick the tag) → **restart Home Assistant**. Reload of the config entry is not enough for new files.

Token without `read`, a bad secret, or a mismatched allowlist **fails closed**.

## What it reads

`GET /api/v1/health`, `GET /api/v1/summary`, `GET /api/v1/servers`, `GET /api/v1/jobs?active_only=true`.

Poll is **database snapshots only**. It never SSH’s the fleet on the HA interval. “Host down” is **`last_seen` age**, not a live ping.

Host **hardware** and **OS** (`os_pretty`, Ubuntu vs HAOS vs Debian) come from the herder **host-facts snapshot** (System Info on the server — not live SSH every poll). Recreate **web** so Alembic **044** is applied, then open System Info once (or wait ~15 minutes).

## What you see in HA

| Surface | Meaning |
|---------|---------|
| Fleet device | Counts, herder version, **Plugin** version, Visit = herder origin |
| Host device | One per PiHerder server. **Visit** = that host. **Hardware** = Pi / DMI / HA chassis. **Model** = stored OS pretty name |
| Sensors | OS, features, alert **status**, last seen, reboot, last backup (`never` if none) |
| Jobs / alerts | Counts and titles. Open Jobs / Audit / Docker **in PiHerder** after Visit |

## What it will not do (Slice 1)

No start/stop, Move, compose write, Files, console, OS apply, or backup-from-HA. Those stay in PiHerder. YAML `rest:` remains possible.

## Related

- [API tokens](../operations/api-tokens.md) · [API.md](https://github.com/bjorngluck/piherder/blob/v1.6.0-dev/docs/API.md)  
- [HAOS hosts](../day-to-day/haos-hosts.md) (path 1) · [Add a server](../day-to-day/add-server.md) (System Info snapshot)  
- Maintainer: [PLAN_v1.6.0.md](https://github.com/bjorngluck/piherder/blob/v1.6.0-dev/docs/PLAN_v1.6.0.md) · [FEATURE_PLAN_HOME_ASSISTANT.md](https://github.com/bjorngluck/piherder/blob/v1.6.0-dev/docs/FEATURE_PLAN_HOME_ASSISTANT.md) §7  
