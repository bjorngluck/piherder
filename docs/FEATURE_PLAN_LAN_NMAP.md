# Feature plan — LAN discovery (nmap)

**Status:** **Approved** (2026-07-19) — **N0–N6 product done**; N7 wiki + link/promote; **N8 soft embed** + **N9 tests/E2E shells** + curated options/presets landed; screenshots still open (stream A)
**Ship target:** **v0.8.0** — [PLAN_v0.8.0.md](PLAN_v0.8.0.md) stream **N**  
**Operator wiki:** [wiki/integrations/lan-discovery.md](../wiki/integrations/lan-discovery.md)  
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
| **Web never runs nmap** | Same as backup/patch |
| **Celery executes scans** | Job + progress + cancel |
| **Results in PostgreSQL** | Devices, ports, runs, vulns |
| **Redis for runtime** | Broker, locks, progress, optional view cache |
| **Separate nmap worker** | Privileges + binary + vuln volume isolated |

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
# illustrative
celery-worker-nmap:
  profiles: ["nmap"]
  image: ${PIHERDER_NMAP_IMAGE:-piherder:nmap-local}
  # build: Dockerfile target `nmap` or Dockerfile.nmap
  command: celery -A app.celery_app.celery worker -Q nmap --concurrency=1 ...
  volumes:
    - ${PIHERDER_NMAP_VULN_PATH:-./piherder_nmap_vuln}:/var/lib/piherder/nmap-vuln
    - ./piherder_data:/data
```

- Default `docker compose up` does **not** start nmap worker.  
- UI shows **scanner offline** without worker heartbeat.  
- Vuln volume empty until operator (or “Update vulnerability database” job) downloads pack.  
- Deep Vulners gated on: flag **and** pack presence.

**Image:** one repo, dedicated target with `nmap` installed; **do not** bake Vulners JSON/CVE DBs into layers.

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
| Devices | Auto-created hosts; filter new/linked/ignored/stale |
| **Network view** | Subnet/graph; discovered vs linked servers |
| Device detail | Ports, services, vulns, link/promote/dismiss, deep scan |
| Runs / Jobs | History + progress |
| Later | Server chips, fabric dots, wizard prefill, transition notifications |

Capture policy: light theme, desktop default ([screenshots README](../wiki/assets/screenshots/README.md)).

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
| `NmapDevice` | auto-created; IP/hostname/MAC; state; linked `server_id` |
| Ports / services | Latest snapshot (bounded) |
| `NmapScriptResult` | NSE / Vulners output, optional CVE ids |
| Artifacts | XML under `DATA_ROOT/nmap/…` + retention |

**Identity:** MAC when present; else IP + merge UI for DHCP churn.  
**Retention:** latest ports per device + last N run summaries; raw XML TTL configurable.  
**Platform alignment:** Jobs/Audit DB purge and entity cascades live in RC3 stream **R** ([PLAN_v0.8.0.md](PLAN_v0.8.0.md) § R) — nmap run rows + `DATA_ROOT/nmap/runs/*.xml` should honor the same operator-visible retention settings (or a dedicated “nmap artifacts days” knob defaulting to 30).

---

## 9. Network view

| External tool | Fit |
|---------------|-----|
| Zenmap Topology | Inspiration only (not embeddable) |
| Scanopy | Reference; do not vendor |
| Netdisco | Different domain (SNMP) |

**0.8 MVP:** in-house subnet/group or simple graph from Postgres (optional Redis snapshot). Node types: discovered, linked server, ignored. Click → device detail.

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
| **N7** | Promote/link/dismiss + audit + **wiki/ADMIN** + screenshots | **Partial** — link/ignore/promote + wiki; **screenshots open** (stream A) |
| **N8** | Soft embed into existing views | **Done** — server list LAN chip + server detail discovery card |
| **N9** | Coverage gate + E2E green | **Mostly done** — unit options/classify/embed + `e2e/test_nmap_lan.py` shells; no live scan in CI |

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
- [ ] Screenshots for network view + discovery — stream **A**

---

## 14. Open questions

| # | Topic | Status | Lean |
|---|-------|--------|------|
| 1 | Integration type `nmap` | **Done** | Catalog integration type |
| 2 | Exact Dockerfile layout | **Done** | `Dockerfile.nmap` |
| 3 | SYN vs connect default | **Locked lean** | Prefer SYN when privileged; connect fallback; per-schedule override |
| 4 | IPv6 in 0.8 | Open | Out unless cheap |
| 5 | Device identity | Lean | MAC when present; else IP; merge UI later |
| 6 | Vuln fetch tooling | **Done** | Volume + `nmap_vuln_db_update` (vulscan / exploit-db style pack) |
| 7 | Deep vuln must for tag? | Lean | **Should**; discovery+inventory+detailed+view **must** |

---

## 15. Changelog

| Date | Note |
|------|------|
| 2026-07-19 | Skeleton opened with v0.8.0 kickoff |
| 2026-07-19 | **Approved:** separate worker, vuln volume for Vulners, intensity ladder, multi-schedule, network view, high test bar |
| 2026-07-19 | Retention note: nmap artifacts align with platform stream **R** (Jobs/Audit 30d opt-in; entity cascades) |
| 2026-07-19 | **N1–N6 landed:** UI, schedules **edit**, network view, deep/vuln pack, host-network worker, Jobs progress; **N7** wiki; N8–N9 open |
| 2026-07-19 | **N8–N9 + polish:** script presets (none/cpe/offline/full), curated timing/ports/UDP, script classify UI, server soft embed, unit + E2E shells |

---

**End of plan** — living until freeze; ship narrative in `RELEASE_v0.8.0.md` at tag.
