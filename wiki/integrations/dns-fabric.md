# Network (hosts ↔ apps ↔ proxy ↔ internet)

## What this is

**Network** is PiHerder’s view of how **names** reach **hosts** and **apps**: Pi-hole records, fleet FQDNs, NPM edges, LAN/gateway/public IP, optional Kuma status, and Docker project links — shown as path cards and topology maps.

It is **not** a Kubernetes-style service mesh. It is a homelab **map** of DNS + proxy + inventory.

**UI label:** **Catalog → Network** (URL slug remains `/dns` for compatibility).

**Pages:** Network hub · Hosts map · Path map · **Kuma coverage** (`/dns/coverage`)

## Why it exists

After a few years of “this CNAME points somewhere,” operators lose the picture of *name → proxy? → host → container*. Network maps rebuild that picture so you can answer “where does `grafana.example.com` go?” without opening three admin UIs.

---

## End-to-end: first useful Hosts map

1. Connect [Pi-hole](pihole.md); set host FQDNs + manage A where appropriate.  
2. On the Network hub, set **LAN subnet**, **gateway**, and public IP (or Lookup).  
3. Optional: bind Router / Public IP Kuma monitors.  
4. **Import all from Pi-hole** or Adopt candidates.  
5. Open **Hosts map** — confirm home ring vs cloud hosts.  
6. Open **Path map** for a specific FQDN flow.  

