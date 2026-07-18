# PiHerder v0.6.0 — RC2 development plan

**Status:** **Active — pre-release** (implementation nearly complete; freeze checklist open)  
**Date opened:** 2026-07-17 · **Refreshed:** 2026-07-18 (final plan review — cert edge mapping + Grafana fix landed)  
**Baseline:** `v0.5.0` (first RC — tagged 2026-07-17)  
**Package target:** `0.6.0` (`pyproject.toml` · `app/version_info.py`) — **still 0.5.0 on main until freeze**  
**Related:** [PLAN_v0.5.0.md](PLAN_v0.5.0.md) · [RELEASE_v0.5.0.md](RELEASE_v0.5.0.md) · [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · [FEATURE_PLAN_HOST_LIFECYCLE.md](FEATURE_PLAN_HOST_LIFECYCLE.md) · [FEATURE_PLAN_RUNTIME_TOPOLOGY.md](FEATURE_PLAN_RUNTIME_TOPOLOGY.md) · [FEATURE_PLAN_TEMPLATES.md](FEATURE_PLAN_TEMPLATES.md) · [FEATURE_PLAN_PIHOLE_NPM_CERTS.md](FEATURE_PLAN_PIHOLE_NPM_CERTS.md) · [SPEC.md](../SPEC.md)

### Decision (locked)

| Choice | Value |
|--------|--------|
| Git tag target | **`v0.6.0`** (second release candidate / RC2) |
| Theme | **RC1 polish** — more intuitive operator paths, docs depth, light performance |
| Planning frame | Single target; pull **selected** H2.75 / pre-1.0 items that improve first-week UX |
| Production path | ~~v0.5.0 RC1~~ **live** → **v0.6.0 RC2** (pre-freeze) → **v1.0** refined production |
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

## 3. Workstreams — status board

### Summary (2026-07-18)

| Stream | Must for 0.6? | Status |
|--------|---------------|--------|
| **C** Templates → Jobs | Must | **Done** |
| **D** Cert setup / maps / edge | Must | **Done** (+ edge mapping + Grafana UID fix) |
| **F** Docker project bulk lifecycle | Nice | **Done** |
| **H** Topology + Kuma coverage | Stretch | **Done** (nmap → **v0.8.0**) |
| **B** Add-host wizard | **Must** | **Open** — largest remaining feature |
| **A** Docs / screenshots | Must (at freeze) | **In progress** — prose updated; screenshots / RELEASE still open |
| **E** Light perf | Should | Open / partial via Jobs |
| **G** Residual polish | Continuous | Open |

---

### A — Documentation polish (parallel, continuous)

| Item | Notes | Priority | Status |
|------|--------|----------|--------|
| Dual TLS story clarity | Caddy edge vs Catalog vault + self-managed edge map | Must | **Done** (wiki https-tls + certificates) |
| Cert cookbooks + presets | OctoPi, Grafana UID 472, stage_sudo, edge mapping | Must | **Done** |
| Docker bulk lifecycle docs | Stop/Start/Restart all as Jobs | Must | **Done** |
| Topology / coverage wiki | Runtime stack + Kuma coverage | Should | **Done** |
| Wizard docs | Update when B lands | Must | Blocked on B |
| Wiki screenshot refresh | Critical light-desktop paths | Must | **Open** (freeze) |
| `RELEASE_v0.6.0.md` | Written at freeze only | Must | **Open** (freeze) |
| ADMIN / SPEC / ROADMAP sync | This cycle | Must | **This refresh** |
| Toward v1.0 docs bar (partial) | contributing-docs freeze items | Nice | Open |

**Deliverable at tag:** this plan frozen + `RELEASE_v0.6.0.md` + wiki pages updated in the same cycle as features.

---

### B — Server onboarding (from H2.75 P2) — **OPEN (must)**

**Problem:** Today is form → server detail → SSH access panel → features/schedules/DNS. Correct order is tribal knowledge; password bootstrap can linger.

**Solution:** Guided multi-step wizard that **reuses** existing SSH / feature / DNS endpoints (orchestration only). Design locked in [FEATURE_PLAN_HOST_LIFECYCLE.md](FEATURE_PLAN_HOST_LIFECYCLE.md) § Phase 2.

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

**Acceptance:** New operator can complete first host without reading the wiki first; wiki happy path documents the wizard; pytest/RBAC for new routes.

**Release call (pre-freeze):** either **ship B** before tag, or **defer B to 0.6.x** and restate ship bar (document advanced form as primary). Prefer ship B if capacity allows — it is the last product must from original RC2 theme.

---

### C — Templates polish — **musts done**

| Item | Priority | Status |
|------|----------|--------|
| Template deploy as Jobs + live log | Must | **Done** |
| Redeploy as Jobs | Should | **Done** |
| Drift check as Jobs | Should | Open |
| Post-deploy CTAs | Should | Partial (redirect to deployment) |
| Wizard copy / empty states | Should | Open |
| Git template catalog pull | Stretch | Open |
| Expanded OOTB pack | Out | Out |

---

### D — TLS cert setup & distribution mapping — **musts done**

| Item | Priority | Status |
|------|----------|--------|
| First-cert setup guide (`/certificates/setup`) | Must | **Done** |
| Map presets (NPM, Caddy, Docker bind, OctoPi, Grafana, UniFi) + path preview | Must | **Done** |
| Grafana volume: UID **472** (not root:600) | Must | **Done** (preset + cookbook + live fix) |
| OctoPi / HAProxy cookbook + preset | Must | **Done** |
| Fleet write modes: direct · **stage_sudo** + sudoers snippet | Should | **Done** |
| List/detail map status (hosts, in-sync/stale, empty CTAs) | Should | **Done** |
| **Self-managed edge map** (Apply to this PiHerder / mapping on-off / renew re-apply) | Should | **Done** |
| Multi-map deploy as Job | Should | **Open** (still request/wait path) |
| DNS fabric ↔ cert deep-link | Nice | Open |
| Upload-source expiry reminders | Nice | Open |
| ACME in herder | Out | Out |

**Key paths:** `app/services/certificates.py` · `certificates_*.html` · wiki [certificates](../wiki/integrations/certificates.md) · [https-tls](../wiki/getting-started/https-tls.md)

---

### E — Slight performance tweaks

| Item | Priority | Status |
|------|----------|--------|
| Template / long stack ops → Jobs | Must (via C/F) | **Done** for templates + stack lifecycle |
| Cert multi-host deploy as Job | Should | Open |
| List queries pure-DB | Should | Open |
| Docker inventory spam | Should | Open |
| Fabric pulse vs full mesh | Should | Open |

---

### F — Docker project bulk (H2.75 P1) — **Done**

Project ⋯ → Stop / Start / Restart **all** → confirm → Jobs (`docker_stack_stop` / `_start` / `_restart`) + JobHold; exclusive stack mutation lane. Wiki [Docker overview](../wiki/docker/overview.md).

---

### G — Fleet / RC1 residual polish

Bugfixes, mobile/ops-hero, audit/job copy, freeze version bump + Hub tags.

---

### H — Discovery & visualisation — **closed for 0.6**

| # | Status |
|---|--------|
| **H3** Kuma coverage | **Done** — `/dns/coverage` |
| **H2** Runtime topology | **Done** — panel, edges, expand, order |
| **H1** LAN nmap | **→ v0.8.0** |

Residual topology (configurable columns, link-to-column): **later** — [FEATURE_PLAN_RUNTIME_TOPOLOGY.md](FEATURE_PLAN_RUNTIME_TOPOLOGY.md) § 12b.

---

## 4. Explicitly out of v0.6.0 (must-ship)

| Area | Reason |
|------|--------|
| Web SSH console | H2.75 P5 |
| Agent-based management | Non-goal |
| ACME inside PiHerder | Use NPM / external |
| NPM proxy host write CRUD | Later |
| Cloudflare DNS automation | H2.5+ |
| Service migrate / destructive wipe | H2.5 |
| Configurable map columns | Later residual |
| **LAN discovery / nmap** | **v0.8.0** |
| Large curated template pack | H3 |
| Kubernetes / bare install as supported | Under consideration only |
| Full v1.0 process freeze | Goal of 1.0 |

---

## 5. Ship bar (`v0.6.0` tag)

### Must-have (freeze)

| # | Item | Status |
|---|------|--------|
| 1 | **Add-host wizard** (B) **or** explicit deferral in RELEASE + ship bar | **Open** |
| 2 | Template deploy/redeploy as Jobs + live log (C) | **Done** |
| 3 | Cert setup / mapping UX (D) | **Done** |
| 4 | Docs: dual TLS, cert cookbooks, critical screenshots, `RELEASE_v0.6.0.md` | **Partial** (prose done; screenshots + RELEASE open) |
| 5 | pytest green; smoke paths | Continuous |
| 6 | Version `0.6.0` + git tag + multi-arch Hub (`0.6.0` / `0.6` / `latest`) | **Open** (still 0.5.0 in tree) |
| 7 | Secret-path review unchanged | OK |

### Shipped early (stretch — on main)

- H3 coverage · H2 runtime topology · F Docker bulk · D edge self-map · stage_sudo · Grafana 472 fix  

### Nice-to-have (won’t block freeze if deferred)

- Multi-map cert deploy as Job  
- Drift check as Job  
- Bootstrap scripts with wizard  
- Playwright Phase A  
- Git catalog pull  

### QA smoke (operator)

- [ ] New host via **wizard** E2E **or** documented advanced path if B deferred  
- [ ] Template deploy → JobHold → deployment page  
- [ ] Cert: NPM pull **and** PEM upload → map (preset) → deploy  
- [ ] Cert: **Apply to this PiHerder** + remove mapping; renew re-apply when mapping on  
- [ ] Grafana (or similar): PEMs readable by container user (UID 472)  
- [ ] Docker ⋯ Stop/Start/Restart all → Job  
- [ ] Path map stack expand + panel reorder  
- [ ] Wiki dual TLS + certs + Docker lifecycle accurate  
- [ ] `pytest` green in container  
- [ ] `mkdocs build --strict` green  
- [ ] Hub multi-arch publish + compose pull of `0.6.0`  
- [ ] About shows **0.6.0**

---

## 6. Implementation order (actual)

```text
1. PLAN + production path                    // done
2. Template deploy → Jobs (C)                // done
3. H3 coverage + H2 topology                 // done
4. Docker bulk lifecycle (F)                 // done
5. Cert UX + edge + stage_sudo + Grafana (D) // done
6. Add-host wizard (B)                       // OPEN — gate for freeze
7. Docs screenshots + RELEASE (A)            // freeze
8. Version + Hub tag                         // freeze
```

---

## 7. Pre-release readiness review (2026-07-18)

### Green (ready)

| Area | Notes |
|------|--------|
| Templates as Jobs | Live log pattern reusable |
| Cert vault UX | Setup guide, presets, sync status, dual TLS story |
| Self-managed edge | Explicit mapping on/off; renew re-apply |
| Fleet least-priv maps | stage_sudo + sudoers snippet |
| Grafana deploy | Correct ownership model documented |
| Docker stack lifecycle | Jobs exclusive with deploy/template |
| Topology / coverage | Operator-locked dual altitude |
| Tests | certs, stack lifecycle, topology suites on main |

### Yellow (freeze work, not product risk)

| Area | Action |
|------|--------|
| Version still `0.5.0` | Bump at freeze only |
| `RELEASE_v0.6.0.md` | Write at freeze |
| Screenshots | Refresh critical paths |
| Wiki add-server | Still says wizard “planned” until B ships |
| Branch unpushed | Push before tag if remote is source of truth |

### Red / gate

| Area | Call |
|------|------|
| **Add-host wizard (B)** | **Last original must.** Ship before tag **or** consciously defer to **0.6.1** and change ship bar + RELEASE. |

### Recommended freeze sequence

1. **Decide B:** implement wizard **or** defer with RELEASE note (“onboarding remains advanced form; wizard in 0.6.x”).  
2. Screenshot pass + `mkdocs build --strict`.  
3. Full pytest in image.  
4. Operator smoke checklist (above).  
5. Bump version → `RELEASE_v0.6.0.md` → tag → Hub multi-arch → compose pull verify.  

### Scope not required for RC2 tag

nmap (0.8), configurable topology columns, multi-map cert Jobs, web SSH, host stats console, ACME-in-herder.

---

## 8. Docs map (this cycle)

| Doc | Role |
|-----|------|
| **This file** | Living ship plan + readiness |
| [RELEASE_v0.6.0.md](RELEASE_v0.6.0.md) | Written at freeze only |
| [FEATURE_PLAN_HOST_LIFECYCLE.md](FEATURE_PLAN_HOST_LIFECYCLE.md) | Wizard / bulk |
| [FEATURE_PLAN_RUNTIME_TOPOLOGY.md](FEATURE_PLAN_RUNTIME_TOPOLOGY.md) | H2 closed |
| [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) | 0.5 → 0.6 → 0.8 nmap → 1.0 |
| [SPEC.md](../SPEC.md) | Phase checkboxes |
| `wiki/` | Operator truth |

---

## 9. Success criteria (operator story)

After upgrading to **0.6.0**, an operator can:

1. **Onboard a Pi** with a guided wizard *(if B ships)* or a documented advanced path.  
2. **Deploy a template** and watch progress like any other fleet job.  
3. **Import a cert**, use a **preset** (incl. Grafana-safe ownership), deploy fleet maps and/or **self-managed edge**, and understand dual TLS.  
4. **Stop/start/restart** a whole compose project from Docker ⋯ as a Job.  
5. **Expand a runtime stack** on Network maps and reorder containers.  
6. Read the **wiki** and see prose that matches the UI (screenshots at freeze).

---

## 10. Relationship to v1.0.0

| v1.0 theme | 0.6.0 contribution |
|------------|-------------------|
| Stable template schema + REST | Schema unchanged; Jobs reliability |
| Clear install / day-to-day story | Cert UX + Docker bulk + (wizard if shipped) |
| Docs freeze bar | Partial; full freeze at 1.0 |
| Not required for 1.0 | Web SSH, stats console, enrollment, nmap |

**After 0.6.0:** residual H2.75 (P3–P5), quality platform, topology residual, **v0.8.0 nmap**, then **v1.0** freeze.

---

**End of plan** — freeze into RELEASE notes at tag.
