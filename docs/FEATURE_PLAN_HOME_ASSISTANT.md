# Feature plan — Home Assistant integration (architecture + discovery)

**Status:** **Discovery** (opened 2026-07-23) — refine through **v0.9.0**; implement REST / HAOS-native later  
**Ship framing:** [PLAN_v0.9.0.md](PLAN_v0.9.0.md) stream **HA**  
**Related:** [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) § Horizon 3 · [FEATURE_PLAN_INTEGRATIONS.md](FEATURE_PLAN_INTEGRATIONS.md) · [FEATURE_PLAN_HOST_LIFECYCLE.md](FEATURE_PLAN_HOST_LIFECYCLE.md) · [FEATURE_PLAN_LAN_NMAP.md](FEATURE_PLAN_LAN_NMAP.md) · [API.md](API.md) · [SPEC.md](../SPEC.md)

This document owns **architecture, product directions, and open decisions** for Home Assistant (HA). The cycle plan owns ship bar and sequencing only.

---

## 1. Problem / opportunity

Homelab operators often run **HAOS** (or Supervised / Container HA) as a first-class host next to Docker Pis. Today PiHerder:

| Capability | Status |
|------------|--------|
| Manage HA host over SSH (backup / patch caveats) | Yes — least-priv **skipped** for HAOS with guidance |
| Bind Kuma **host service** monitors to HA | Yes (Integrations hub) |
| HA as **client** of PiHerder (token REST) | Yes — [API.md](API.md) · wiki API tokens |
| Mark / fingerprint a node as HA | **No** |
| Enrich fleet UI from HA or Supervisor APIs | **No** |
| HAOS custom component / add-on talking to PiHerder | **No** |

Goal: decide a durable model for **path 1** (PiHerder → HA) and **path 2** (HA → PiHerder), ship only discovery-safe pieces in **v0.9.0**, and reserve deep API + HAOS-native work for **v1.0+**.

---

## 2. Two integration directions

```text
Path 1 — PiHerder → HA (inbound enrichment)
  Server marked / discovered as HA*
       │
       ├─ SSH facts (0.9 spike)      version, install type, hostname
       ├─ Optional REST (later)      /api/ config, status summary, Supervisor add-ons
       └─ UI                         chips, stats strip, dashboard deep links

Path 2 — HA → PiHerder (HAOS-native)
  Custom component / add-on on HAOS
       │
       ├─ Sensors (fleet health, job status)
       ├─ Actions (safe: trigger check, scoped jobs)
       └─ Alerts / automations in HA
  Target: v1.0 or first maintenance after 1.0 — discovery only in 0.9
```

**Today’s asymmetry:** path 2 is partially enabled already via **generic API tokens** (HA `rest` / `rest_command`). Path 1 is the greenfield product work.

---

## 3. Install types (must model explicitly)

| Install | Typical host | SSH | Supervisor API | Notes |
|---------|--------------|-----|----------------|-------|
| **HAOS** | Appliance VM / Pi | root or limited | Yes | Primary fingerprint target |
| **Supervised** | Debian + Supervisor | Yes | Yes | Similar APIs to HAOS |
| **Container** | Docker on managed host | Via host | No Supervisor | Core in container |
| **Core** | venv / bare metal | Yes | No | Dev-like |

Do not assume every “Home Assistant” node exposes Supervisor. Path 1 REST must branch on install type.

---

## 4. Architecture principles (proposed locks)

1. **PiHerder owns fleet truth**; HA owns home automation truth — do **not** mirror all entities into Postgres.  
2. **Opt-in** enrichment; never require HA to use PiHerder.  
3. **Read-mostly** for path 1 API; write actions rare, audited, step-up 2FA where dangerous.  
4. **Secrets:** any HA Long-Lived Access Token (LLAT) stored **Fernet-encrypted** like other integration credentials; included in herder self-backup.  
5. **Bidirectional is two products:** path 1 = server/Catalog enrichment; path 2 = HA component consuming [API.md](API.md).  
6. **No live HA in CI** — fixtures, mocked SSH command output, and pure classifiers only.  
7. Integrations remain **optional** — core fleet ops work alone (same as Kuma/Grafana).

---

## 5. Path 1 — feature catalog

| Idea | Value | Complexity | Candidate version |
|------|-------|------------|-------------------|
| Mark server as HA / HAOS | Operator clarity | Low | **0.9** (HA1) |
| Auto-detect via nmap (8123, hostname) + kind | Discovery chrome | Low–M | **0.9** (HA2) |
| SSH fingerprint (`ha` CLI, os-release, supervisor paths) | Trust without API token | M | **0.9 spike** (HA2/HA3) |
| Store profile on Server | Data model | S | **0.9** |
| Long-lived HA access token (LLAT) | Rich stats | M | **v1.0?** (HA4) |
| Poll Core `GET /api/` + `/api/config` | Version / location chip | M | **v1.0?** |
| Supervisor add-ons list / health | “Installed apps” | M–L | Post-1.0 (HA6) |
| Lovelace / dashboard deep links | Operator shortcuts | S once URL known | v1.0 |
| Entity/state flood into PiHerder | Noise vs value | High | **Avoid** as default |
| Safe actions (restart core, host reboot via Supervisor) | High risk | High | Post-1.0, audited |
| MQTT bridge | Ops + IoT merge | High | Later |

### 5.1 Suggested data model

**Option A — Server profile (lean for 0.9):**

```text
Server (features / config_json / dedicated columns — TBD):
  host_profile: "haos" | "ha_supervised" | "ha_container" | "ha_core" | null
  ha:
    marked_by: manual | detected
    detected_at, detection_method: ssh | nmap | manual
    version, install_type
    base_url (optional, for future deep links)
```

