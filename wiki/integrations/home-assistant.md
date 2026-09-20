# Home Assistant → PiHerder (HACS)

## What this is

A **HACS integration that runs on Home Assistant** and **observes** your PiHerder fleet over `/api/v1`. It is **not** the same as [HAOS hosts](../day-to-day/haos-hosts.md) (PiHerder managing the appliance over SSH).

| Arrow | Where it lives | What it does |
|-------|----------------|--------------|
| Path 1 | This PiHerder image | SSH + `ha` CLI on an HAOS **server** |
| Path 2 (this page) | Separate HACS repo | HA polls PiHerder snapshots; **Visit** opens the herder |

The plugin is **not** inside the PiHerder Docker image. GitHub: [bjorngluck/piherder-ha](https://github.com/bjorngluck/piherder-ha). Current plugin **0.2.0**.

## Why it exists

YAML `rest` sensors against an API token already work. Path 2 is that, first-class: config flow, fleet sensors, one HA device per host. The door back to PiHerder is HA’s **Visit** on the device (the host page). Status sensors are status only — not fake “open” / “press here” buttons (those opened HA history, not PiHerder).

## Install (Slice 1)

1. In PiHerder: Settings → **API management** → create a token with **`read` only**. Set the **IP allowlist** to the HA host (HAOS ≈ appliance LAN IP; a container HA may egress as a Docker/bridge IP).  
2. HACS → custom repository → **Integration** → `https://github.com/bjorngluck/piherder-ha`.  
3. Add **PiHerder**: base URL (your herder origin, including scheme), token `ph_…`, TLS verify, poll interval. A bad URL or token keeps the fields filled (plugin ≥ 0.1.3).  
4. Confirm fleet **Plugin** is **0.2.0**. Device page **Visit** is still the host. For totals and per-host links, add the **PiHerder fleet** card (below).

HACS does **not** auto-refresh custom repos. New GitHub Release: HACS → PiHerder → **⋮ → Redownload** (pick the tag) → **restart Home Assistant**. Reload of the config entry is not enough for new files.

Token without `read`, a bad secret, or a mismatched allowlist **fails closed**.

## What it reads

`GET /api/v1/health`, `GET /api/v1/summary`, `GET /api/v1/servers`, `GET /api/v1/jobs?active_only=true`.

Poll is **database snapshots only**. It never SSH’s the fleet on the HA interval. “Host down” is **`last_seen` age**, not a live ping.

Host **hardware**, **OS**, CPU, memory, and disk come from the herder **host-facts snapshot**. Recreate **web** so Alembic **044** and **045** apply, then System Info refresh (or wait ~15 minutes).

## Fleet card (dashboard)

The built-in HA **device page** only has one Visit link. For fleet totals and per-host shortcuts, add the **PiHerder fleet** Lovelace card:

1. HACS plugin **0.2.1**, restart HA, then **hard-refresh the browser** (Ctrl+Shift+R).  
2. Dashboard → Add card → search **PiHerder fleet**, or YAML:

```yaml
type: custom:piherder-dashboard-card
```

If it still does not list: Dashboard **⋮ → Resources** — delete any `/api/piherder/…` card URL, then **Add**  
`/local/piherder-dashboard-card.js?v=0.2.2` · type **JavaScript module**, then hard-refresh.  
3. The card shows **hosts, CPU cores, containers, memory %, disk %** for the whole fleet, then each host. Expand a host for CPU/memory/disk bars and links: Host, Docker, Backups, Alerts, Audit.

Numbers come from the herder **host-facts** snapshot (about every 15 minutes). Recreate **web** so Alembic **045** is applied, then System Info refresh icon once per host (or wait for the scheduler).

## What you see in HA

| Surface | Meaning |
|---------|---------|
| Fleet device | Counts, herder version, **Plugin** version, Visit = herder origin |
| Host device | One per PiHerder server. **Visit** = host page. Hardware / OS from the stored snapshot |
| Shortcuts | On **that same host** (not extra devices): attributes **Docker**, **Backups**, **Alerts**, **Audit** — HA draws them as real links, same as Visit. Docker/Backups only if the feature is on |
| Sensors | OS, features, alert status, last seen, reboot, last backup — all on the host |

## What it will not do (Slice 1)

No start/stop, Move, compose write, Files, console, OS apply, or backup-from-HA. Those stay in PiHerder. YAML `rest:` remains possible.

## Related

- [API tokens](../operations/api-tokens.md) · [API.md](https://github.com/bjorngluck/piherder/blob/v1.6.0-dev/docs/API.md)  
- [HAOS hosts](../day-to-day/haos-hosts.md) (path 1) · [Add a server](../day-to-day/add-server.md) (System Info snapshot)  
- Maintainer: [PLAN_v1.6.0.md](https://github.com/bjorngluck/piherder/blob/v1.6.0-dev/docs/PLAN_v1.6.0.md) · [FEATURE_PLAN_HOME_ASSISTANT.md](https://github.com/bjorngluck/piherder/blob/v1.6.0-dev/docs/FEATURE_PLAN_HOME_ASSISTANT.md) §7  
