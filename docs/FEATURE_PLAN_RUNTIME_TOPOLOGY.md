# Feature plan — Runtime topology & stack dependencies (H2.5 depth)

**Status:** **Stream closed for v0.6** (2026-07-18) — P0–P5 + P4b **shipped**; residual → later / **v0.8**  
**Horizon:** H2.5 · builds on Network maps (v0.5) + Kuma coverage (v0.6 H3)  
**Related:** [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) § H2.5 · [PLAN_v0.6.0.md](PLAN_v0.6.0.md) § H · [FEATURE_PLAN_PIHOLE_NPM_CERTS.md](FEATURE_PLAN_PIHOLE_NPM_CERTS.md) · Wiki [Network maps](../wiki/integrations/dns-fabric.md) · Coverage `/dns/coverage`

---

## 1. Goal

Give operators two clear altitudes of truth without one endless screen:

| Altitude | Question | Primary surface |
|----------|----------|-----------------|
| **Service path (high level)** | What **customer-facing** names exist, and **where** do they land? | Catalog → Network hub · Path map · Hosts map |
| **Runtime stack (detail)** | What **containers** make that service work, how they **connect**, and what is **monitored**? | **Stack panel** (P1) + **map expand** (P4) · Coverage for monitor gaps |

**Suggest links** from deployments + Docker inventory whenever possible; **operator confirms** edges and monitor binds. Manual dependency mapping is first-class when inference is incomplete.

---

## 2. Problem statement

Homelab stacks are multi-hop:

```text
Internet → (NPM?) → Caddy → web → redis / celery / db
                         celery → db
```

Today PiHerder is strong at:

- Host fleet + SSH  
- DNS fabric (name → host / NPM / project)  
- Kuma **bindings** and **coverage** (paths + inventory deps, mute infra)

It is weak at:

- Showing **one stack at a time** on the main map without clutter  
- **Suggested** runtime edges (web→db) from compose/inventory  
- **Manual** “this depends on that” when compose is silent  
- Separating **public path monitoring** (HTTPS) from **infra** (TCP/Postgres/Docker up-down)

Without a plan, Network becomes a long audit scroll and maps become spaghetti.

---

## 3. Decisions (locked unless reversed)

| # | Decision |
|---|----------|
| 1 | **Two altitudes** — default maps stay **customer-facing paths**; runtime detail is expand / drill-down, not default fleet-wide container mesh |
| 2 | **Suggest → confirm** — never auto-create Kuma monitors; never auto-commit dependency edges without operator accept (or a soft “suggested” layer) |
| 3 | **One entity graph** — name, host, project, container, edge, monitor bind are projections of one model (ROADMAP H2.5) |
| 4 | **Manual edges are first-class** — compose `depends_on` / inventory ports are hints only |
| 5 | **Monitoring stays in Kuma** — PiHerder maps monitors (HTTP, TCP, Docker, Postgres…) to entities; connection strings stay in Kuma |
| 6 | **Infra mute remains** — postgres/redis/… default muted for nag lists; show-infra + bind when operator wants TCP/DB checks |
| 7 | **No agent fleet** — SSH inventory + optional Kuma Docker; no eBPF/mesh product in this plan |
| 8 | **Coverage stays its own page** (`/dns/coverage`) — hub stays lean (maps teaser + paths + settings) |
| 9 | **Expand UX = side panel first (P1)** — Path map canvas blow-up is P4 only after panel is solid |
| 10 | **Cross-host edges in scope for v1 edges** — reality (NPM edge host ≠ app host; shared DB/redis/services across Pis). Same-host still common; UI must allow different `from_server` / `to_server` |
| 11 | **Enrich inventory in DB** — parse compose metadata (`depends_on`, services, published ports) into stored inventory (or sibling JSON on server), not only on-demand in the panel |
| 12 | **Inventory refresh triggers** — (a) scheduled job, (b) operator **Refresh now**, (c) after template/stack **deploy** and meaningful **compose save/redeploy** on that host (best-effort, debounced) |
| 13 | **Container down** — Docker inventory / Docker UI already shows running state; **optional alert** only when a **monitored** (Kuma-bound or explicit unmute) container is down — not spam for muted infra |

