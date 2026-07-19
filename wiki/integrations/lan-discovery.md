# LAN Discovery (nmap)

## What this is

**LAN Discovery** is an **opt-in** Catalog integration that scans your configured **CIDR(s)** with **nmap**, auto-creates **discovered device** records, and shows a **network view**. Devices are **not** managed fleet servers until you **link** or **promote** them.

**Where:** Catalog → **Integrations** → add / open **LAN Discovery** (`/integrations/{id}?tab=…`).

## Why it exists

PiHerder already manages hosts you onboarded. Discovery answers: *what else is on my LAN, and which of those do I want to manage?* Scans never run silently: you configure CIDRs and start work manually or via **schedules you enable**.

---

## Prerequisites

| Requirement | Notes |
|-------------|--------|
| **nmap worker** | Compose **profile `nmap`** — not started by default |
| **nmap image** | Build: `docker build -f Dockerfile.nmap -t piherder:nmap-local .` |
| **Start worker** | `docker compose --profile nmap up -d celery-worker-nmap` |
| **Vuln pack volume** | Host dir `./piherder_nmap_vuln` (or `PIHERDER_NMAP_VULN_PATH`) mounted into web (ro) + nmap worker (rw) |
| **Host networking** | Worker uses **host network** so ARP/MAC and LAN reachability work; Postgres/Redis must listen on host loopback (`127.0.0.1`) as in stock compose |

Without the worker, Overview shows **scanner offline**. Without the vuln pack, deep **vuln scripts** stay gated.

!!! warning "Active recon"
    Scans are intentional reconnaissance on **your** networks only. Stay inside configured CIDRs. Do not point PiHerder at networks you do not own.

---

## End-to-end: first discovery week

1. Build and start the nmap worker (commands above).  
2. Catalog → Integrations → **Add** → **LAN Discovery** → set **CIDR(s)** (e.g. `192.168.1.0/24`) and optional excludes.  
3. Optional: enable **vuln scripts** on the integration when you want deep scans to use NSE vuln packs.  
4. **Overview** → download / update **vulnerability database** if you plan deep vuln scans (Jobs page shows progress).  
5. Run **Discovery** (or Inventory / Detailed) from the UI.  
6. Open **Devices** and **Network** tabs; link or promote hosts you care about.  
7. Optional: **Schedules** → create discovery daily / inventory weekly — leave **disabled** until you trust the first manual runs; use **Edit** to change options later.

---

## Tabs

| Tab | Purpose |
|-----|---------|
| **Overview** | Worker status, CIDRs, vuln pack status, quick scan actions, pack update |
| **Devices** | Auto-created hosts (IP, hostname, MAC, ports); filter / link / ignore / promote |
| **Network** | Subnet-oriented view of discovered vs linked devices |
| **Schedules** | Multiple named schedules (intensity + cron/interval + options) — create **and edit** |
| **Runs** | Scan run history (linked to Jobs) |

---

## Scan intensities

| Profile | Intent | Typical use |
|---------|--------|-------------|
| **Discovery** | Who is alive | Frequent light sweeps (`-sn`-class) |
| **Inventory** | Ports + services | Daily top-ports + version detect |
| **Detailed** | Broader map | Weekly wider ports / OS-ish depth |
| **Deep** | Single-host full audit | Manual or scheduled; optional **script preset** + SYN |

**On-demand:** scan network now; scan **this device** (deep); curated options (timing, top-ports, UDP, port list, script preset) — **no free-form nmap flags**.

### Deep script presets

| Preset | What runs | When to use |
|--------|-----------|-------------|
| **none** | No NSE vuln scripts | Inventory-like deep ports only |
| **cpe** | Stock `vulners` (CPE/version, online API) | Quieter version triage |
| **offline** | Pack **vulscan** only | Offline tables; needs pack |
| **full** | Stock `vuln` category + vulscan + helpers | Noisy; many clear/error rows expected |

Device detail **classifies** script rows: **finding** · **clear** · **script error** · **info**. Each finding shows the **port/service** it ran on (or was inferred for from CPE/product). Ports with issues are **highlighted** in the port table. Errors mean the probe failed (often irrelevant apps), not “unknown vulnerability”. Version/CPE matches still need human verification.

### Timing (nmap `-T`)

