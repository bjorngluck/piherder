# Feature plan — LAN discovery (nmap)

**Status:** **Approved** (2026-07-19) — **N0–N10 product complete** on main: worker, devices, schedules, presets, soft embed, **kind heuristics + override**, **map identity** (name / gateway role), **known/new lifecycle**, **MAC identity / DHCP**, **Hosts map dual layout** (radar toggle, compact 1:1 fit, one-line chrome), **centered edit modal**. **v0.9 chrome:** Devices **List | Map** (merged former Network tab); server LAN chip `return=server:{id}`; Overview shortcuts trimmed; Schedules/Runs mobile cards — [PLAN_v0.9.0.md](PLAN_v0.9.0.md) · operator wiki [lan-discovery.md](../wiki/integrations/lan-discovery.md). Screenshots may lag chrome.
**Ship target:** **v0.8.0** — [PLAN_v0.8.0.md](PLAN_v0.8.0.md) stream **N**  
**Operator wiki:** [wiki/integrations/lan-discovery.md](../wiki/integrations/lan-discovery.md) · [dns-fabric.md](../wiki/integrations/dns-fabric.md)  
**Related:** [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · [FEATURE_PLAN_RUNTIME_TOPOLOGY.md](FEATURE_PLAN_RUNTIME_TOPOLOGY.md) · [FEATURE_PLAN_HOST_LIFECYCLE.md](FEATURE_PLAN_HOST_LIFECYCLE.md) · [ADMIN.md](ADMIN.md) · [SPEC.md](../SPEC.md)

This document owns **product + technical design** for LAN discovery. The cycle plan owns ship bar and sequencing only.

### Locked packaging (2026-07-19)

| Decision | Value |
|----------|--------|
| Runtime | **Separate worker container** `celery-worker-nmap` (compose profile `nmap`, opt-in) |
| Image | **Dedicated image target** (Dockerfile multi-target / `Dockerfile.nmap`) — same app code; main web/celery stay nmap-free |
| Vuln / Vulners data | **Host volume mount** for downloaded artefacts; supports **full Vulners-style scans** when pack present |
| Default ship | No nmap worker, **no** vuln DB blobs in images |
| Queue | Celery queue `nmap`, concurrency **1** default |

---

## 1. Problem

Operators manage a known fleet in PiHerder but do not see the broader LAN without external tools. Discovery should answer: *what else is on my configured networks, and which of those do I want to manage?*

---

## 2. Product principles (locked)

| Principle | Decision |
|-----------|----------|
| Opt-in only | No silent full-net scans; operator configures CIDR(s) and triggers (manual + optional schedule) |
| **Auto-create discovery records** | Scan results **persist as first-class discovered devices** — not a transient job log only |
| **Nmap network view** | Dedicated UI (map/graph or subnet layout), not only a flat table |
| **Manual onboarding where appropriate** | Discovery ≠ managed server. Promote / link / wizard stay **operator-driven** |
| Audit + safety | Preview / confirm where wide-blast; rate limits; no automatic privilege escalation |
| Orthogonal to stack topology | Device discovery is not `RuntimeEdge` / compose deps — may later *display* near fabric |
| Web never scans | HTTP only enqueues; **Celery nmap worker** runs nmap |
| Vuln pack opt-in | **Vulners** / NSE data on **mapped volume**; never baked into default image |

### Explicit non-goals (0.8)

- Replacing Uptime Kuma or inventing a full NMS  
- Wireless site survey / RF tooling  
- Agent install from discovery alone  
- Silent auto-enroll as full managed **Server** rows with SSH/backups enabled  
- DoS/flood or brute-force NSE as product actions  
- Live nmap of real networks in GitHub Actions (fixtures / mock XML only)  
- Shipping full vuln DB in any default image layer  
- Full Zenmap / Scanopy embed  

---

## 3. Mental model

```text
  CIDR(s) + schedules ──► Job (web enqueue) ──► Redis broker
                                                    │
                                                    ▼
                                          celery-worker-nmap (-Q nmap)
                                                    │
                              ┌─────────────────────┼─────────────────────┐
                              ▼                     ▼                     ▼
                     nmap -oX parse          vuln volume            progress keys
                              │              (Vulners pack)           (Redis)
                              ▼
                    Auto-create/update NmapDevice + ports + scripts
                              │
                              ▼
                      Network view + device list
                              │
            ┌─────────────────┼─────────────────┐
            ▼                 ▼                 ▼
         Ignore          Link existing     Promote / wizard
         (dismiss)       Server row        → managed Server
                                            (manual, audited)
```

| Entity | Meaning |
|--------|---------|
| **Integration type `nmap`** | Catalog entry: enablement, CIDRs, excludes, vuln flags |
| **Scan schedule** | Named intensity + cron/interval + scope (multiple allowed) |
| **Scan run** | One execution; linked to `Job`; summary + artifact path |
| **Discovery device** | Auto-created host (IP, hostname, MAC, ports, state) |
| **Link** | Optional FK to existing PiHerder `Server` |
| **Promote** | Operator starts wizard / add-server prefilled — no silent SSH enable |

---

## 4. Nmap capability map

Source themes: [Recorded Future — top Nmap commands](https://www.recordedfuture.com/threat-intelligence-101/tools-and-techniques/nmap-commands).

| # | Capability | Typical flags | Product use |
|---|------------|---------------|-------------|
| 1 | Single host | `nmap <ip>` | On-demand device scan |
| 2 | Host discovery | `-sn` on CIDR | **Discovery** schedule |
| 3 | Full / large port range | `-p-` | **Detailed** + per-IP deep |
| 4 | Specific ports | `-p 80,443` | Presets |
| 5 | CIDR / ranges | `192.168.1.0/24` | LAN profiles (primary) |
| 6 | Top ports | `--top-ports N` | **Inventory** schedule |
| 7 | Target list | `-iL` | Worker-built target files |
| 8 | Structured out | **`-oX`** | Parse → DB |
| 9 | Skip reverse DNS | `-n` | Fast LAN option |
| 10 | Aggressive | `-A -T4` | **Detailed** (expensive) |
| 11 | Service/version | `-sV` | Inventory / detailed / deep |
| 12 | TCP / UDP | `-sS`/`-sT`, `-sU` | TCP default; UDP opt-in |
| 13 | Vuln + **Vulners** | `--script vuln` / vulners + **volume DB** | Deep / on-demand if pack ready |
| 14–16 | Flood / brute / malware external | — | **Out** |

### Intensity ladder

| Profile | Intent | Default cadence | Typical shape |
|---------|--------|-----------------|---------------|
| **Discovery** | Who is alive | Every few hours / daily | `-sn` (+ light ports if ping blocked) |
| **Inventory** | Ports + services | Daily | `--top-ports 100` + `-sV` |
| **Detailed** | Broad map + OS/service | Weekly | large/`-p-` + `-sV` (+ `-O` if caps) |
| **Deep (on-demand)** | Single-IP full audit | Manual only | Full ports + `-sV` + optional Vulners if pack on |

---

## 5. Architecture

### 5.1 Hard rules

| Rule | Why |
|------|-----|
| **Web never runs nmap** | Same as backup/patch; `PIHERDER_NMAP_WORKER=0` + no binary |
| **Celery executes scans** | Only **queue `nmap`** on **celery-worker-nmap** |
| **Results in PostgreSQL** | Devices, ports, runs, vulns |
| **Redis for runtime** | Broker, locks, progress, optional view cache |
| **Separate nmap worker** | Privileges + binary + vuln volume + fence marker isolated |

### 5.2 Deploy shape (**locked**)

```text
  web (default image) ──enqueue──► Redis ──► celery-worker-nmap (profile nmap)
                                                   │
                                                   ├── volume: vuln artefacts (Vulners)
                                                   ├── DATA_ROOT: scan XML artefacts
                                                   ▼
                                              PostgreSQL
```

**Compose lean:**

```yaml
# illustrative — see repo docker-compose.yml
x-piherder-app-env:
  PIHERDER_NMAP_WORKER: "0"   # web + main celery — tasks refuse to scan
celery-worker-nmap:
  profiles: ["nmap"]
  image: ${PIHERDER_NMAP_IMAGE:-piherder:nmap-local}
  environment:
    PIHERDER_NMAP_WORKER: "1"  # only allowed executor
  command: celery -A app.celery_app.celery worker -Q nmap --concurrency=1 ...
  volumes:
    - ${PIHERDER_NMAP_VULN_PATH:-./piherder_nmap_vuln}:/var/lib/piherder/nmap-vuln
    - ./piherder_data:/data
```

- Default `docker compose up` does **not** start nmap worker.  
- **`worker_guard`:** refuse scan/vuln tasks if `PIHERDER_NMAP_WORKER=0` or `nmap` binary missing (misrouted queue).  
- Fence is compose/image-owned; documented in [`.env.example`](../.env.example) (usually **not** set in operator `.env`).  
- UI shows **scanner offline** without worker heartbeat.  
- Vuln volume empty until operator (or “Update vulnerability database” job) downloads pack.  
- Deep Vulners gated on: flag **and** pack presence.

**Image:** one repo, dedicated `Dockerfile.nmap` with `nmap` + `PIHERDER_NMAP_WORKER=1`; **do not** bake Vulners JSON/CVE DBs into layers.

### 5.3 Job flow

1. Operator or APScheduler enqueues.  
2. Create `Job` (`nmap_discover` / `nmap_inventory` / `nmap_detailed` / `nmap_host_deep` / `nmap_vuln_db_update`).  
3. `apply_async(..., queue="nmap")`.  
4. Task: Redis lock → progress → nmap → parse XML → upsert DB → finish.  
5. UI: Jobs page / HTMX (existing patterns).  
6. Cancel: revoke + kill nmap child when safe.

### 5.4 Redis keys (illustrative)

| Key | Role |
|-----|------|
| `piherder:nmap:lock:cidr:{hash}` | Overlapping LAN sweeps |
| `piherder:nmap:lock:host:{ip}` | Per-IP deep serialize |
| `piherder:nmap:progress:{job_id}` | Phase / percent |
| `piherder:nmap:worker:heartbeat` | Scanner online chip |
| `piherder:nmap:view:snapshot` | Optional short-TTL view JSON |

---

## 6. UX surfaces

| Surface | Content |
|---------|---------|
| Integrations → **LAN Discovery** | Enable, worker status, vuln pack status, CIDRs |
| Schedules | Multiple named schedules (intensity + cron/interval) |
| Devices | Auto-created hosts; **List | Map** views; filter new/known/linked/ignored/offline |
| **Map view** (under Devices) | Subnet cards; **Show unlinked**; click → **centered edit modal** (legacy `?tab=network`) |
| Edit modal | Map name, kind override, gateway role, Mark known/new, ignore, link, promote, ports |
| **Hosts map** (`/dns/physical`) | Fleet + unlinked discoveries; radar toggle; dual compact/full; **1:1** fit |
| Device detail | Ports, services, vulns, link/promote/dismiss, deep scan (also via modal) |
| Runs / Jobs | History + progress |
| Soft embed | Server list LAN chip + server detail card |
| Later / roadmap | Kind **icons/shapes** on map; per-service port labels |

Capture policy: light theme, desktop default ([screenshots README](../wiki/assets/screenshots/README.md)).

### 6.1 Map identity & lifecycle (shipped)

| Concept | Behaviour |
|---------|-----------|
| **display_name** | Operator map label; priority over hostname/IP on chips |
| **kind_override** | Sticky type when heuristics wrong; empty = Auto |
| **map_role=gateway** | Router spine label + network gateway IP; not drawn as outer chip |
| **new → known** | Mark known, or auto on Save map identity; rescans keep new until reviewed |
| **MAC identity** | `mac:…` preferred; DHCP IP updates row in place |
| **Hosts chrome** | Radar (disc on/off), −/%/+, **1:1** (full canvas vs compact fit), fullscreen |

---

## 7. Scheduling

**Multiple schedules** supported; all **off by default**.

| Example | Intensity | Cadence | Target |
|---------|-----------|---------|--------|
| `lan-discovery` | Discovery | 6h / daily | All CIDRs |
| `lan-inventory` | Inventory | Daily | CIDR / live hosts |
| `lan-detailed` | Detailed | Weekly | CIDR / known devices |
| Deep vuln | Deep | Manual **or** scheduled (opt-in) | CIDR / deep intensity |

**On-demand:** scan network now; scan this device (full ports / services / optional vuln); rescan selection.

**Edit path (shipped):** list → **Edit** → same form prefilled (`?schedule=ID`) → POST `…/schedules/{id}/edit`. Per-schedule options in `options_json` (e.g. `vuln_scripts` on deep, `use_syn` override vs inherit integration).

Guardrails: max concurrent LAN-wide = 1; skip if previous running; audit schedule changes; targets must be inside configured CIDR allowlist.

---

## 8. Data model (draft)

| Entity | Purpose |
|--------|---------|
| `Integration` type `nmap` | Enablement + `config_json` (CIDRs, excludes, flags) |
| `NmapScanSchedule` | name, intensity, cron/interval, enabled, scope |
| `NmapScanRun` | job_id, schedule_id?, intensity, targets, summary, artifact path |
| `NmapDevice` | auto-created; IP/hostname/MAC; state; linked `server_id`; `display_name`; `kind_override`; `map_role` |
| Ports / services | Latest snapshot (bounded) |
| `NmapScriptResult` | NSE / Vulners output, optional CVE ids |
| Artifacts | XML under `DATA_ROOT/nmap/…` + retention |

**Identity (shipped):** MAC when present (`mac:…`); else IP (`ip:…`). Same MAC at new DHCP IP updates `ip_address` in place (name/kind/state kept). First MAC at an IP-only row upgrades identity.  
**Retention:** latest ports per device + last N run summaries; raw XML TTL configurable.  
**Platform alignment:** Jobs/Audit DB purge and entity cascades live in RC3 stream **R** ([PLAN_v0.8.0.md](PLAN_v0.8.0.md) § R) — nmap run rows + `DATA_ROOT/nmap/runs/*.xml` honor opt-in stale cleanup.

---

## 9. Network view

| External tool | Fit |
|---------------|-----|
| Zenmap Topology | Inspiration only (not embeddable) |
| Scanopy | Reference; do not vendor |
| Netdisco | Different domain (SNMP) |

**0.8 MVP (shipped) + 0.9 chrome:** subnet-grouped **Devices → Map** cards + end-to-end Hosts map overlay (outer chips, dual compact/full layout). Node types: discovered, linked server, ignored, gateway spine. Click Map card or List row → edit modal; Hosts chip → device detail (`return=hosts`); server LAN chip → same modal (`return=server:{id}`).

---

## 10. Security & ops

- Opt-in copy: active recon on **your** LAN only.  
- No flood/brute product actions.  
- Vuln pack off by default; volume-mounted; “Update vuln DB” job on nmap worker.  
- Document `NET_RAW` / `NET_ADMIN` for SYN/OS; fallback connect-scan.  
- Refuse targets outside configured scopes.  
- RBAC: operator+ mutate; viewer read.  
- Audit: configure, scan, promote, dismiss, vuln-DB update.  
- CI: fixtures only — no live scan, no Vulners download.

---

## 11. Testing bar (this feature)

Aim for **high unit + E2E coverage** of nmap surfaces (stronger than global ~50% bar for *this* package).

| Layer | Scope |
|-------|--------|
| Unit | XML parse, upsert/identity, allowlist, intensity → argv, vuln-pack gate, locks |
| Job | Enqueue, mocked subprocess success, cancel, lock skip |
| HTTP | AuthZ, enqueue, list filters |
| E2E | Devices + network view shells; viewer cannot start scan; stub worker OK |
| Out of CI | Real interface scan, real Vulners pull |

---

## 12. Implementation phases

| Phase | Deliverable | Status |
|-------|-------------|--------|
| **N0** | This plan approved + docs committed | **Done** |
| **N1** | Models + migrations + parse/upsert + fixtures | **Done** |
| **N2** | nmap image + profile + host-network worker + vuln volume + queue | **Done** |
| **N3** | Integration UI: setup, devices, runs, on-demand | **Done** |
| **N4** | Multiple schedules + **create/edit** + APScheduler sync + options_json | **Done** |
| **N5** | Network view MVP | **Done** (subnet groups) |
| **N6** | Per-IP deep + vuln pack update job + Jobs progress; deep NSE (vuln+vulscan, no double vulners) | **Done** |
| **N7** | Promote/link/dismiss + audit + **wiki/ADMIN** + screenshots | **Partial** — product + wiki/ADMIN done; **screenshots open** (stream A) |
| **N8** | Soft embed into existing views | **Done** — server list LAN chip + server detail discovery card |
| **N9** | Coverage gate + E2E green | **Mostly done** — unit options/classify/embed/map-identity + `e2e/test_nmap_lan.py` shells; no live scan in CI |
| **N10** | Map identity polish | **Done** — kind override, gateway role, known/new, MAC/DHCP, Hosts dual layout + chrome; icons/shapes + per-service labels **roadmap** |

**Also shipped with N (ops hardening):** root nmap worker for reliable inventory; hostname/MAC via host net + DNS; schedule SYN/vuln options; web mounts vuln volume **:ro** for Overview pack status.

MVP product slice: **N1–N6**; docs wiki **N7 partial**; screenshots + E2E = tag bar with stream **A/Q**.

---

## 13. Acceptance

- [x] Configure LAN CIDR(s); run discover/inventory/detailed jobs  
- [x] Multiple schedules (create + **edit**; discovery daily, detailed weekly, optional deep)  
- [x] On-demand network + per-IP deep  
- [x] Auto-created devices in DB + list + **network view**  
- [x] Manual promote/link/dismiss shell; audit on key actions  
- [x] Separate nmap worker via compose profile; default compose without it  
- [x] Vuln volume mapped; deep vuln scripts when pack present + enabled  
- [x] Operator wiki ([lan-discovery.md](../wiki/integrations/lan-discovery.md)) + ADMIN notes  
- [x] High unit + E2E shells (fixtures only); no live scan in CI — **N9**  
- [x] Soft embed: fleet list + host detail (N8)  
- [x] Curated options + deep script presets + script result classification  
- [x] Map identity: name, kind override, gateway role, known/new, MAC/DHCP  
- [x] Hosts map dual layout + radar chrome + 1:1 compact fit  
- [x] Network centered edit modal (save and close, scroll restore)  
- [ ] Screenshots for network view + discovery — stream **A**

---

## 14. Open questions

| # | Topic | Status | Lean |
|---|-------|--------|------|
| 1 | Integration type `nmap` | **Done** | Catalog integration type |
| 2 | Exact Dockerfile layout | **Done** | `Dockerfile.nmap` |
| 3 | SYN vs connect default | **Locked lean** | Prefer SYN when privileged; connect fallback; per-schedule override |
| 4 | IPv6 in 0.8 | Open | Out unless cheap |
| 5 | Device identity | **Done lean** | MAC when present; else IP; DHCP updates in place; no merge UI |
| 6 | Vuln fetch tooling | **Done** | Volume + `nmap_vuln_db_update` (vulscan / exploit-db style pack) |
| 7 | Deep vuln must for tag? | Lean | **Should**; discovery+inventory+detailed+view **must** |

---

## 15. Changelog

| Date | Note |
|------|------|
| 2026-07-21 | **Docs:** `.env.example` + wiki env-reference / install / architecture / lan-discovery / volumes document `PIHERDER_NMAP_WORKER` fence |
| 2026-07-21 | **P0/P1 review follow-up:** worker_guard + compose fence; Show unlinked; Hosts return path; lifecycle close; promote prefill; stale 14d; config split (device_ops / fabric_projection); detailed/deep confirm; P2 on roadmap only |
| 2026-07-21 | **Docs pass:** wiki (modal, Hosts chrome, 1:1 fit), ADMIN, PLAN/ROADMAP; N10 product complete bar |
| 2026-07-21 | **Hosts map chrome:** radar disc toggle (count in footer), one-line tools, **1:1** fits compact when disc off; dual-layout gateway clip |
| 2026-07-21 | **Network UX:** centered edit modal; Save and close + scroll restore; Mark known/new |
| 2026-07-21 | **Mark known / Mark new** UI + auto-known on map-identity save; DHCP: MAC identity updates IP |
| 2026-07-21 | **Map identity:** `kind_override` + `map_role=gateway` (router spine + network gateway IP); sticky type when heuristics bust; roadmap icons/shapes + service labels |
| 2026-07-19 | Skeleton opened with v0.8.0 kickoff |
| 2026-07-19 | **Approved:** separate worker, vuln volume for Vulners, intensity ladder, multi-schedule, network view, high test bar |
| 2026-07-19 | Retention note: nmap artifacts align with platform stream **R** (Jobs/Audit 30d opt-in; entity cascades) |
| 2026-07-19 | **N1–N6 landed:** UI, schedules **edit**, network view, deep/vuln pack, host-network worker, Jobs progress; **N7** wiki; N8–N9 open |
| 2026-07-19 | **N8–N9 + polish:** script presets (none/cpe/offline/full), curated timing/ports/UDP, script classify UI, server soft embed, unit + E2E shells |

---

**End of plan** — living until freeze; ship narrative in `RELEASE_v0.8.0.md` at tag.