---

## 4. Operator UX (target)

### 4.1 High-level Network map (default)

**Shows:**

- Hosts (LAN / cloud)  
- Customer-facing **services** (fabric FQDNs / path cards): *where it runs* (backend host, optional edge NPM)  
- Optional status chip: Kuma path coverage (covered / partial / gap) — already light chips on path cards  

**Does not show by default:**

- Every container on every host  
- Every redis/db edge across the fleet  

**Actions:**

- Open Path map / Hosts map (existing)  
- Open **Kuma coverage** (existing `/dns/coverage`)  
- **Expand stack** for one service or one project (see 4.2)

### 4.2 Expand a stack (one at a time)

From a **path card**, **Path map focus**, or host Docker project:

```text
[ piherder.hacknow.info ]  · host rpi-X · project piherder · Kuma ✓
        [ Expand stack ▾ ]
┌─────────────────────────────────────────────┐
│  web (running)     ports 8000               │
│  caddy (running)   ports 80/443             │
│  db (running)      ports — / muted infra    │
│  redis (running)   muted infra              │
│  celery-worker                              │
│                                             │
│  Suggested edges:                           │
│    web → db, web → redis, celery → db       │
│    [ Accept all ] [ Accept selected ]       │
│  Manual: [ + Link dependency ]              │
│  Monitors: bind / open coverage             │
└─────────────────────────────────────────────┘
```

**Expand behaviour (locked):**

| Mode | Description | Phase |
|------|-------------|--------|
| **C — Side panel** | List + suggested/manual links + monitor status; no full SVG required | **P1 — ship first** |
| **A — Blow-up on same canvas** | Focus path; child nodes + edges for **this stack only** | **P4** after panel |
| **B — Stack detail map** | Full-page `/dns/stack?…` if panel outgrows mobile | Optional if needed |

**Locked:** side panel first.

### 4.3 Detailed linkages

When expanded:

| Edge source | Treatment |
|-------------|-----------|
| Compose `depends_on` / `links` (if readable) | **Suggested** edge |
| Same project + common roles (web→db, worker→db) | **Suggested** by heuristic |
| Published ports + “data” image roles | **Hint** for TCP monitor, not edge by itself |
| Operator **manual link** | **Confirmed** edge (stored) |
| Kuma bind on container/project | **Monitor** decoration on node |

Click container → host Docker deep link, bind Kuma, mute, mark role (`app` / `worker` / `data` / `cache` / `edge`).

### 4.4 Manual dependency mapping

```text
From:  [ project / container dropdown ]
To:    [ project / container dropdown ]
Kind:  depends_on | talks_to | optional
Note:  free text (optional)
[ Save ]  → audit: fabric.edge_set
```

Rules:

- **Cross-host allowed in v1** — e.g. NPM on edge host → app project on another; app → shared Postgres on a third host  
- Manual picker: host → project → container on **both** ends (default `to_server` = backend host of path)  
- Soft-delete / hide edge; no cascade delete of monitors  
- Suggested edges can be **accepted** (promote to confirmed) or **dismissed** (don’t re-nag for N days / forever)  
- Suggestions may also be **cross-host** when fabric already knows edge (NPM target ≠ backend) or shared service FQDNs exist

---

## 5. Data model (indicative)

### 5.1 Existing (reuse)

| Entity | Role |
|--------|------|
| `Server` | Host |
| `ServiceDnsRecord` | Customer-facing name + backend/target + optional `docker_project` |
| `StackDeployment` | Template desired state → project on host |
| Docker inventory JSON on `Server` | Projects, containers, ports, running — **extend** with compose graph fields |
| `IntegrationBinding` | Kuma (and others) ↔ server / project / container |