**Option B — Integration type `home_assistant` (v1.0-shaped):**

```text
Integration(type=home_assistant, base_url, credentials LLAT)
  + IntegrationBinding(role=service|dashboard, server_id=…)
  last_status_json: version, location_name, addons_summary…
```

**0.9 lean:** Option A (+ optional nmap kind refinement). Option B documented for when REST ships.

### 5.2 SSH discovery probes (spike design)

Read-only, timeout-bounded, **no** package install:

| Probe | Signal |
|-------|--------|
| `cat /etc/os-release` | `ID=hassos` / pretty name Home Assistant OS |
| `command -v ha` + `ha core info` / `ha supervisor info` | HAOS CLI |
| Docker inventory: image/name contains `homeassistant` | Container install on managed host |
| Open port **8123** + hostname patterns (nmap) | Weak advisory signal only |

Nmap: extend `device_classify` heuristics for HA-ish hosts (port 8123 + hostname) — **advisory kind only**; never auto-promote to managed Server.

### 5.3 Future REST surface (path 1)

Document only until implementation:

| API | Auth | Use |
|-----|------|-----|
| Core `GET /api/` | LLAT Bearer | Reachability / API version |
| Core `GET /api/config` | LLAT Bearer | location_name, version, components summary |
| Supervisor (HAOS) host/addon endpoints | Supervisor token / proxy | Install type depth, add-ons |

- TLS verify **default on** (operator override like Grafana).  
- **Do not** pull full entity state graphs by default.  
- **Gut call:** implement REST in **v1.0**, not 0.9 — 0.9 proves identity via mark + SSH/nmap.

---

## 6. Path 2 — HAOS-native (post-1.0 discovery)

| Idea | Notes | Version |
|------|-------|---------|
| Custom integration (HACS or core-style) | Uses PiHerder API tokens + IP allowlist | **≥1.0** |
| Sensors: server online, last backup, pending updates | Read-only first | ≥1.0 |
| Buttons/services: update-check, refresh docker inventory | Scope-limited tokens | ≥1.0 |
| Binary sensors for job failed / integration_down | Complement web push | ≥1.0 |
| Add-on packaging (HAOS store) | Heavier; may follow component | Maintenance |

**Existing bridge:** operators can already wire HA to PiHerder with bearer tokens ([API.md](API.md) § Home Assistant). Path 2 productizes that with sensors/actions and docs — it does not invent a second auth model.

---

## 7. Relationship to existing systems

| System | Interaction |
|--------|-------------|
| **Integrations hub** | Kuma host-service binds already cover “is HA up?”; HA type would be parallel, not a replacement |
| **LAN Discovery** | Fingerprint + kind; promote/link stays manual |
| **Host lifecycle / wizard** | HAOS least-priv skip stays; profile may inform privilege step copy |
| **API tokens** | Path 2 client; path 1 does not replace tokens |
| **Push / notifications** | Path 2 may surface fleet alerts in HA; PiHerder push remains independent |

---

## 8. Open decisions (refine in 0.9)

| # | Question | Lean |
|---|----------|------|
| **D1** | Server profile vs Integration row for HA identity? | **Profile first (0.9)**; Integration if REST |
| **D2** | Ship any REST in 0.9? | **No** unless spike is trivial and high value |
| **D3** | Path 2 target tag | **1.0** or **1.0.x** maintenance |
| **D4** | Auto-create Integration when HA detected? | **No** — mark server only |
| **D5** | Separate “PiHerder from HA” wiki from path 1 enrichment? | **Yes** — keep API-token client docs distinct |
| **D6** | nmap kind id (`home_assistant` vs fold into `iot` / `server`)? | Prefer explicit kind if UI chip is useful |

Update this table as spikes land; freeze before v0.9.0 tag for anything that shipped.

---

## 9. Phases

| Phase | Content | Window |
|-------|---------|--------|
| **P0** | This architecture / discovery document | **0.9 day-1** |
| **P1** | Mark HAOS + UI chip + optional nmap kind | **0.9** (HA1/HA2) |
| **P2** | SSH fact collection + store on server | **0.9** if capacity (HA3) |
| **P3** | REST adapter + Integration type | **v1.0 candidate** (HA4) |
| **P4** | Supervisor add-ons / deep links | Post-1.0 (HA6) |
| **P5** | HA custom component (path 2) | **≥1.0** (HA5) |

Unit + E2E for any shipped P1/P2 UI: see [PLAN_v0.9.0.md](PLAN_v0.9.0.md) stream **Q**.

---

## 10. Explicit non-goals

- Replacing HA dashboards, energy, or history.  
- Syncing thousands of entities into PiHerder.  
- Controlling IoT devices from PiHerder.  
- Shipping an HA add-on **inside** the main PiHerder image.  
- Automating HAOS least-priv the same as Debian (documented skip remains).  
- Live Home Assistant instances in GitHub Actions.

---

## 11. Security bar (all paths)

| Topic | Bar |
|-------|-----|
| Credentials | Fernet at rest; never log LLAT / SSH keys |
| Actions | Confirm + audit; step-up for host reboot / core restart |
| Network | Prefer TLS; document LAN-only Supervisor exposure risks |
| Tokens (path 2) | Least scopes; IP allowlist recommended for HA host |
| RBAC | Viewer: read chips only; admin/operator for mark + credentials |

---

## 12. Changelog (planning)

| Date | Note |
|------|------|
| 2026-07-23 | Document opened for v0.9 discovery; path 1 lean (mark/SSH); path 2 ≥1.0; REST lean v1.0 |

**End of feature plan** — living; refine with spikes and decisions during v0.9.0.
