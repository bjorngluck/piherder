# Feature plan — Runtime topology & stack dependencies (H2.5 depth)

**Status:** **Active design** — open questions locked 2026-07-18 (operator answers)  
**Horizon:** H2.5 · builds on Network maps (v0.5) + Kuma coverage (v0.6 H3)  
**Related:** [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) § H2.5 · [PLAN_v0.6.0.md](PLAN_v0.6.0.md) § H · [FEATURE_PLAN_PIHOLE_NPM_CERTS.md](FEATURE_PLAN_PIHOLE_NPM_CERTS.md) · Wiki [Network maps](../wiki/integrations/dns-fabric.md) · Coverage `/dns/coverage`

---

## 1. Goal

Give operators two clear altitudes of truth without one endless screen:

| Altitude | Question | Primary surface |
|----------|----------|-----------------|
| **Service path (high level)** | What **customer-facing** names exist, and **where** do they land? | Catalog → Network hub · Path map · Hosts map |
| **Runtime stack (detail)** | What **containers** make that service work, how they **connect**, and what is **monitored**? | **Side panel first** (P1); optional path-map blow-up later; Coverage for monitor gaps |

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
| **P1** | **Side panel** stack expand | Path/project → container list + ports + mute/bind; one stack at a time | 0.6.x / 0.7 |
| **P1b** | Inventory enrich in DB | Compose `depends_on` + service metadata; schedule + on-demand + post-deploy/compose triggers | with or just after P1 |
| **P2** | Suggest edges | From enriched inventory + heuristics; accept/dismiss; **include cross-host candidates** (NPM edge, shared services) | 0.7 |
| **P3** | Manual edges | Persist `RuntimeEdge` (cross-host capable); show in side panel | 0.7 |
| **P4** | Map expand | Path map: one-stack blow-up with confirmed edges | 0.7–0.8 |
| **P5** | Monitor depth | TCP/Docker bind polish; **optional alert** when monitored/unmuted container down | parallel |
| **P6** | Richer discovery | nmap H1 remains separate; shared-service catalog polish | later |

**0.6 ship bar:** does **not** require P1–P4. P0 + coverage/deps audit is enough for RC2 polish track; **P1 side panel** is the next high-value slice.

---

## 8. UX sketches

### High-level Path map (unchanged mental model)

```text
  name ──► NPM ──► host ──► project
                 (optional expand)
```

### Expanded stack (focused)

```text
              ┌ caddy ─┐
  name → NPM →│        ├→ web ─┬→ redis
              └ host   ┘      └→ db
                         celery ─┘
```

Only **one** expanded stack at a time (accordion). Collapse returns to path-only.

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
| D | Relation to LAN scan (H1) | **Orthogonal** (devices vs stacks) |

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

## 12. Explicit non-goals

- Replacing Uptime Kuma / Grafana  
- Full APM / distributed tracing  
- Auto mesh of entire LAN  
- Guaranteed complete dependency graphs without operator input  
- Kubernetes topology  

---

## 13. References in codebase (today)

| Area | Path |
|------|------|
| Fabric views / paths | `app/services/dns_fabric/` |
| Kuma coverage + dep inventory audit | `app/services/dns_fabric/kuma_coverage.py` |
| Coverage UI | `app/templates/dns_coverage.html` · route `/dns/coverage` |
| Network hub | `app/templates/dns_list.html` |
| Docker inventory | `app/services/docker_inventory.py` |
| Kuma binds | `IntegrationBinding` · `app/services/integrations/` |

---

## 14. Changelog

| Date | Note |
|------|------|
| 2026-07-18 | Initial plan: dual altitude, expand-one-stack, suggest + manual deps, Kuma mapping, P0–P6 |
| 2026-07-18 | Locked: side panel first; cross-host edges v1; DB inventory enrich + schedule/on-demand/post-deploy; optional alert only for monitored/unmuted container down |

**End of plan** — P1 side panel + P1b inventory enrich can proceed with decisions 1–13 locked; refine A–D defaults only if needed.