### 5.2 Inventory enrichment (locked approach)

Store richer data **in DB** (on the server inventory blob and/or structured columns later):

| Field (per project / service) | Source |
|-------------------------------|--------|
| containers, running, image, ports | `docker ps` / existing L1 |
| `depends_on` list | compose file(s) on host (SSH read) |
| service names, networks (optional) | compose |
| checksum / mtime of compose | change detection for trigger (c) |

**Refresh triggers (locked):**

| Trigger | Behaviour |
|---------|-----------|
| **Schedule** | Per-host or fleet cadence (align with container check / inventory stale, e.g. existing L1 refresh) |
| **On command** | Server Docker **Force refresh** / stack panel “Refresh stack” |
| **On deploy / compose change** | After template deploy, stack deploy, compose save+redeploy — enqueue inventory enrich for that host (debounce 30–60s) |

Failed enrich must not break Docker UI; fall back to last good inventory.

### 5.3 New (proposed)

```text
RuntimeEdge
  id
  from_server_id, from_project, from_container   # container optional = whole project
  to_server_id, to_project, to_container         # may differ host (NPM, shared services)
  kind: depends_on | talks_to | mounts | custom
  source: suggested | accepted | manual
  confidence: 0–100   # for suggestions
  dismissed_at / note
  created_at, updated_at, created_by_user_id
```

Include `RuntimeEdge` rows in **herder self-backup**.

Optional later:

```text
RuntimeNodeMeta
  server_id, project, container
  role: edge | app | worker | data | cache | other
  mute_monitoring: bool   # overlaps coverage mute keys
```

### 5.4 Suggestion engine (v1 — no new agents)

| Input | Suggestion |
|-------|------------|
| Inventory containers in same project | Nodes of the stack |
| Image/name heuristics | Role = data/cache/app |
| Enriched compose `depends_on` in DB | Directed edges (often same-host) |
| Fabric path NPM edge host ≠ backend | Cross-host edge candidate (proxy → app host/project) |
| Shared service FQDN / known projects | Cross-host shared DB/cache candidates (low confidence) |
| Template deployment files | Compose graph when inventory lagging |
| Existing Kuma binds | “Already monitored” on node |
| Coverage mute patterns | Don’t alert on muted infra unless unmuted |

**Out of v1:** packet capture, eBPF, automatic L7 service mesh, creating Kuma monitors via API (unless a later explicit opt-in).

---

## 6. Monitoring mapping (Kuma)

| Check type | When | PiHerder mapping |
|------------|------|------------------|
| HTTP(S) on FQDN | Customer path | Path coverage (H3) |
| Docker container running | App/worker up-down without published port | Bind role=service + container (Kuma Docker or HTTP) |
| TCP / Postgres / Redis | Data plane; needs reachability from Kuma | Bind + optional edge web→db |
| Host SSH | Host alive | Existing SSH binds |

**Container down (locked product stance):**

| Signal | Where | Alert? |
|--------|--------|--------|
| Running / stopped | Docker inventory · Docker UI (already) | No automatic fleet spam |
| Monitored container down | Kuma bind on that container **or** explicit unmute in coverage | **Optional notification** — only if bound/unmuted |
| Muted infra (postgres/redis default) | Coverage mute | No alert unless operator unmutes or binds |

Implementation note: prefer reusing Kuma poll / binding `last_state` for “monitored container down” alerts rather than inventing a second health system. Docker view remains source of truth for operators browsing stacks.

**Reachability note (docs, not code magic):**  
TCP/DB monitors require published ports, shared Docker network with Kuma, or host-local Kuma. Prefer **not** exposing DB to the whole LAN only for monitoring.

---

## 7. Phased delivery

