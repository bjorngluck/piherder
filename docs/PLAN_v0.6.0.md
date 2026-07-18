# PiHerder v0.6.0 — RC2 development plan

**Status:** **Active** (implementation target)  
**Date opened:** 2026-07-17 · **Refreshed:** 2026-07-18 (H closed; F bulk Docker done; **D cert UX musts done**; next **B wizard**)  
**Baseline:** `v0.5.0` (first RC — tagged 2026-07-17)  
**Package target:** `0.6.0` (`pyproject.toml` · `app/version_info.py`)  
**Related:** [PLAN_v0.5.0.md](PLAN_v0.5.0.md) · [RELEASE_v0.5.0.md](RELEASE_v0.5.0.md) · [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · [FEATURE_PLAN_HOST_LIFECYCLE.md](FEATURE_PLAN_HOST_LIFECYCLE.md) · [FEATURE_PLAN_RUNTIME_TOPOLOGY.md](FEATURE_PLAN_RUNTIME_TOPOLOGY.md) · [FEATURE_PLAN_TEMPLATES.md](FEATURE_PLAN_TEMPLATES.md) · [FEATURE_PLAN_PIHOLE_NPM_CERTS.md](FEATURE_PLAN_PIHOLE_NPM_CERTS.md) · [SPEC.md](../SPEC.md)

### Decision (locked)

| Choice | Value |
|--------|--------|
| Git tag target | **`v0.6.0`** (second release candidate / RC2) |
| Theme | **RC1 polish** — more intuitive operator paths, docs depth, light performance |
| Planning frame | Single target; pull **selected** H2.75 / pre-1.0 items that improve first-week UX |
| Production path | ~~v0.5.0 RC1~~ **live** → **v0.6.0 RC2** → **v1.0** refined production |
| Docs strategy | Living wiki + `RELEASE_v0.6.0.md` at freeze (no wiki fork) |

---

## 1. Theme

**RC2 — polish what RC1 shipped.** No second large product wave. Make the existing control plane feel obvious for a new operator:

1. **Server onboarding** that guides order of operations  
2. **Templates** that feel consistent with other long-running jobs  
3. **TLS cert vault → service maps** that are easier to set up and reason about  
4. **Documentation** that matches the UI and closes RC1 screenshot / prose gaps  
5. **Slight performance** wins where pages already feel sluggish  

Pull from the **v1.0 / H2.75** backlog only where it directly serves that theme (wizard, optional Docker bulk, docs freeze progress). Defer high-risk surface area (web SSH, first-boot phone-home, ACME-in-herder).

---

## 2. What RC1 already delivered (do not re-build)

| Area | v0.5.0 state |
|------|----------------|
| Templates | Deploy wizard, desired state, drift, env import, last-known config, volume editor |
| Certs | NPM pull + PEM upload, service maps, layouts, renew loop (NPM), herder backup |
| Onboarding | Add-server form + SSH access panel (deploy key, least-priv, deps) — **capable, not guided** |
| Docs | Live wiki + multi-arch Hub image; screenshots / freeze bar still open polish |
| Connectors | Pi-hole multi, NPM RO, Network maps, Catalog hub |

RC2 **orchestrates and clarifies**; it does not replace the SSH/template/cert engines.

---

## 3. Workstreams (recommended order)

### A — Documentation polish (parallel, continuous)

| Item | Notes | Priority |
|------|--------|----------|
| RC1 operator QA residual | Walk PLAN_v0.5.0 freeze checklist; file bugs into B–E or “known limitations” | Must |
| Wiki screenshot refresh | Critical light-desktop paths: install, add-server, templates deploy, certificates maps, Network, About | Must |
| Dual TLS story clarity | Split mental model: **(1)** Caddy / `certs/` for PiHerder edge · **(2)** Catalog Certificates for fleet vault + maps | Must |
| Wizard + cert map docs | Update [add-server](../wiki/day-to-day/add-server.md), [certificates](../wiki/integrations/certificates.md), operator scenarios when UX lands | Must |
| Install / first-login / roles / env accuracy | Align with 0.6 behaviour; version callouts where needed | Must |
| ADMIN / SPEC / ROADMAP sync | Point production path at 0.6; tick Phase 6.5 items as they land | Must |
| Toward v1.0 docs bar (partial) | Progress [contributing-docs](../wiki/developers/contributing-docs.md) freeze items without claiming 1.0 | Nice |

**Deliverable at tag:** this plan frozen + `RELEASE_v0.6.0.md` + wiki pages updated in the same cycle as features.

---

### B — Server onboarding (from H2.75 P2)

**Problem:** Today is form → server detail → SSH access panel → features/schedules/DNS. Correct order is tribal knowledge; password bootstrap can linger.

**Solution:** Guided multi-step wizard that **reuses** existing SSH / feature / DNS endpoints (orchestration only). Design already locked in [FEATURE_PLAN_HOST_LIFECYCLE.md](FEATURE_PLAN_HOST_LIFECYCLE.md) § Phase 2.

| Step | Content |
|------|---------|
| 1 Identity | Name, hostname/IP, port, SSH user |
| 2 Trust | Generate/upload key; optional one-time password |
| 3 Connect | Deploy key → Test connection → clear password CTA |
| 4 Privilege | Least-priv (Debian/Pi OS) or skip (HAOS) + Docker base dir if needed |
| 5 Features | Backups / OS / Docker toggles |
| 6 Schedules | Checks only by default |
| 7 Network | Optional FQDN + Manage A on Pi-holes |
| 8 Done | Summary + CTAs (first backup, update check, open Docker) |

| Rule | Detail |
|------|--------|
| Entry | Primary **Add server (wizard)**; keep advanced/single-form as escape hatch or fold into wizard |
| Save & exit | Partial server allowed mid-flow (same as today) |
| No new trust model | Same encrypt, audit, RBAC as current SSH panel |
| Bootstrap scripts | Optional **stretch**: downloadable pre-join scripts with embedded public key (H2.75 P4 A) — only if wizard lands early |

**Acceptance:** New operator can complete first host without reading the wiki first; wiki happy path documents the wizard; pytest/RBAC for new routes.

**Key files (expected):** `app/routers/servers.py` · `server_ssh.py` · `ssh_onboarding.py` · `add_server.html` · new wizard templates · `wiki/day-to-day/add-server.md` (repo paths)

---

### C — Templates polish (intuition + job UX)

Foundation is production-shaped; RC2 makes long ops and post-deploy feel like the rest of the product.

| Item | Notes | Priority | Status |
|------|--------|----------|--------|
| **Template deploy as Jobs + live log** | Mirror stack Deploy/Check (B07): enqueue job, JobHold modal, exclusive stack mutation where needed | **Must** | **Done** (2026-07-17) |
| Redeploy as Jobs | Primary redeploy path via `template_redeploy` Job + JobHold | Should | **Done** (with deploy) |
| Drift check as Jobs | Keep short “check” snappy unless slow in practice | Should | Open |
| Post-deploy CTAs | After success: open deployment (redirect_url), Docker project, optional “bind Kuma / NPM / add cert map” | Should | Partial (redirect to deployment) |
| Wizard copy / empty states | Clearer volume/boolean help; fail banners with next step | Should | Open |
| Git template catalog pull | Model already has `source=git` — **stretch only** if time | Stretch | Open |
| Expanded OOTB pack (Frigate, HA, …) | **Out** — Horizon 3 | Out | Out |

**Acceptance:** Deploy/redeploy never holds a long HTTP request as the only progress channel; operators see job + audit trail; wiki deploy page updated.

**Key files:** `app/services/service_templates/deploy.py` · `templates_deploy.py` · jobs package · `template_deploy.html` · `template_deployment.html` · `tests/test_service_templates.py`

---

### D — TLS cert setup & distribution mapping

Vault + maps shipped in 0.5; RC2 makes **setup and mapping** obvious.

| Item | Notes | Priority | Status |
|------|--------|----------|--------|
| **Onboarding path for certs** | Guided first-cert flow: `/certificates/setup` + step banners (map → deploy) | **Must** | **Done** (2026-07-18) |
| **Map presets expansion** | NPM, Caddy, Docker bind, **OctoPi/HAProxy**, **Grafana volume**, UniFi PFX + live path preview | **Must** | **Done** |
| **OctoPi / host TLS cookbook** | Staging under `piherder` home + sudoers + combined `snakeoil.pem` + `systemctl restart haproxy` | **Must** | **Done** (docs + UI preset) |
| **Grafana volume TLS cookbook** | Pair → home stage → sudo install into volume + compose restart | **Must** | **Done** (docs + UI preset) |
| **Distribution visibility** | List hosts + deploy counts; map cards in-sync / stale fingerprint; empty CTAs | Should | **Done** |
| **Multi-map deploy as Job** | “Deploy all maps” / renew redistribute via Jobs + live log (long multi-host SSH) | Should | Open |
| **DNS fabric link** | Optional: from service DNS / path, deep-link “linked certificate” or “suggest map” when FQDN matches vault domains | Nice |
| **Upload-source expiry** | Remind/notify on expiry for upload certs (no false NPM renew) | Nice |
| Cross-link template → cert | After NPM template deploy, CTA “Pull certs / add maps” | Nice |
| ACME inside PiHerder | **Out** (use NPM or external LE) | Out |
| Delete remote files on map remove | **Out** (document only; destructive) | Out |

**Docs:** Emphasize two TLS surfaces; cookbook examples (NPM custom SSL, UniFi PFX) with screenshots.

**Key files:** `app/services/certificates.py` · `app/routers/certificates.py` · `certificates_*.html` · `wiki/integrations/certificates.md` · `wiki/getting-started/https-tls.md` · `tests/test_certificates.py`

---

### E — Slight performance tweaks

Target **noticeable operator-facing lag**, not a rewrite.

| Item | Notes | Priority |
|------|--------|----------|
| Template / cert long ops → Jobs | Avoid blocking request threads (ties C + D) | Must (via C/D) |
| Servers / Catalog list queries | Ensure list pages stay pure-DB; avoid accidental SSH or N+1 on list | Should |
| Docker inventory | Review L1 refresh / force-refresh UX; avoid double full collect on tab spam | Should |
| DNS fabric pulse vs full mesh | Keep hub/dashboard on cheap counts; full mesh only on map pages | Should |
| Cert renew poll | Soften blocking sleep loop if it holds a worker slot badly (requeue/backoff) | Nice |
| HTML/JS payload | No big framework work; only fix regressions (SW cache, unused assets) | Nice |

**Out:** New metrics product, continuous host polling, multi-tenant scale work.

---

### F — Optional pull-ins from H2.75 / pre-1.0 (high value, low risk)

Ship **only if** B–D are green and schedule allows. Prefer one clean slice over half-finished P3–P5.

| Item | Horizon | Recommendation for 0.6.0 |
|------|---------|---------------------------|
| **Docker project bulk** start/stop/restart | H2.75 P1 | **Done** (2026-07-18) — project ⋯ Stop/Start/Restart all as Jobs |
| Bootstrap scripts (P4 A) | H2.75 | Stretch with wizard |
| Host stats / allowlisted commands (P3) | H2.75 | **Defer** to post-0.6 or 0.7 |
| Web SSH (P5) | H2.75 | **Out of 0.6** |
| First-boot enrollment (P4 D) | H2.75 | **Out of 0.6** |
| Playwright smoke Phase A | Quality | Nice-to-have CI job, not ship blocker |
| Custom password policy | Quality | **Out** (or post-0.6) |
| pip-audit / Dependabot | Quality | Nice |

---

### G — Fleet / RC1 residual polish (as found)

During RC1 operator use, small fixes land here without opening new horizons:

- Bugfixes from freeze checklist failures  
- Mobile / ops-hero consistency regressions  
- Audit/Jobs copy for new job types  
- Version bump + Hub tags `0.6.0` / `0.6` / `latest` at freeze  

---

### H — Discovery & visualisation (**stream closed for 0.6**)

**Captured:** 2026-07-17 · **Closed for 0.6 ship:** 2026-07-18.  
**Stance:** Offline-first slices that reuse Docker inventory + Kuma shipped as RC2 stretch. Active LAN scan is **not** in 0.6 — parked for **v0.8.0**.

| # | Idea | Intent | Status |
|---|------|--------|--------|
| **H3** | **Coverage audit (Kuma)** | Paths + inventory deps vs Kuma; mute infra; bind/suggest | **Done** — `/dns/coverage` + hub teaser; path chips |
| **H2** | **Runtime topology** | Ports, suggest/manual deps, expand-one-stack — [FEATURE_PLAN_RUNTIME_TOPOLOGY.md](FEATURE_PLAN_RUNTIME_TOPOLOGY.md) | **Done** (P0–P5 + stack order) |
| **H1** | **LAN discovery (nmap-class)** | Periodic scan of LAN CIDR: IPs, MACs, open ports; link to servers / fabric | **Moved to v0.8.0** — not in 0.6 |

#### H2 — Runtime topology (ports, deps, expand stack) — **shipped**

Full plan (archive + residual): **[FEATURE_PLAN_RUNTIME_TOPOLOGY.md](FEATURE_PLAN_RUNTIME_TOPOLOGY.md)** · operator wiki: [Network maps — Runtime stack](../wiki/integrations/dns-fabric.md#runtime-stack-detail-altitude).

| Shipped in 0.6 | Detail |
|----------------|--------|
| Stack side panel | Containers, ports, mute/bind, suggested + confirmed edges |
| `RuntimeEdge` | Accept / dismiss / manual; herder backup |
| Inventory enrich | Compose `depends_on` + graph in Docker inventory L1 |
| Map expand | Sideways fan (edge · app · queue · data); soft adjacent columns |
| Stack container order | Long-press/drag; drives column L→R (e.g. celery last → queue rightmost) |
| Monitor depth | Optional inventory-down alert when Kuma-bound container is down |

**Not done — later (not 0.6 ship-bar):**

| Residual | When |
|----------|------|
| User-configurable map columns / pin roles | Later (topology § 12b) |
| Link-to-column layout rules | Later |
| Per-stack layout profiles | Later |
| Cross-host manual picker polish | Later polish |
| Broader Hosts/Path port chips | Later polish |
| Shared-service catalog polish (P6) | Later / with discovery |

#### H3 — Monitoring coverage audit (Uptime Kuma) — **shipped**

| Aspect | Direction |
|--------|-----------|
| **Value** | “Is this watched?” before silent failure |
| **Logic** | Fabric paths + Docker containers vs Kuma bindings; infra mute; TCP suggest |
| **UI** | `/dns/coverage` + hub teaser card; path-card Kuma chips |
| **Scope** | Bind existing monitors only — no auto-create in Kuma |
| **0.6** | **Shipped** |

#### H1 — Network scan (nmap / equivalent) — **v0.8.0**

| Aspect | Direction |
|--------|-----------|
| **Value** | Unknown devices, orphan IPs, ports not yet in PiHerder; suggest add-server / fabric adopt |
| **Surfaces** | Catalog → Network (or Settings → Discovery): last scan table; link/unlink to `Server` / DNS records |
| **Scheduler** | **Opt-in, default off**; scope = Network **LAN CIDR** only; never WAN by default |
| **Engine** | Prefer light TCP/ARP probes first; full `nmap` binary/sidecar later if needed (`NET_RAW` tax) |
| **Risks** | Active scan policy; container capabilities; large-subnet load; no auto-mutate hosts |
| **Release** | **v0.8.0** — design + product (orthogonal to stack deps). **No work in 0.6** |

**H* summary:** H3 + H2 **done** · H1 **→ v0.8.0** · residual column/layout polish **later**.

**Non-goals (all releases):** replace Kuma continuous monitoring; internet-wide scan; auto-add untrusted hosts from scan; require nmap for core fleet ops.

---

## 4. Explicitly out of v0.6.0 (must-ship)

| Area | Reason |
|------|--------|
| Web SSH console | High risk; separate ship bar (H2.75 P5) |
| Agent-based management | Non-goal (SSH-first) |
| ACME / Let’s Encrypt inside PiHerder | Use NPM or external |
| NPM proxy host **write** CRUD | Later |
| Cloudflare DNS automation | H2.5+ |
| Service migrate host→host / destructive wipe | H2.5 |
| Full topology plugins / configurable columns | Later residual of H2 (see runtime topology § 12b) |
| **LAN discovery / nmap product (H1)** | **v0.8.0** — not in 0.6 |
| Large curated template pack expansion | H3 |
| Kubernetes / bare install as supported | Under consideration only |
| Multi-tenant orgs · optional AI · custom branding | Far horizon |
| Full v1.0 process freeze | Goal of **1.0**, not RC2 (progress only) |

---

## 5. Ship bar (`v0.6.0` tag)

### Must-have

1. **Add-host wizard** (workstream B) — primary path documented  
2. **Template deploy (and primary redeploy path) as Jobs + live log** (C)  
3. **Cert setup / mapping UX polish** — guided first cert + clearer maps + presets (D)  
4. **Docs:** dual TLS clarity, wizard + cert cookbooks, critical screenshot refresh, `RELEASE_v0.6.0.md`  
5. **pytest** green; smoke: wizard host, template deploy job, cert map deploy  
6. Version `0.6.0` + git tag + multi-arch Hub publish (`0.6.0` / `0.6` / `latest`)  
7. Secret-path review unchanged (step-up 2FA, no cleartext audit, host `.env` 600)

### Shipped early (stretch — not required for freeze, already on main)

- **H3** Kuma coverage audit (`/dns/coverage`)  
- **H2** Runtime topology (stack panel, edges, map expand, container order, bound-container down alerts)  

### Nice-to-have in the same tag

- ~~Docker project bulk stop/start/restart (F / P1)~~ **Done**  
- Multi-map cert deploy as Job  
- Bootstrap script download from wizard  
- HTTP smoke or Playwright Phase A  
- Git catalog pull  

### Explicitly later releases

- **H1** LAN discovery / nmap-class scan → **v0.8.0**  
- Configurable map columns / link-to-column → post-0.6 residual  


### QA smoke (operator)

- [ ] New host via **wizard** end-to-end (key deploy, least-priv or HAOS skip, features, optional DNS)  
- [ ] Advanced add path still works (if kept)  
- [ ] Template deploy → JobHold live log → deployment page + drift check  
- [ ] Cert: NPM pull **and** PEM upload → add map (preset) → deploy → files on host  
- [ ] Cert renew / deploy-all still works for NPM auto-renew  
- [ ] Catalog Certificates list empty states + map status readable  
- [ ] Wiki: https-tls vs managed certificates; add-server wizard; cert cookbook  
- [ ] `pytest` green in container  
- [ ] `mkdocs build --strict` green  
- [ ] Hub multi-arch publish + compose pull of `0.6.0`  
- [ ] About shows **0.6.0**

---

## 6. Suggested implementation order

```text
1. PLAN + ROADMAP/SPEC production path          // done
2. Template deploy → Jobs (C)                   // done
3. H3 coverage + H2 runtime topology            // done (stretch stream closed)
4. Docker bulk lifecycle P1 (F)                 // done — stop/start/restart all as Jobs
5. Cert map UX + first-cert guidance (D)        // done — setup guide + presets + map status
6. Add-host wizard (B)                          // next must (largest UI)
7. Docs/screenshots continuous (A)
8. Perf pass on list/inventory/fabric (E)
9. Freeze: RELEASE + version + Hub + tag
```

**Next focus for 0.6 ship-bar:** workstream **B** (add-host wizard). Topology, Docker bulk (F), and cert UX (D) closed for musts.

Parallelise A with remaining feature streams.

---

## 7. Docs map (this cycle)

| Doc | Role |
|-----|------|
| **This file** | Living ship plan |
| [RELEASE_v0.6.0.md](RELEASE_v0.6.0.md) | Written at freeze only |
| [FEATURE_PLAN_HOST_LIFECYCLE.md](FEATURE_PLAN_HOST_LIFECYCLE.md) | Wizard / bulk design authority |
| [FEATURE_PLAN_RUNTIME_TOPOLOGY.md](FEATURE_PLAN_RUNTIME_TOPOLOGY.md) | H2 stream closed for 0.6; residual later |
| [FEATURE_PLAN_TEMPLATES.md](FEATURE_PLAN_TEMPLATES.md) | Schema locked; note Jobs deploy |
| [FEATURE_PLAN_PIHOLE_NPM_CERTS.md](FEATURE_PLAN_PIHOLE_NPM_CERTS.md) | Cert model; RC2 is UX not redesign |
| [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) | Release track: 0.5 → **0.6 RC2** → … → **0.8 nmap** → 1.0 |
| [SPEC.md](../SPEC.md) | Phase 6.5 checkboxes |
| `wiki/` | Operator truth |

---

## 8. Success criteria (operator story)

After upgrading to **0.6.0**, an operator can:

1. **Onboard a Pi** with a single guided wizard without guessing panel order.  
2. **Deploy a template** and watch progress like any other fleet job.  
3. **Import a cert**, create a **service map** from a sensible preset, deploy it, and understand how that differs from PiHerder’s own HTTPS.  
4. Read the **wiki** and see screenshots that match the UI.  
5. Notice **snappier** list/long-op behaviour (no hung browser on deploy).

That is the RC2 bar — polished intuition on a trusted RC1 foundation — not a feature race to 1.0.

---

## 9. Relationship to v1.0.0

| v1.0 theme | 0.6.0 contribution |
|------------|-------------------|
| Stable template schema + REST | Schema unchanged; deploy reliability via Jobs |
| One clear install/day-to-day story | Wizard + docs/screenshots |
| Docs freeze bar | Partial progress; full freeze at 1.0 |
| Optional small H2.75 slice | Wizard (required) + maybe Docker bulk |
| Not required for 1.0 | Web SSH, stats console, enrollment phone-home |

**After 0.6.0:** residual host lifecycle (P3–P5), quality platform (Playwright depth), topology residual (configurable columns), **v0.8.0 LAN discovery (nmap)**, then **v1.0** freeze.

---

**End of plan** — update statuses as work lands; freeze into RELEASE notes at tag.
