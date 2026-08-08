# Feature plan — Map interactivity (icons · pop-out · ports)

**Status:** **M1–M4 shipping on v1.1.0-dev** (2026-07-30) — progressive ports UX + discovered devices; M5 custom pack remains roadmap  
**Horizon:** Elevated into v1.1 train (capacity); residual polish with operator QA  
**Related:** [FEATURE_PLAN_RUNTIME_TOPOLOGY.md](FEATURE_PLAN_RUNTIME_TOPOLOGY.md) · [FEATURE_PLAN_LAN_NMAP.md](FEATURE_PLAN_LAN_NMAP.md) · [PLAN_v1.1.0.md](PLAN_v1.1.0.md) · wiki [Network maps](../wiki/integrations/dns-fabric.md) · residual **S-icon**

---

## 1. Goal

Hosts / Path maps are **functional**. Make them **more legible and interactive** without a fleet-wide container spaghetti mesh.

| Theme | Outcome |
|-------|---------|
| **R1 Canned icons** | Device / discovered / host kinds show **glyphs**, not text badges only |
| **R2 Custom pack** | Optional operator icon overrides / pack (roadmap after R1) |
| **R3 Select pop-out** | Locked focus **enlarges** the primary node; dim neighbours stay |
| **R4 Ports & ownership** | Host ports map to **stack/container owners** + roles (DNS, web, DB…) |

**Non-goals:** eBPF mesh · auto-create Kuma monitors · k8s topology · geo map · redesign path cards as the primary surface.

**Design lock (unchanged):** two altitudes — default maps stay customer-facing / host topology; container + port depth is **expand / focus / panel**.

---

## 2. Baseline (today)

| Capability | Gap |
|------------|-----|
| Hosts + Path SVG maps, pan/zoom via viewBox | Text-first cards |
| `device_kind` + operator override | No map glyphs (**S-icon** residual) |
| Focus dim + stroke/shadow | No real enlarge (root SVG bans CSS transform for crisp pan/zoom) |
| Stack panel + published `host→container` chips | No port **role** or host-wide ownership view |
| `RuntimeEdge`, topology categories/tags | Deps / roles for containers — not port annotations |
| Integration `service_logos/` | Not on fabric map nodes |

---

## 3. Requirements (summary)

### R1 — Canned icons

- Stable ids = `device_classify` kinds (`raspberry_pi`, `nas`, `router`, …).
- In-app SVG sprite/symbols (no CDN); dark/light tokens; unknown fallback.
- Surfaces: Hosts map nodes, discovered chips, rack/list leading icon.
- Kind override updates icon.
- **Not** free-form upload (R2).

### R2 — Custom icon pack (roadmap)

- Resolve: device/host override → canned kind → fallback.
- `DATA_ROOT` storage + herder self-backup; SVG preferred; sanitize.
- UI: pick canned or upload on device/server; optional zip pack later.
- No marketplace; no remote URL fetch by default.

### R3 — Selection pop-out

- Locked focus ~**1.30×** scale + lift; hover = stroke-only (no flicker).
- Scale on **node `<g>`** (SVG/CSS child transform), not root `.fabric-mesh-svg`.
- Preserve NPM multi-path focus, clear-on-second-click, mobile + fullscreen.
- Pan/zoom remains viewBox-based.

### R4 — Ports, ownership, relationships

```text
Host / discovered device
  compact callout  →  ports-only list  →  by-service fan (optional)
       │                    │                    │
       │                    ├─ 443/tcp · web     ├─ pihole → 53, 443
       │                    ├─ 53/tcp · dns      └─ dbstack → 5432
       │                    └─ 8080 · observed
```

- Sources: Docker published ports (high) · compose/image heuristics · nmap open · fabric path · operator annotation (highest).
- Host port inventory panel: owner project/container when known.
- **Progressive on-map UX:** compact (whole-box tap) → **ports-only** list → optional **Services** fan; compact chrome (Back / Edit / Services).
- **Discovered devices** (cameras, printers, …): same flow via `nmap_device_id` + nmap ports.
- **App path containers:** published ports on stack boxes; click container with ports → scoped callout.
- Stack expand: this stack’s ports first; siblings collapsed “other on host”.
- Fixed role vocab: `web`, `dns`, `db`, `cache`, `proxy`, `ssh`, `metrics`, `other`.
- Multi-port one container (Pi-hole) first-class.
- Edges stay on **RuntimeEdge** — no second edge system.
- M4 sticky `PortAnnotation` (roles, hide, owner overrides).