| Phase | Name | Outcome | Target |
|-------|------|---------|--------|
| **P0** | IA split | Hub lean; Coverage page; path chips | **Done** (0.6) |
| **P1** | **Side panel** stack expand | Path/project → container list + ports + mute/bind; one stack at a time | **Done** (0.6.x) |
| **P1b** | Inventory enrich in DB | Compose `depends_on` + service metadata; schedule + on-demand + post-deploy/compose triggers | **Done** (inventory v2 + panel edges; accept/dismiss = P2) |
| **P2** | Suggest edges | From enriched inventory + heuristics; accept/dismiss; **include cross-host candidates** (NPM edge, shared services) | **Done** (panel accept/dismiss; fabric NPM candidate) |
| **P3** | Manual edges | Persist `RuntimeEdge` (cross-host capable); show in side panel | **Done** (same-project manual form; cross-host via accept + API) |
| **P4** | Map expand | Path/hosts map: sideways fan; role colors; confirmed + soft adjacent columns | **Done** |
| **P4.5** | **Detailed stack view** | Panel detail expand; map node click → detail | **Done** |
| **P4b** | **Stack container order** | Operator long-press/drag reorder; persists; drives **column left→right** on map | **Done** |
| **P5** | Monitor depth | TCP bind in panel; **optional alert** when Kuma-bound container down in inventory | **Done** |
| **P6** | Shared-service catalog polish | Catalog of shared DB/redis/services for better cross-host suggest | **Later** (not 0.6) |
| **Later** | **Configurable columns & links** | Operator-defined map columns, pin roles to columns, explicit edge→column layout (beyond order-driven L→R) | **Partial (0.7):** categories/tags/visual stacks + vocab-driven columns — see § 12c |
| **—** | **LAN discovery (nmap)** | Orthogonal device discovery — not stack deps | **v0.8.0** (PLAN H1) |

### Shipped vs not done (stream close-out)

| Shipped (0.6) | Not done — add later |
|---------------|----------------------|
| P0 IA split + Coverage page | Link-to-column layout rules / per-project column profiles |
| P1 side panel stack expand | Cross-host manual picker polish (API works; UI still same-project-first) |
| P1b compose graph in inventory | Broader Hosts/Path published-port chips |
| P2 suggest edges accept/dismiss | P6 shared-service catalog polish |
| P3 manual `RuntimeEdge` + backup | LAN nmap discovery → **v0.8.0** |
| P4/P4.5 map expand + detail | Docker management full visual parity (stretch) |
| P4b container order → column L→R | |
| P5 Kuma-bound container down alerts | |
| **0.7 T0–T4 annotations** (exact project, category, tags, visual stacks, vocab columns) | |

**0.6 track:** operator-locked dual-altitude UX is **done**. Do not reopen for freeze unless regressions.

---

## 8. UX sketches

### High-level Path map (unchanged mental model)

```text
  name ──► NPM ──► host ──► project
                 (optional expand)
```

### Expanded stack (focused) — locked UX

**Map expand (sideways fan, no deep-link chips):**

```text
  path focus ──►  [ edge: caddy ] → [ app: web ] → [ data: db, redis ] → [ queue: celery ]
```

- Columns are **role groups**: `edge` · `app` · `queue` · `data` (data = db + redis + tooling together — **not** a separate cache column).  
- Default L→R when **no** custom order: `edge → app → queue → data`.  
- With **custom stack order**, column order follows **min order_index** per column (e.g. celery last in panel → **queue rightmost** so soft lines stay left→right).  
- Soft structure lines connect **adjacent columns only**; confirmed `RuntimeEdge` always drawn.  
- Click container → Stack panel detail. Nav deep-links live in the **panel**, not as map chips.

**Panel reorder:**

- Desktop: drag **⋮⋮** handle. Mobile: long-press row, then drag.  
- Persist: app setting `stack_container_order_json` (`server_id:project` → ordered names).  
- Audit: `fabric.stack_order`.

### Mobile

- **Stack side panel** (list + forms) is the primary expand UX  
- Coverage remains its own page; panel = full-width drawer/sheet on small screens  