| Value | Meaning | When |
|-------|---------|------|
| **T3** | Normal — slower, quieter | Fragile IoT, WAN edges |
| **T4** | Aggressive — default | Typical home/lab LAN |
| **T5** | Insane — fastest | Speed over thoroughness; may miss or stress hosts |

### Port scope (inventory / detailed / deep)

| Mode | nmap shape | Notes |
|------|------------|--------|
| **Top ports** | `--top-ports N` (default 100) | Fast inventory of common services |
| **All ports** | `-p-` | Full TCP 1–65535 — slower; use when top-N is not enough |
| **Custom list** | `-p 22,80,443` or ranges | Curated only (digits, commas, hyphens) |

**Detailed** and **deep** default to all ports unless you pick top or custom.

### Targets & excludes

- **Scan now** pre-fills **configured LAN CIDR(s)** so you do not re-type the subnet.
- **Always exclude** — every intensity (including discovery).
- **Exclude from port/vuln scans** — inventory / detailed / deep skip these hosts; **discovery still finds them**.
- **Exclude from deep only** — inventory can still map ports; deep/vuln skips them.

Excludes are passed as nmap `--exclude` so a single IP does not block scanning the rest of the CIDR.

### Network tab — open ports

Port chips show the **latest snapshot per host** (from the last inventory/detailed/deep that recorded ports), **not** a merge of all historical scans. Discovery no longer clears a prior port snapshot.

### Soft embed (fleet)

Linked discovery devices appear on **Servers** list (LAN chip) and **server detail** (ports + script summary + links back to Devices / Network view).

---

## Schedules

- All schedules are **off by default**.  
- Intensities: discovery · inventory · detailed · **deep** (deep may set **vuln scripts** and SYN override).  
- Provide **cron** (5 fields, app timezone) **or** **interval hours**.  
- **Create** and **Edit** (list → Edit → same form prefilled → Save).  
- Options stored per schedule (`options_json`): script preset, timing, top-ports, UDP, port list, SYN vs inherit.  
- Changes resync APScheduler; audit records configure/scan actions.

---

## Devices & onboarding

| Action | Meaning |
|--------|---------|
| **Ignore / dismiss** | Hide from “new” focus without deleting history carelessly |
| **Link** | Attach discovered device to an **existing** Server row |
| **Promote** | Start **add-host wizard** / prefilled path — still **manual**; no silent SSH enable |

Discovery ≠ fleet membership. Hostnames and MACs depend on scan privileges and host-network worker mode.

---

## Vulnerability pack

| Item | Detail |
|------|--------|
| Location | Host volume → `/var/lib/piherder/nmap-vuln` in containers |
| Update | Overview → **Update vulnerability database** → Job `nmap_vuln_db_update` on the **nmap** queue |
| Progress | Jobs page detail (log lines / progress), same pattern as OS updates |
| Gate | Deep vuln scripts need pack **READY** + integration vuln enablement |

---

## Jobs

| Job type | Runner |
|----------|--------|
| `nmap_discover` / `nmap_inventory` / `nmap_detailed` / `nmap_host_deep` | **celery-worker-nmap** (`-Q nmap`) |
| `nmap_vuln_db_update` | nmap worker |

Web only **enqueues**. Cancel and progress follow the fleet Jobs UI (finished jobs keep the modal open with a done banner instead of forcing a full page reload).

---

## Security & ops notes

- RBAC: **operator+** mutate; **viewer** read.  
- Targets outside configured CIDRs are refused.  
- SYN / raw scans need appropriate privileges in the nmap container (stock image runs as root with caps for reliable LAN + inventory root). Connect-scan (`-sT`) is the fallback.  
- Default install: **no** nmap worker, **no** vuln DB in image layers.  
- CI never live-scans real networks (fixtures / mock XML only).

---

## Related

- [Integrations overview](overview.md)  
- [Add a server](../day-to-day/add-server.md) — promote path  
- [Jobs, audit & notifications](../day-to-day/jobs-audit-notifications.md)  
- [Settings — Stale data cleanup](../operations/settings.md#stale-data-cleanup) — optional purge of old nmap runs  
- [Volumes](../operations/volumes.md)  
- Design: [FEATURE_PLAN_LAN_NMAP.md](https://github.com/bjorngluck/piherder/blob/main/docs/FEATURE_PLAN_LAN_NMAP.md) · ship plan [PLAN_v0.8.0.md](https://github.com/bjorngluck/piherder/blob/main/docs/PLAN_v0.8.0.md)