---

## 4. Phasing

| Phase | Scope | When |
|-------|--------|------|
| **M0** | This plan + locked decisions | **Done** 2026-07-30 |
| **M1** | Canned icons (**S-icon**) — sprite + Hosts map + racks | **Landed** 2026-07-30 |
| **M2** | Focus pop-out (~1.30× locked; hover stroke-only) | **Landed** 2026-07-30 |
| **M3** | Port ownership lite — role heuristics on stack chips | **Landed** 2026-07-30 |
| **M4** | Sticky roles / nmap∪docker / map focus port summary / host panel | **Landed** 2026-07-30 (`PortAnnotation`, host ports panel, sticky role select) |
| **M5** | Custom pack upload + backup | **Roadmap** (v1.2 late / v1.3) |

---

## 5. Locked decisions (2026-07-30)

| # | Decision | Locked value |
|---|----------|--------------|
| D1 | “Custom pac” | **Custom icon pack** (not proxy PAC) |
| D2 | M1+M2 vs freeze | **After** v1.1 E2E / tag; optional S-icon Cap only if capacity |
| D3 | Icon style | Simple line/duotone SVG, PiHerder tokens — not full product logos in M1 |
| D4 | Pop-out scale | ~**1.30×** locked focus; hover stroke-only |
| D5 | Port annotation persistence | M4 sticky `PortAnnotation` (roles / hide / owner) |
| D6 | Nmap ports | On map via progressive expand for fleet **and** discovered devices; panel marked **observed** |
| D7 | Port expand steps | **compact → ports-only → by-service** (touch: whole callout) |

---

## 6. Acceptance (product)

- Operator identifies device types on the map without reading every name.
- Selecting a host/path **feels interactive** (pop-out), not only dimming.
- Operator can answer: *what is port X on this host, and which stack owns it?* for Docker-backed services.
- Operator can open **discovered** device ports (e.g. camera RTSP/HTTP) without linking a Server first.
- On mobile, ports expand uses a **whole-callout** hit target and progressive steps (not a microscopic Expand control).
- Custom icons remain a roadmap path without blocking the canned set.

---

## 7. Implementation touchpoints

| Area | Files / modules |
|------|-----------------|
| Icons | `device_icons.py` · `partials/device_kind_icons*.html` · `mesh_physical` · racks |
| Pop-out | `fabric.css` `.fabric-mesh-node-pop` scale(~1.30) · `fabric-mesh.js` focus lock |
| Port inventory | `dns_fabric/host_ports.py` · `ports.py` · `PortAnnotation` (migration 036) |
| On-map expand | `fabric-host-ports-expand.js` (compact / ports / full) · `/dns/host-ports-expand.json` |
| Stack ports | `stack_expand.py` `port_chips` · `fabric-stack-expand.js` taller boxes + container click |
| Panel | `/dns/host-ports-panel` · `partials/dns_host_ports_panel.html` |
| Wiki | [dns-fabric.md](../wiki/integrations/dns-fabric.md) progressive ports section |
| Backup | M5 paths under `DATA_ROOT` like `service_logos/` |

---

## 8. History

| Date | Note |
|------|------|
| 2026-07-30 | M0 requirements drafted from operator themes; **open decisions approved** as locked defaults above |
| 2026-07-30 | **M1+M2+M3-lite landed** on `v1.1.0-dev`: kind SVG sprite, Hosts/Path pop-out, stack port role chips |
| 2026-07-30 | **M4 landed:** `PortAnnotation` migration 036, host ports panel (Docker∪nmap), sticky roles, map focus summary + Ports button; M5 remains roadmap |
| 2026-07-30 | **On-map fan:** host → service cards with ports inside; then app-card layout polish |
| 2026-07-30 | **Ports UX polish:** pop-out **1.30×**; progressive **compact → ports-only → Services**; discovered devices; container/service ports; compact footer chrome (Edit · Services) |