---

## 9. Security & product bar

- No private keys or DB passwords in PiHerder for monitoring (Kuma owns secrets)  
- Audit: `fabric.edge_accept`, `fabric.edge_manual`, `fabric.edge_dismiss`, inventory enrich jobs; bind remains existing integration audits  
- Viewer: read graph; operator+: accept/manual/mute  
- Suggestions must not open firewall ports or rewrite compose  
- Post-deploy inventory enrich is best-effort; never block deploy success on enrich failure  

---

## 10. Decisions log (2026-07-18 operator answers)

| # | Question | Answer (locked) |
|---|----------|-----------------|
| 1 | Expand UX first? | **Side panel first** (canvas expand later) |
| 2 | Cross-host deps? | **Yes in v1** — NPM, shared services are real; edges are multi-host |
| 3 | Compose/depends_on when? | **Enrich inventory in DB**; schedule + on-demand + trigger after deploy/compose change |
| 4 | Container down / Kuma Docker? | Docker UI already shows state; **optional alert** only if container is **monitored** (bound) or **unmuted** |

### Remaining open (smaller)

| # | Question | Lean default |
|---|----------|--------------|
| A | Herder backup includes `RuntimeEdge`? | **Yes** |
| B | Debounce window for post-deploy enrich | **30–60s** |
| C | Cross-host auto-suggest confidence threshold | High only for fabric NPM edge→backend; low for “shared DB” heuristics |
| D | Relation to LAN scan (H1) | **Orthogonal** (devices vs stacks) — product target **v0.8.0** |

---

## 11. Success criteria

An operator can:

1. See **customer-facing** services and **which host** they run on without container noise.  
2. **Open a side panel** for one stack and see containers + ports + Kuma status.  
3. **Accept suggested** links (including **cross-host** where relevant) or **draw manual** ones across hosts.  
4. Rely on inventory that **stays fresh** via schedule, button, and post-deploy enrich.  
5. Bind HTTP to the public path and optionally TCP/Docker to data containers **without** PiHerder storing DB credentials.  
6. Mute infra noise; get **optional alerts** only when a **monitored/unmuted** container is down.

---

## 12. Explicit non-goals (now)

- Replacing Uptime Kuma / Grafana  
- Full APM / distributed tracing  
- Auto mesh of entire LAN  
- Guaranteed complete dependency graphs without operator input  
- Kubernetes topology  
- Map deep-link chips (Server/Service/Docker) on expand — **panel owns nav**  

## 12b. Later roadmap (not in 0.6; add when capacity)

| Item | Intent | Target |
|------|--------|--------|
| **User-configurable columns** | Operator names/pins columns beyond fixed role groups (edge/app/queue/data) | Later residual |
| **Link-to-column rules** | Explicit soft/confirmed edge placement between named columns (not only adjacent L→R) | Later residual |
| **Per-stack layout profiles** | Save alternate fan layouts per project | Later residual |
| **Cross-host manual picker polish** | First-class dual-host container picker in panel (backend already multi-host) | Later polish |
| **P6 shared-service catalog** | Named shared DB/redis services for better suggest confidence | Later |
| **LAN discovery (nmap-class)** | Opt-in LAN CIDR scan — devices, not stack graph | **v0.8.0** |

---

## 12c. Topology annotations (0.7 capacity)

Operator presentation layer on top of compose projects — **does not** change deploy/stop boundaries.

| Concept | Rules |
|---------|--------|
| **Category** | One per container; drives map columns; fixed vocab (seed: edge/app/queue/cache/data/tooling); heuristic default; operator override |
| **Tags** | Multi chips from fixed vocab (web/db/worker/…); not free text; operator can add vocab entries |
| **Visual service stack** | Group containers under **one compose project** for panel/map filter; create/move; deploy still whole project |
| **Exact project match** | No soft substring match (`piherder` ≠ `piherder-e2e`) |
| **DB** | `topologycategory`, `topologytag`, `visualservicestack`, `containerannotation`, `containerannotationtag` · herder backup included |
| **Order** | `containerannotation.sort_index` primary; settings JSON dual-write for one release |