Journey: [Operator scenarios — Journey E](../getting-started/operator-scenarios.md#journey-e).

<figure class="ph-figure" markdown>
  ![Network Hosts map](../assets/screenshots/dns-physical.png)
  <figcaption>Hosts map — home ring vs cloud hosts (light desktop).</figcaption>
</figure>

<figure class="ph-figure" markdown>
  ![Network Path map](../assets/screenshots/dns-logical.png)
  <figcaption>Path map — name → proxy → host → service flow.</figcaption>
</figure>

<figure class="ph-figure" markdown>
  ![Network Hosts map mobile](../assets/screenshots/dns-physical-mobile.png)
  <figcaption>Optional mobile showcase — Hosts map list-first layout.</figcaption>
</figure>

---

## Mental model — entities & relationships

One published **name** maps through optional layers:

```text
name  →  [NPM]  →  host  →  [service/project]  →  [container]
```

| Entity | Example | Notes |
|--------|---------|--------|
| **Name** | `grafana.example.com` | CNAME, or **host identity A** when name = host FQDN |
| **NPM** | RPI5-3 | Only when proxied (edge host) |
| **Host** | RPI5-6 | Fleet server + A record |
| **Service** | `grafana` | Compose project (Kuma / NPM / deploy) |
| **Container** | `grafana` | Runtime container |

### Path kinds

| Kind | UI label (hub stats) | Meaning |
|------|----------------------|---------|
| **host_identity** | **Host** | Name **is** the host A record (e.g. `3dprint.example.com`) — **no CNAME** |
| **app** | **App** | CNAME → host → Docker project/container (e.g. Grafana) |
| **npm_host** / **npm_app** | **NPM** | CNAME → NPM edge → host (and optional project/container) |

On the Network hub stat strip, **By path type** shows counts for Host / App / NPM (how published names reach a host). Hover the card for a short definition. Service path filters still use the finer path-kind chips (All / Via NPM / Direct / Host identity).

---

## Network hub layout

The hub (`/dns`) is intentionally dense in **v0.8** — path cards, host A table, external DNS checklist, network map settings, and Pi-hole adopt live on one page for operators who already know the fabric.

| Block | What it is |
|-------|------------|
| **Nav cards** | Jump to Kuma coverage, Hosts map, Path map |
| **Stat strip** | Hosts named · Mapped names · Via NPM · **By path type** (Host / App / NPM) |
| **Service paths** | Searchable path cards (name → layers → Stack / maps) |
| **Host DNS** | Fleet A records (server FQDN / manage A) |
| **External DNS** | Checklist for Cloudflare/etc. (not automated) |
| **Network map settings** | LAN CIDR, gateway, public IP, optional Kuma binds |
| **Adopt existing DNS** | Import / candidates from Pi-hole |

!!! note "UX polish (v0.9)"
    Host DNS, External DNS, Network map settings, and Adopt will move toward **modals / drawers** so the hub stays path-first — [PLAN_v0.9.0.md](https://github.com/bjorngluck/piherder/blob/main/docs/PLAN_v0.9.0.md). Behaviour of each section stays the same.

---

## Network map settings

On the **Network hub** (`/dns`), configure home topology used by the Hosts map:

| Field | Example | Used for |
|-------|---------|----------|
| **LAN subnet** | `192.168.86.0/24` | Home host ring; IPs **outside** CIDR → **cloud** |
| **Gateway / router IP** | `192.168.86.1` | Router node between Internet and LAN |
| **Public (WAN) IP** | looked up or manual | Shown on Internet / Public IP nodes |
| **Lookup public IP now** | — | Outbound check from the PiHerder host |
| **Router Kuma monitor** | optional | Status chip + deep link on **Router** |
| **Public IP Kuma monitor** | optional | Status on **Public IP** / Internet |

### Hosts map layout

```text
Internet (☁) ── WAN ── Router (on zone rim)
                         │
                    LAN badge (centre)
                   /  |  |  \
            fleet hosts on a **fan / circle**   ← compact zone
         · · · discovered chips on outer rings · · ·  ← expands when “Network / discovered” is on

Mapped apps fan **outside** the zone from fleet hosts.
```

- **Spine always drawn** when fleet hosts exist (even if LAN/gateway settings are empty).
- Without a LAN CIDR: **RFC1918 / CGNAT** addresses stay on LAN; other addresses are **cloud**.
- **Fleet fan** — managed hosts on a ring around the LAN badge (size scales with fleet count).
- **Discovered (radar) toggle** — **off** = compact zone (fleet only); **on** = zone expands to outer rings of discovery chips. Internet / Router sit on the **active** zone rim; fleet fan positions stay fixed.
- Cloud hosts sit beside the Internet cloud (not on the LAN fan).

---

## Map pages

| Page | URL | Shows |
|------|-----|--------|
| **Network hub** | `/dns` | Path cards · **By path type** stats · filters · network settings · adopt/import · host A table · external checklist |
| **Hosts map** | `/dns/physical` | Rack cards + SVG: Internet → Router → **LAN fan** + apps; radar expands the circle for discovery |
| **Path map** | `/dns/logical` | Flow list (mobile-first) + SVG (URL → NPM hub → destination) |

### LAN discovery on Hosts map {#lan-discovery-on-hosts-map}

After **[LAN Discovery](lan-discovery.md)** has scanned, **unlinked** devices appear on the Hosts map **automatically**. You do **not** need to link each device to a Server for it to show — that is the whole-LAN end-to-end view.

| Topic | Behaviour |
|-------|-----------|
| **Toggle** | **Radar** icon in the one-line map chrome (next to zoom / full screen), default **on**; browser `localStorage`. Count is in the **footer** + tooltip, not a toolbar badge |
| **Layout** | Fleet on compact fan; toggle expands zone to outer discovery rings; apps / spine dual-layout with the zone |
| **Labels** | Operator **map name** (e.g. `cctv1`) → scan hostname → IP — set in LAN Discovery **edit modal** (Network or Devices) |
| **Kind** | Heuristic or **operator override** badge (printer, Pi, camera, …) — never auto-promotes |
| **Gateway** | Device map role **Gateway / router** labels the Router spine and sets network gateway IP; that IP is not double-drawn as a LAN chip |
| **Dedup** | Same IP as a fleet server, already **linked**, or map-role gateway / network gateway IP → not a second discovery chip |
| **Ignored** | Stay off the map |
| **Tap** | Opens LAN Discovery **Network** edit modal with **← Hosts map** return after save/close |
| **Requires** | nmap integration + devices from a scan |

LAN Discovery’s own **Network** tab remains a discovery-only subnet browser (**Show unlinked** ≠ Hosts radar). Device naming / type / known / ignore: [LAN Discovery — edit modal](lan-discovery.md#edit-modal-network--devices).

### Map chrome (Hosts + Path)

| Control | What it does |
|---------|----------------|
| **Hide map** (mobile) | Return to list-first layout |
| **Discovered** (Hosts only, radar) | Outer discovery chips on/off + compact vs full LAN zone |
| **− / % / +** | Zoom out · level · zoom in (SVG viewBox) |
| **1:1** | Fit map to the window. Hosts + discovered **off**: fits the **compact** fleet (fills the pane). Discovered **on** / Path map: designed full canvas. Double-click map = same |
| **Full screen** | Expand map; Esc or control again to leave |

### Focus, zoom & mobile

- **Hover** (mouse/stylus) any **host** (including Nomad with no mapped services), **Router**, **LAN**, **Internet**, **Public IP**, or **app path** to **preview** highlight.
- **Click / tap** to **lock** focus — the path stays highlighted when the pointer leaves. Click the same node again or **Clear focus** to unlock.
- Hosts **without** mapped services are still selectable (node focus). App satellites focus the service **path**.
- **Open host** / **Open in Kuma** appears when the focused node has a link (same-tab for fleet hosts; new tab for external Kuma).
- **Copy path** copies the callout route string.
- Maps: **pinch** / scroll-wheel zoom up to **500%** (SVG **viewBox** — stays sharp), **drag** to pan; see chrome table above.
- Status dots: **green** = last Pi-hole sync ok · **amber** partial · **red** error · small amber ring = managed cert linked · Kuma **up/down** on Router / Public IP when bound.  
- Path cards also show **Kuma coverage** (see below).
- Deep links: `/dns/physical?focus=<service_id|#map>` and `/dns/logical?focus=…#map` (also from each path card / dashboard / Docker **Path map** pills). Deep links **auto-open** the SVG on mobile.
- On **narrow screens**, maps default to the **list** (racks / flows). Use **View full map** for the SVG; use **Hide map** on the graph toolbar to return to list-first density.
- **Hamburger while fullscreen:** the slide-out menu is portaled to `body` and sits **above** map fullscreen. Opening **☰** fully exits fullscreen (label, listeners, and viewport sizes reset) so the drawer is never painted off-screen.
- **Portrait ↔ landscape:** maps call `PiHerderFabric.refreshLayout` (with the global viewport reflow) so SVG heights, zoom, and page width rescale without leaving the page. Path hop chips **wrap** within each card (no horizontal swipe per card).
- Hub and path map support **search** and path-type filters (All / Via NPM / Direct / Host identity).
- **Adopt candidates** load after the hub paints (HTMX → `/dns/candidates`) so a slow or down Pi-hole does not block path cards / host DNS.
- Hosts map caps app satellites per host (then a **+N more** marker); full app list stays on rack cards.
- **Docker UI:** project **Path map** links use a cheap, **case-insensitive** project index (no full access-path resolve on HTMX stack polls).

### Runtime stack (detail altitude)

Maps stay **customer-facing** by default. For **one** focused service (or host project), open the **Stack** panel and/or map **expand** to see containers, categories, tags, Kuma binds, and runtime links.

| Surface | What you get |
|---------|----------------|
| **Stack panel** | Modal/drawer: containers (category, tags, running, Kuma), **view group** pills, detail expand, suggested/confirmed edges, accept/dismiss/manual link, **Refresh** inventory, deep links to Server / Service / Docker / maps |
| **Map expand** | On Path map or Hosts map focus: sideways fan to the right of the path — **not** a fleet-wide container mesh. With **All** view groups and 2+ groups populated, one fan per group. |

Compose **project** identity is exact (case-insensitive) for annotation storage. Soft substring match (e.g. conflating unrelated project names) is not used.

Summary chips: **depends_on** means inventory parsed compose `depends_on` (feeds suggested links). If suggestions exist, the chip jumps to **Suggested links** in the panel — it is not a separate page.

#### Labels (category + tags)

| Label | Rules |
|-------|--------|
| **Category** | One per container; drives **map columns**. Fixed list (edge, app, queue, cache, data, tooling, …). Default = heuristic from name/image; override in panel detail → **Save labels**. |
| **Tags** | Multi chips from a fixed list (web, db, worker, proxy, test, …). Not free text. Add new entries via `POST /dns/vocab` (operator). |

#### Visual service stacks and view groups {#visual-service-stacks--view-groups}

Under **one compose project**, create **view groups** (e.g. **Main** vs a custom group) and **move** containers between them. Deploy / stop / start still act on the **whole compose project** (or a [compose set](../docker/overview.md#compose-sets-same-folder-one-project-card) on the Docker page) — view groups are **presentation only**.

Panel pills: **All** · **Main** (unassigned) · (your groups). Compact segmented control styling. Map expand respects the same filter; **All** draws multiple fans when more than one group has members.

**Main** = containers with no view-group assignment. Assigning every container to a named group leaves Main empty (expected).

**vs Docker compose sets:** compose sets = files on disk under one folder. View groups = labels for how you look at containers on Network maps.

#### Map expand layout

```text
  focused path ──►  edge → app → queue → cache → data → tooling  (enabled categories that have containers)
```

- Column order follows **category vocabulary** sort order (empty columns hidden).  
- Role colors + type chips on boxes; confirmed dependency curves; soft structure lines between **adjacent** columns only.  
- **No Server / Service / Docker chips on the map** — use the Stack panel for navigation.  
- Click a container box → opens Stack panel focused on that container.

#### Reorder containers (operators)

1. Open **Stack** for the service/project (optionally filter to a **view group** first).  
2. **Desktop:** drag the **⋮⋮** handle. **Mobile:** long-press a row, then drag.  
3. Order is saved in the DB (`containerannotation.sort_index`) and dual-written to `stack_container_order_json` for compatibility.  

**View groups keep independent order:** reordering while **Main** or a named group is selected only updates that list — it does **not** wipe the other group. Reordering under **All** replaces the full project order. Separate compose projects (`piherder` vs `piherder-e2e`) never share order keys.

**Effect on the map:** with a custom order, **column left→right** can follow that order (by earliest container in each category). Example: put **celery last** in the panel → **queue column moves right**.

!!! note "Still later"
    Per-project column profiles and explicit edge→column layout rules remain residual. See [FEATURE_PLAN_RUNTIME_TOPOLOGY.md](https://github.com/bjorngluck/piherder/blob/main/docs/FEATURE_PLAN_RUNTIME_TOPOLOGY.md) § 12b–12c.

### Light / dark theme

Infrastructure nodes (Internet cloud, Router, LAN, NPM hub) use theme-aware fills (no default black SVG fill). Zoom chrome stays readable in light mode.

---

## Setup

1. **Base domain** (optional) on Catalog → Network (e.g. `example.com`) for name suggestions.  
2. **Network map** — set LAN CIDR, gateway, public IP (or **Lookup**); optionally bind Router / WAN Kuma monitors (poll Kuma first so the dropdown is populated).  
3. **Host DNS** — each server **Edit → General**: FQDN + IP; tick **Manage A on all Pi-holes** (creates/updates A; duplicates treated as success).  
4. **Import existing names** — Catalog → Network → **Import all from Pi-hole** (or Adopt per row after candidates load). Existing CNAMEs are mapped; Pi-hole is **not** recreated when the record already exists.  
5. **Host identity** — when the app name equals the host A name (Kuma host-level service, no Docker), use **Map host identity** (A only).  
6. **Template deployments** — Service DNS card attaches an inferred plan (one FQDN field when needed).  
7. **External DNS** — checklist on the hub for Cloudflare/etc. (not automated in 0.5.0).

---

## Pi-hole behaviour

| Action | Behaviour |
|--------|-----------|
| Host A / service CNAME create | Fans out to **all enabled** Pi-holes |
| Record already present | Treated as **success** (adopt / re-sync safe) |
| Remove service **CNAME** mapping | Deletes CNAME on Pi-holes when managed |
| Remove **host identity** mapping | Does **not** delete host A (owned by server Host DNS) |

Audit actions include `dns_host_*`, `dns_service_cname_sync`, `dns_service_a_sync`, `dns_service_delete`.

---

## Uptime Kuma coverage (v0.6+)

**Catalog → Network → Kuma coverage** (`/dns/coverage`) is a **dedicated page** (not the whole hub — keeps maps + paths scannable).

The hub shows a **teaser card** with path/dep gap counts. Full audit, binds, filters, and stack dependencies live on the coverage page.

| Status | Meaning |
|--------|---------|
| **Covered** | Service-role binding matches FQDN / Docker project (or a clear host-scoped service monitor) |
| **Partial** | Host has SSH reachability only, or a weak/label-only match |
| **Gap (none)** | No useful Kuma binding on the backend host for this name |

Path cards show a small **Kuma** / **Kuma·** / **no Kuma** chip.

### Binding from the gaps table

For each gap (operators only):

1. **Poll** Kuma on the integration if the monitor list is empty.  
2. Choose a **Suggested** (or other) HTTP monitor — ranked by FQDN / name / URL.  
3. Click **Bind** — creates a service binding on the **backend host** with the path’s Docker project when known, then returns to Network coverage.  
4. **Advanced…** opens the full Kuma “Add service binding” form with server / project pre-filled.

This does **not** create monitors inside Kuma — only **links** an existing monitor to a fleet host/project. Create the HTTP check in Kuma first ([Uptime Kuma](uptime-kuma.md)).

### Stack dependencies (Docker inventory)

Below path coverage, **Stack dependencies** lists **compose containers** from host Docker inventory (not only published FQDNs):

| Status | Meaning |
|--------|---------|
| **Bound** | Kuma service bind matches project (and container when set) |
| **Suggest bind** | No bind — pick TCP/HTTP monitor; host ports shown when published |
| **Muted / infra** | Postgres, Redis, MySQL, Mongo, … (name/image heuristics) **or** operator **Mute** |

**Show infra** toggles whether DB/cache roles appear as suggestions (default **hidden** — they are not public path monitors; a TCP/Postgres check needs a port reachable from Kuma).

**Path gap filters:** All · Hard gaps · Public/apps · Strict (drops host-identity partial noise).

### Monitoring Postgres (example)

1. Ensure Kuma can reach the DB (publish port carefully, or put Kuma on the same Docker network).  
2. Create a **TCP** or **Postgres** monitor in Kuma (connection string stays in Kuma).  
3. Network → coverage → **Show infra** if needed → **Bind** to `project` / `db` container.  
4. Or keep DB muted and rely on app HTTPS + host SSH.

!!! note "Availability"
    Coverage audit + dependency suggest: **v0.6.0+**. Requires enabled Uptime Kuma + Docker inventory on hosts.

---

## Data model (summary)

| Setting / field | Storage | Notes |
|-----------------|---------|--------|
| `network_lan_subnet` | App settings | CIDR |
| `network_gateway_ip` | App settings | Router internal IP |
| `network_public_ip` (+ checked_at) | App settings | WAN IP |
| `network_gateway_kuma_external_id` | App settings | Kuma monitor id/name |
| `network_public_kuma_external_id` | App settings | Optional WAN monitor |
| `network_kuma_integration_id` | App settings | Empty = first enabled Kuma |
| **Server** | `dns_name`, `dns_manage_a`, `dns_ip_override` | Host A |
| **ServiceDnsRecord** | FQDN, `record_type` (`cname` \| `a`), servers, project, NPM, sync | Service path |
| **RuntimeEdge** | Confirmed/manual/suggested stack dependency edges | Panel + map expand; herder backup |
| **TopologyCategory / TopologyTag** | Fixed vocab for category + tags | Seeded; operator can add |
| **VisualServiceStack** | Visual group under one compose project | Presentation only |
| **ContainerAnnotation** (+ tags) | Category override, visual stack, order, tag set | Herder backup |
| `stack_container_order_json` | App settings | Dual-write fallback for order |
| `stack_inventory_down_alerts` | App settings | Optional alert when Kuma-bound container is down in inventory |

Resolution also uses Pi-hole inventory, NPM poll cache + proxy_host binds, Kuma service binds, Docker inventory (compose graph v2), and stack deployments.

**Code:** `app/services/dns_fabric/` · `container_annotations.py` · `stack_order.py` · `compose_graph.py` · `runtime_edges.py` · `stack_monitor.py` · `app/routers/dns.py` · `fabric-mesh.js` / `fabric-stack-*.js` · `fabric.css` · `dns_*.html`  
**Tests:** `tests/test_dns_fabric.py` · `test_kuma_coverage.py` · `test_stack_*.py` · `test_container_annotations.py` · `test_compose_graph.py` · `test_runtime_edges.py`

---

## Related

- [LAN Discovery (nmap)](lan-discovery.md) — whole-LAN devices, map identity (name / type / gateway), Hosts map overlay  
- [Pi-hole](pihole.md)  
- [NPM](npm.md)  
- [Uptime Kuma](uptime-kuma.md)  
- [Certificates](certificates.md)  
- [v0.5.0 plan § F.1](https://github.com/bjorngluck/piherder/blob/main/docs/PLAN_v0.5.0.md)  
- Roadmap H2.5 + runtime topology plan (expand stack, suggest/manual deps): [FEATURE_PLAN_RUNTIME_TOPOLOGY.md](https://github.com/bjorngluck/piherder/blob/main/docs/FEATURE_PLAN_RUNTIME_TOPOLOGY.md) · [ROADMAP_ECOSYSTEM.md](https://github.com/bjorngluck/piherder/blob/main/docs/ROADMAP_ECOSYSTEM.md)
