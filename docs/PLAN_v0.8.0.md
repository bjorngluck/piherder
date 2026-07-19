# PiHerder v0.8.0 — RC3 development plan

**Status:** **Planned** (opens after `v0.7.0` tag)  
**Date opened:** 2026-07-19 (scope parked from 0.7 product review)  
**Baseline:** `v0.7.0` (when tagged) · interim tree may still show `0.6.0` / `0.7.0` until bump  
**Package version during cycle:** prefer `0.8.0.dev0` after 0.7 freeze · **tag:** `0.8.0`  
**Related:** [PLAN_v0.7.0.md](PLAN_v0.7.0.md) · [FEATURE_PLAN_RUNTIME_TOPOLOGY.md](FEATURE_PLAN_RUNTIME_TOPOLOGY.md) · [FEATURE_PLAN_HOST_LIFECYCLE.md](FEATURE_PLAN_HOST_LIFECYCLE.md) · [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · [SPEC.md](../SPEC.md)

### Decision (locked at park)

| Choice | Value |
|--------|--------|
| Git tag target | **`v0.8.0`** |
| Theme | **RC3** — third release-candidate polish + **LAN discovery (nmap-class)** |
| Planning frame | Must ship **opt-in LAN scan** product slice; absorb **residual polish cut from 0.7**; deeper host lifecycle only as capacity |
| Production path | ~~v0.5.0 RC1~~ → ~~**v0.6.0 RC2**~~ → **v0.7.0** (wizard + E2E) → **v0.8.0 RC3** (this cycle) → **v1.0** |
| Docs strategy | Wiki + ADMIN/SPEC with nmap UX; `RELEASE_v0.8.0.md` at tag |
| **Out of 0.8 by default** | Web SSH (P5), full ACME-in-herder, K8s, large template pack |

### Why RC3

| RC | Tag | Role |
|----|-----|------|
| **RC1** | `v0.5.0` | First candidate — ops depth, fabric, multi-arch, freeze bar |
| **RC2** | `v0.6.0` | Operator polish — template Jobs, cert UX, Docker bulk, topology |
| **(onboarding)** | `v0.7.0` | Wizard + Playwright E2E + compose sets / annotations (not numbered RC) |
| **RC3** | **`v0.8.0`** | LAN discovery + leftover polish; last major candidate wave before **v1.0** |

---

## 1. Theme

**Discover devices on the LAN and clear the 0.7 backlog.** v0.7 finished guided host add, browser E2E, and Docker topology presentation (view groups + compose sets). Operators still have no first-class **“what’s on my network?”** discovery, and several **nice** Jobs/UX items were intentionally cut so 0.7 could freeze.

v0.8.0 RC3:

1. **LAN discovery (nmap-class)** — opt-in CIDR scan, link results to servers / fabric  
2. **Residual polish** deferred from 0.7 stream C  
3. Optional **host lifecycle P3** or quality depth **only if capacity remains** after 1–2  

This is **not** web SSH, ACME-in-herder, or a second E2E rewrite.

---

## 2. What 0.7.0 already delivered (do not re-build)

| Area | v0.7.0 state |
|------|----------------|
| Onboarding | Add-host **wizard** primary; advanced form secondary |
| Quality | Playwright **Phase A + wizard B**; e2e compose set; CI job |
| Docker / topology | Annotations (T0–T4), **compose sets**, stack panel / map expand |
| Docs | Wizard wiki path, fabric/Docker compose-set prose; screenshot pack at 0.7 tag |

0.8 **adds discovery + polish**; it does not replace the wizard or E2E harness.

---

## 3. Workstreams

### Summary

| Stream | Must for 0.8? | Status |
|--------|---------------|--------|
| **N** LAN discovery (nmap-class) | **Must** | Planned (design in topology / ecosystem roadmap H1) |
| **C** Residual polish from 0.7 | Should / capacity | Parked inventory below |
| **L** Host lifecycle P3 (stats / allowlisted commands) | Nice | Capacity only |
| **Q** E2E / quality depth (B6, HTTP smoke, Phase C slice) | Nice | Capacity only |
| **D** Packaging | Must at tag | End of cycle |

---

### N — LAN discovery (nmap-class) — **must**

**Problem:** Operators know services and stacks on **managed** hosts, but not the broader LAN (printers, IoT, unmanaged boxes) without external tools.

**Solution (product target):** Opt-in scan of a configured **Network LAN CIDR**, store/discover devices, optional link to PiHerder servers / fabric nodes. Orthogonal to stack dependency edges (devices ≠ compose graphs).

| Aspect | Lean |
|--------|------|
| Trigger | Manual + optional schedule; **opt-in** only |
| Engine | nmap-class (or equivalent) in job/worker — never silent full-net scans |
| Safety | Preview / confirm / audit; rate limits; no automatic privilege escalation |
| UI | Discovery list + “link to server” / ignore; fabric may show device dots later |
| Out | Replacing Kuma; agent install; wireless site survey |

**Design detail:** Expand from [FEATURE_PLAN_RUNTIME_TOPOLOGY.md](FEATURE_PLAN_RUNTIME_TOPOLOGY.md) H1 notes + [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) when cycle opens. Full acceptance criteria locked at kickoff.

**Acceptance (indicative — refine at open):**

- [ ] Operator can configure LAN CIDR(s) and run a **discover** job  
- [ ] Results list with address / hostname / open ports (bounded)  
- [ ] Link or dismiss a device; audit trail  
- [ ] Wiki + ADMIN security notes (opt-in, blast radius)  
- [ ] Unit tests for parse / link helpers; no live scan required in CI  

---

### C — Residual polish (from 0.7 capacity cut)

**None of these block the nmap theme**, but they are the explicit **0.7 → 0.8 carry list**.

| Item | Why | Priority | Status |
|------|-----|----------|--------|
| Cert **multi-map / multi-host deploy as Job** | Long ops pattern | Nice | Parked |
| Template wizard copy / empty states | First-use clarity | Nice | Parked |
| Docker management chips cohesive with stack panel (T6) | Stretch UX | Stretch | Parked |
| List query / Docker inventory spam / fabric pulse | Light perf | Stretch | Parked |
| Git template catalog pull | Ops depth | Out unless cheap | Parked |
| Topology **link-to-column** / per-project column profiles | Residual after T0–T4 | Stretch | Parked |

If freeze pressure returns, **ship N only** — cut C again.

---

### L — Host lifecycle (capacity)

| Phase | Name | Default for 0.8 |
|-------|------|-----------------|
| **P3** | Host stats + healthcheck + allowlisted commands | Capacity / should |
| **P4** | Bootstrap depth + enrollment phone-home | Out unless tiny |
| **P5** | Web SSH console | **Out of 0.8** |

See [FEATURE_PLAN_HOST_LIFECYCLE.md](FEATURE_PLAN_HOST_LIFECYCLE.md).

---

### Q — Quality depth (capacity)

| Item | Notes |
|------|--------|
| E2E **B6** viewer cannot add server | If not free in 0.7 freeze |
| HTTP TestClient smoke | Cheap unit-job layer |
| Playwright Phase C slice | Optional visual/a11y — not full matrix |
| E2E deeper journeys | Template/certs shell only if cheap |

---

### D — Packaging (end of cycle)

| Item | Notes |
|------|--------|
| Unit + E2E green | Existing harness |
| Version → `0.8.0` | `pyproject.toml` · `app/version_info.py` |
| `RELEASE_v0.8.0.md` | Highlights, upgrade, intentional outs |
| Git tag `v0.8.0` + Hub multi-arch | `0.8.0` / `0.8` / `latest` |
| `mkdocs build --strict` | If wiki touched |

---

## 4. Explicitly out of v0.8.0

| Area | Reason |
|------|--------|
| **H2.75 P5** Web SSH | Separate ship bar |
| ACME inside PiHerder | External / NPM |
| NPM proxy host write CRUD | Later |
| Cloudflare DNS automation | H2.5+ |
| Large curated template pack | H3 |
| Kubernetes / bare metal as supported install | Under consideration |
| Live nmap of real lab networks in GitHub Actions | Mock parsers / fixtures only |
| Full v1.0 process freeze | Goal of 1.0 |

---

## 5. Ship bar (`v0.8.0` tag)

### Must-have

| # | Item | Status |
|---|------|--------|
| 1 | LAN discovery product slice (N acceptance) | Open |
| 2 | Wiki + security notes for opt-in scan | Open |
| 3 | Unit pytest green | Open |
| 4 | E2E harness still green (no regression) | Open |
| 5 | Version `0.8.0` + tag + Hub | Open |
| 6 | `RELEASE_v0.8.0.md` | Open |

### Should

- One or more C residual items  
- P3 host stats slice  
- E2E B6 / HTTP smoke  

### Not tag gates

- P4–P5 · full Phase C · large catalog  

---

## 6. Implementation order (indicative)

```text
1. PLAN_v0.8.0 open + nmap design lock          // docs
2. Discovery model + job + parser fixtures       // N
3. UI list + link/dismiss + audit                // N
4. Wiki / ADMIN security copy                    // N
5. Capacity: C residual or P3                    // optional
6. Freeze: tests · RELEASE · version · Hub
```

---

## 7. Open questions (resolve at kickoff)

| # | Question | Default lean |
|---|----------|--------------|
| 1 | nmap binary in image vs sidecar vs remote agent? | **Job on herder host** first; document image deps |
| 2 | Persist full port inventory forever? | **Bounded TTL** + latest snapshot |
| 3 | Auto-create server rows from discovery? | **No** — link/dismiss only |
| 4 | Ship multi-map cert Jobs in same cycle? | Only if N is green early |
| 5 | Pull any P3 into must? | **No** unless operator pain is acute |

---

## 8. Docs map

| Doc | Role |
|-----|------|
| **This file** | Living ship plan for RC3 |
| [PLAN_v0.7.0.md](PLAN_v0.7.0.md) | Prior cycle (wizard + E2E + compose sets) |
| [FEATURE_PLAN_RUNTIME_TOPOLOGY.md](FEATURE_PLAN_RUNTIME_TOPOLOGY.md) | H1 nmap notes · topology residual |
| [FEATURE_PLAN_HOST_LIFECYCLE.md](FEATURE_PLAN_HOST_LIFECYCLE.md) | P3–P5 design |
| [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) | Multi-horizon |
| `RELEASE_v0.8.0.md` | Written at tag only |

---

## 9. Success criteria

After upgrading to **0.8.0**, an operator can:

1. **Opt in** to a LAN CIDR discovery job and see a usable device list.  
2. **Link** a discovered address to a managed server (or ignore it) with audit.  
3. Still rely on 0.7 wizard / E2E / compose sets / annotations unchanged.  
4. Optionally enjoy residual polish items that made the cut.

Before tagging **0.8.0**, a maintainer can:

1. Run unit + e2e green without live lab nmap in CI.  
2. Read RELEASE notes that list intentional outs (web SSH, ACME-in-herder, …).

---

## 10. Changelog (plan)

| Date | Note |
|------|------|
| 2026-07-19 | Plan created at 0.7 product review: **RC3** = nmap must + 0.7 stream-C residual parked; P5 out |

---

**End of plan** — parked until `v0.7.0` tags; then becomes active living document.