Still later: per-project column profiles, explicit edge→column layout, Docker UI full parity (T6).

**Related (Docker, not fabric):** **compose sets** — multiple compose files under one project folder with under-project pills and optional `-f` deploy. Orthogonal to view groups. See [PLAN_v0.7.0.md](PLAN_v0.7.0.md) stream C · wiki Docker overview.

---

## 13. References in codebase (shipped)

| Area | Path |
|------|------|
| Fabric views / paths | `app/services/dns_fabric/` (`core`, mesh, coverage) |
| Stack panel payload | `app/services/dns_fabric/stack_panel.py` |
| Map expand payload | `app/services/dns_fabric/stack_expand.py` · `GET /dns/stack-expand.json` |
| Panel + map JS | `app/static/js/fabric-stack-panel.js` · `fabric-stack-expand.js` · `fabric-mesh.js` |
| Container order | `app/services/stack_order.py` · `POST /dns/stack-order` · annotation `sort_index` + settings dual-write |
| Annotations | `app/services/container_annotations.py` · migration `024_topology_annotations` |
| Compose sets (Docker) | `app/services/compose_sets.py` · inventory + Docker UI pills |
| Compose graph / edges | `app/services/compose_graph.py` · `runtime_edges.py` · migration `021_runtime_edge` |
| Stack monitor alerts | `app/services/stack_monitor.py` · setting `stack_inventory_down_alerts` |
| Kuma coverage | `app/services/dns_fabric/kuma_coverage.py` · `/dns/coverage` |
| Docker inventory | `app/services/docker_inventory.py` (compose graph v2 + compose_sets) |
| Herder backup | includes `RuntimeEdge` + topology annotation tables |
| Tests | `tests/test_stack_*.py` · `test_container_annotations.py` · `test_compose_sets.py` · `test_compose_graph.py` · `test_runtime_edges.py` |

---

## 14. Changelog

| Date | Note |
|------|------|
| 2026-07-18 | Initial plan: dual altitude, expand-one-stack, suggest + manual deps, Kuma mapping, P0–P6 |
| 2026-07-18 | Locked: side panel first; cross-host edges v1; DB inventory enrich + schedule/on-demand/post-deploy; optional alert only for monitored/unmuted container down |
| 2026-07-18 | P1 side panel shipped; P1b compose graph in inventory L1 + suggested edges in panel (display); force refresh from panel |
| 2026-07-18 | P2/P3: `RuntimeEdge` + accept/dismiss/manual/delete; herder backup; panel UI |
| 2026-07-18 | P4: path map focus expands runtime stack (containers + confirmed edges) via `/dns/stack-expand.json` |
| 2026-07-18 | P4 polish: LTR edge/app/queue/data columns, role colors + type chips; hosts map; soft structure; P4.5 detail |
| 2026-07-18 | **P4.5** expandable container detail; map node click → detail; **P5** inventory down alerts for Kuma-bound containers |
| 2026-07-18 | **P4b** stack container order (long-press/drag); map column L→R from order; **data** keeps db+redis; no expand link chips; later: configurable columns / link-to-column |
| 2026-07-18 | **Stream closed for v0.6** — P0–P5+P4b done; residual column/layout/P6 later; **nmap → v0.8.0** |
| 2026-07-19 | **0.7 annotations:** exact project match; category/tags/visual stacks (DB); map columns from category vocab; stack panel editor |
| 2026-07-19 | View groups UX polish (Main token, pill chrome); multi-fan map on All; **compose sets** shipped on Docker page (orthogonal) |

**End of plan** — dual-altitude product + 0.7 annotations + Docker compose sets. Residual: link-to-column profiles, cross-host picker polish, P6 catalog; LAN scan is **v0.8.0**.
