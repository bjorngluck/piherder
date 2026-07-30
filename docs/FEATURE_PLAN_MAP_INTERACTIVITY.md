# Feature plan — Map interactivity (icons · pop-out · ports)

**Status:** **M1 + M2 + M3-lite shipping on v1.1.0-dev** (2026-07-30) — M0 locked same day; M4 sticky annotations + M5 custom pack remain roadmap  
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

- Locked focus ~**1.18×** scale + lift; hover = stroke-only (no flicker).
- Scale on **node `<g>`** (SVG/CSS child transform), not root `.fabric-mesh-svg`.
- Preserve NPM multi-path focus, clear-on-second-click, mobile + fullscreen.
- Pan/zoom remains viewBox-based.

### R4 — Ports, ownership, relationships

```text
Host
  ├─ 443/tcp  → pihole · role web
  ├─ 53/tcp+udp → pihole · role dns
  ├─ 8080/tcp → app/web · role web
  └─ 22/tcp   → host · role ssh (nmap/observed)
```

- Sources: Docker published ports (high) · compose/image heuristics · nmap open · fabric path · operator annotation (highest).
- Host port inventory panel: owner project/container when known.
- Stack expand: this stack’s ports first; siblings collapsed “other on host”.
- Fixed role vocab: `web`, `dns`, `db`, `cache`, `proxy`, `ssh`, `metrics`, `other`.
- Multi-port one container (Pi-hole) first-class.
- Edges stay on **RuntimeEdge** — no second edge system.
- M3 derive-only; M4 sticky `PortAnnotation` only if needed.

---

## 4. Phasing

| Phase | Scope | When |
|-------|--------|------|
| **M0** | This plan + locked decisions | **Done** 2026-07-30 |
| **M1** | Canned icons (**S-icon**) — sprite + Hosts map + racks | **Landed** 2026-07-30 |
| **M2** | Focus pop-out (~1.18× locked; hover stroke-only) | **Landed** 2026-07-30 |
| **M3** | Port ownership lite — role heuristics on stack chips | **Landed (lite)** 2026-07-30 — roles on chips; host-wide port panel later |
| **M4** | Sticky roles / nmap∪docker / map focus port summary | v1.2 |
| **M5** | Custom pack upload + backup | v1.2 late / v1.3 |

---

## 5. Locked decisions (2026-07-30)

| # | Decision | Locked value |
|---|----------|--------------|
| D1 | “Custom pac” | **Custom icon pack** (not proxy PAC) |
| D2 | M1+M2 vs freeze | **After** v1.1 E2E / tag; optional S-icon Cap only if capacity |
| D3 | Icon style | Simple line/duotone SVG, PiHerder tokens — not full product logos in M1 |
| D4 | Pop-out scale | ~**1.18×** locked focus; hover stroke-only |
| D5 | Port annotation persistence | M3 **derive-only**; M4 table if sticky roles needed |
| D6 | Nmap ports on fleet hosts | Host port panel, marked **observed**; off map canvas by default |

---

## 6. Acceptance (product)

- Operator identifies device types on the map without reading every name.
- Selecting a host/path **feels interactive** (pop-out), not only dimming.
- Operator can answer: *what is port X on this host, and which stack owns it?* for Docker-backed services.
- Custom icons remain a roadmap path without blocking the canned set.

---

## 7. Implementation touchpoints (when coding)

| Area | Files / modules (indicative) |
|------|------------------------------|
| Icons | `device_classify` kinds · new sprite under `app/static/` · `mesh_physical` node markup · rack templates |
| Pop-out | `fabric-mesh.js` focus · `fabric.css` node group scale |
| Ports | `dns_fabric/ports.py` · stack panel · host drawer · optional annotation model later |
| Backup | M5 paths under `DATA_ROOT` like `service_logos/` |

---

## 8. History

| Date | Note |
|------|------|
| 2026-07-30 | M0 requirements drafted from operator themes; **open decisions approved** as locked defaults above |
| 2026-07-30 | **M1+M2+M3-lite landed** on `v1.1.0-dev`: kind SVG sprite, Hosts/Path pop-out, stack port role chips |
