# PiHerder v0.6.0 — RC2 development plan

**Status:** **Released** as `v0.6.0` (2026-07-18)  
**Date opened:** 2026-07-17 · **Refreshed:** 2026-07-18 (tag + Hub + RELEASE)  
**Baseline:** `v0.5.0` (first RC — tagged 2026-07-17)  
**Package version at tag:** `0.6.0` (`pyproject.toml` · `app/version_info.py`)  
**Related:** [PLAN_v0.5.0.md](PLAN_v0.5.0.md) · [RELEASE_v0.5.0.md](RELEASE_v0.5.0.md) · [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · [FEATURE_PLAN_HOST_LIFECYCLE.md](FEATURE_PLAN_HOST_LIFECYCLE.md) · [FEATURE_PLAN_RUNTIME_TOPOLOGY.md](FEATURE_PLAN_RUNTIME_TOPOLOGY.md) · [FEATURE_PLAN_TEMPLATES.md](FEATURE_PLAN_TEMPLATES.md) · [FEATURE_PLAN_PIHOLE_NPM_CERTS.md](FEATURE_PLAN_PIHOLE_NPM_CERTS.md) · [SPEC.md](../SPEC.md)

### Decision (locked)

| Choice | Value |
|--------|--------|
| Git tag target | **`v0.6.0`** (second release candidate / RC2) |
| Theme | **RC1 polish** — more intuitive operator paths, docs depth, light performance |
| Planning frame | Single target; pull **selected** H2.75 / pre-1.0 items that improve first-week UX |
| Production path | ~~v0.5.0 RC1~~ **live** → **v0.6.0 RC2** (code freeze) → **v0.7.0** (wizard + docs screenshots) → **v1.0** |
| Docs strategy | Living wiki + `RELEASE_v0.6.0.md` at tag (no wiki fork) |

### Decision (2026-07-18 freeze)

| Choice | Value |
|--------|--------|
| **Add-host wizard (B)** | **Deferred to v0.7.0** — onboarding remains form + SSH access panel |
| **Screenshot refresh** | **Deferred to v0.7.0** — prose is current; capture pass tracked in wiki screenshots README |
| **Product code** | **Frozen** — no new 0.6 features; bugfixes only if pytest/smoke blocks tag |
| Remaining freeze work | ~~pytest · RELEASE · version · tag · Hub~~ **done at tag** |

---

## 1. Theme

**RC2 — polish what RC1 shipped.** No second large product wave. Make the existing control plane feel obvious for a new operator:

1. ~~**Server onboarding** that guides order of operations~~ → **v0.7.0** (wizard)  
2. **Templates** that feel consistent with other long-running jobs  
3. **TLS cert vault → service maps** that are easier to set up and reason about  
4. **Documentation** prose that matches the UI (screenshot PNGs → **v0.7.0**)  
5. **Slight performance** wins where pages already feel sluggish  

Pull from the **v1.0 / H2.75** backlog only where it directly serves that theme (Docker bulk shipped; wizard deferred). Defer high-risk surface area (web SSH, first-boot phone-home, ACME-in-herder).

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

### Summary (2026-07-18 code freeze)

| Stream | Must for 0.6? | Status |
|--------|---------------|--------|
| **C** Templates → Jobs | Must | **Done** |
| **D** Cert setup / maps / edge | Must | **Done** (+ edge mapping + Grafana UID fix) |
| **F** Docker project bulk lifecycle | Nice | **Done** |
| **H** Topology + Kuma coverage | Stretch | **Done** (nmap → **v0.8.0**) |
| **B** Add-host wizard | ~~Must~~ **→ v0.7.0** | **Deferred** (locked 2026-07-18) |
| **A** Docs / screenshots | Prose must; screenshots → **v0.7.0** | **Prose done**; PNG refresh deferred |
| **E** Light perf | Should | Partial via Jobs (enough for freeze) |
| **G** Residual polish | Continuous | Freeze packaging only |

---

### A — Documentation polish

| Item | Notes | Priority | Status |
|------|--------|----------|--------|
| Dual TLS story clarity | Caddy edge vs Catalog vault + self-managed edge map | Must | **Done** |
| Cert cookbooks + presets | OctoPi, Grafana UID 472, stage_sudo, edge mapping | Must | **Done** |
| Docker bulk lifecycle docs | Stop/Start/Restart all as Jobs | Must | **Done** |
| Topology / coverage wiki | Runtime stack + Kuma coverage | Should | **Done** |
| Wizard docs | Primary path when B lands | **v0.7.0** | Deferred |
| Wiki screenshot refresh | Critical light-desktop paths + new 0.6 surfaces | **v0.7.0** | Deferred — inventory in [screenshots README](../wiki/assets/screenshots/README.md) |
| `RELEASE_v0.6.0.md` | Written at tag | Must | **Done** |
| ADMIN / SPEC / ROADMAP sync | This freeze | Must | **This refresh** |

**At tag:** this plan frozen + `RELEASE_v0.6.0.md` + prose wiki aligned. Screenshots are **not** a 0.6.0 tag gate.

---

### B — Server onboarding — **deferred to v0.7.0**

**Problem:** Today is form → server detail → SSH access panel → features/schedules/DNS. Correct order is tribal knowledge; password bootstrap can linger.

**Solution (v0.7.0):** Guided multi-step wizard that **reuses** existing SSH / feature / DNS endpoints (orchestration only). Design locked in [FEATURE_PLAN_HOST_LIFECYCLE.md](FEATURE_PLAN_HOST_LIFECYCLE.md) § Phase 2.

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

**0.6.0 ship path:** advanced **Add server** form + **SSH access** panel remains primary and fully supported. Wiki documents that path.

**Decision (locked):** do **not** implement B in this freeze. Track under **v0.7.0** with screenshot pack for the new UI.

---

### C — Templates polish — **musts done**

| Item | Priority | Status |
|------|----------|--------|
| Template deploy as Jobs + live log | Must | **Done** |
| Redeploy as Jobs | Should | **Done** |
| Drift check as Jobs | Should | Open (post-0.6) |
| Post-deploy CTAs | Should | Partial (redirect to deployment) |
| Wizard copy / empty states | Should | Open (post-0.6) |
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
| Multi-map deploy as Job | Should | **Open** (post-0.6) |
| DNS fabric ↔ cert deep-link | Nice | Open |
| Upload-source expiry reminders | Nice | Open |
| ACME in herder | Out | Out |

**Key paths:** `app/services/certificates.py` · `certificates_*.html` · wiki [certificates](../wiki/integrations/certificates.md) · [https-tls](../wiki/getting-started/https-tls.md)

---

### E — Slight performance tweaks

| Item | Priority | Status |
|------|----------|--------|
| Template / long stack ops → Jobs | Must (via C/F) | **Done** for templates + stack lifecycle |
| Cert multi-host deploy as Job | Should | Open (post-0.6) |
| List queries pure-DB | Should | Open |
| Docker inventory spam | Should | Open |
| Fabric pulse vs full mesh | Should | Open |

---

### F — Docker project bulk (H2.75 P1) — **Done**

Project ⋯ → Stop / Start / Restart **all** → confirm → Jobs (`docker_stack_stop` / `_start` / `_restart`) + JobHold; exclusive stack mutation lane. Wiki [Docker overview](../wiki/docker/overview.md).

---

### G — Fleet / RC1 residual polish

Bugfixes only under freeze; version bump + Hub tags at release packaging.

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
| **Add-host wizard** | **→ v0.7.0** (locked freeze decision) |
| **Wiki screenshot refresh** | **→ v0.7.0** (prose is enough for 0.6 tag) |
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
| 1 | Product streams C/D/F/H (as scoped) | **Done** |
| 2 | Explicit deferrals documented (wizard + screenshots → 0.7) | **Done** |
| 3 | Template deploy/redeploy as Jobs + live log (C) | **Done** |
| 4 | Cert setup / mapping UX (D) | **Done** |
| 5 | Docs prose: dual TLS, cert cookbooks, Docker bulk, topology | **Done** |
| 6 | pytest green | **Done** — 426 passed (2026-07-18 freeze pack) |
| 7 | Version `0.6.0` + git tag + multi-arch Hub (`0.6.0` / `0.6` / `latest`) | **Done** |
| 8 | `RELEASE_v0.6.0.md` | **Done** |
| 9 | Secret-path review unchanged | OK |

### Deferred to v0.7.0 (not tag gates)

- Add-host wizard (B) + wiki primary path rewrite  
- Screenshot PNG refresh (existing + new 0.6 surfaces) — see [screenshots README](../wiki/assets/screenshots/README.md)  
- Multi-map cert deploy as Job · drift as Job · Playwright Phase A · git catalog pull  

### QA smoke (operator)

- [x] Onboarding path = **advanced form + SSH access** (documented; wizard deferred)  
- [ ] Template deploy → JobHold → deployment page  
- [ ] Cert: NPM pull **and** PEM upload → map (preset) → deploy  
- [ ] Cert: **Apply to this PiHerder** + remove mapping; renew re-apply when mapping on  
- [ ] Grafana (or similar): PEMs readable by container user (UID 472)  
- [ ] Docker ⋯ Stop/Start/Restart all → Job  
- [ ] Path map stack expand + panel reorder  
- [ ] Wiki dual TLS + certs + Docker lifecycle accurate (prose)  
- [ ] `pytest` green in container  
- [ ] `mkdocs build --strict` green (if wiki touched at tag)  
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
6. Add-host wizard (B)                       // DEFERRED → v0.7.0
7. Code freeze + pytest                      // this step
8. RELEASE + version + Hub tag               // packaging
9. Screenshots + wizard                      // v0.7.0
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
| Scope decisions | Wizard + screenshots **→ v0.7.0** (locked) |
| Tests | Full pack **426 passed** (backup format v3 assertion fixed at freeze) |

### Yellow (packaging only — not product risk)

| Area | Action |
|------|--------|
| Version | **0.6.0** at tag |
| `RELEASE_v0.6.0.md` | **Written** |
| Hub multi-arch | `0.6.0` / `0.6` / `latest` |
| Operator smoke checklist | Recommended after pull |

### Red / gate

| Area | Call |
|------|------|
| ~~Add-host wizard~~ | **Resolved** — deferred **v0.7.0** |
| pytest | **Cleared** — full pack green; re-run if packaging-only code changes |

### Freeze sequence (remaining)

1. ~~Decide B~~ → **deferred v0.7.0**  
2. Full pytest in image (this freeze).  
3. Operator smoke checklist (above).  
4. Bump version → `RELEASE_v0.6.0.md` → tag → Hub multi-arch → compose pull verify.  

### Scope not required for RC2 tag

Wizard (0.7), screenshot PNG pack (0.7), nmap (0.8), configurable topology columns, multi-map cert Jobs, web SSH, host stats console, ACME-in-herder.

---

## 8. Docs map (this cycle)

| Doc | Role |
|-----|------|
| **This file** | Living ship plan + freeze record |
| [RELEASE_v0.6.0.md](RELEASE_v0.6.0.md) | Written at tag only |
| [FEATURE_PLAN_HOST_LIFECYCLE.md](FEATURE_PLAN_HOST_LIFECYCLE.md) | P1 done; P2 → **0.7.0** |
| [FEATURE_PLAN_RUNTIME_TOPOLOGY.md](FEATURE_PLAN_RUNTIME_TOPOLOGY.md) | H2 closed |
| [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) | 0.5 → 0.6 freeze → **0.7 wizard/screenshots** → 0.8 nmap → 1.0 |
| [SPEC.md](../SPEC.md) | Phase checkboxes |
| `wiki/` | Operator truth (prose) |
| [wiki screenshots README](../wiki/assets/screenshots/README.md) | **v0.7.0** capture inventory |

---

## 9. Success criteria (operator story)

After upgrading to **0.6.0**, an operator can:

1. **Onboard a Pi** with the **documented advanced form + SSH access** path (wizard in **0.7.0**).  
2. **Deploy a template** and watch progress like any other fleet job.  
3. **Import a cert**, use a **preset** (incl. Grafana-safe ownership), deploy fleet maps and/or **self-managed edge**, and understand dual TLS.  
4. **Stop/start/restart** a whole compose project from Docker ⋯ as a Job.  
5. **Expand a runtime stack** on Network maps and reorder containers.  
6. Read the **wiki** prose that matches the UI (PNG refresh in **0.7.0**).

---

## 10. Relationship to v1.0.0 / v0.7.0

| Release | Contribution |
|---------|----------------|
| **0.6.0** | Template Jobs · cert UX · Docker bulk · topology+coverage · dual TLS prose |
| **0.7.0** | Add-host wizard · screenshot pack (stale + new 0.6 surfaces) · residual H2.75 polish as capacity allows |
| **0.8.0** | LAN nmap |
| **v1.0** | Stable schema + REST + full docs freeze bar |

**After 0.6.0:** **v0.7.0** wizard + screenshots → residual H2.75 (P3–P5), quality platform, topology residual → **v0.8.0 nmap** → **v1.0**.

---

**End of plan** — product code frozen 2026-07-18; freeze into RELEASE notes at tag.
