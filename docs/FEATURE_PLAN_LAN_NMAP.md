# Feature plan — LAN discovery (nmap)

**Status:** **Draft skeleton** (opened 2026-07-19 with v0.8.0 RC3)  
**Ship target:** **v0.8.0** — [PLAN_v0.8.0.md](PLAN_v0.8.0.md) stream **N**  
**Related:** [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · [FEATURE_PLAN_RUNTIME_TOPOLOGY.md](FEATURE_PLAN_RUNTIME_TOPOLOGY.md) (H1 pointer) · [FEATURE_PLAN_HOST_LIFECYCLE.md](FEATURE_PLAN_HOST_LIFECYCLE.md) (promote / onboard) · [ADMIN.md](ADMIN.md) · [SPEC.md](../SPEC.md)

This document owns **product + technical design** for LAN discovery. The cycle plan owns ship bar and sequencing only.

---

## 1. Problem

Operators manage a known fleet in PiHerder but do not see the broader LAN without external tools. Discovery should answer: *what else is on my configured networks, and which of those do I want to manage?*

---

## 2. Product principles (locked leans — 2026-07-19)

| Principle | Decision |
|-----------|----------|
| Opt-in only | No silent full-net scans; operator configures CIDR(s) and triggers (manual + optional schedule) |
| **Auto-create discovery records** | Scan results **persist as first-class discovered devices** — not a transient job log only |
| **Nmap network view** | Dedicated UI to see the discovered network (map/graph or equivalent), not only a flat table |
| **Manual onboarding where appropriate** | Discovery ≠ managed server. Promote / link / wizard / existing server flows stay **operator-driven** |
| Audit + safety | Preview / confirm where destructive or wide-blast; rate limits; no automatic privilege escalation |
| Orthogonal to stack topology | Device discovery is not `RuntimeEdge` / compose deps — may later *display* near fabric |

### Explicit non-goals (0.8 unless reopened)

- Replacing Uptime Kuma or inventing a full NMS  
- Wireless site survey / RF tooling  
- Agent install from discovery alone  
- Silent auto-enroll as full managed **Server** rows with SSH/backups enabled  
- Live nmap of real networks in GitHub Actions (fixtures / mock XML only)

---

## 3. Mental model

```text
  CIDR(s) ──► Discover job ──► Auto-create/update DiscoveryDevice
                                      │
                                      ▼
                              Network view + list
                                      │
                    ┌─────────────────┼─────────────────┐
                    ▼                 ▼                 ▼
                 Ignore          Link existing     Promote / wizard
                 (dismiss)       Server row        → managed Server
                                                    (manual, audited)
```

| Entity (working names — TBD) | Meaning |
|------------------------------|---------|
| **Scan target / LAN profile** | Named CIDR(s), schedule, last run, policy |
| **Discovery device** | Auto-created host/device from scan (IP, hostname, ports snapshot, state) |
| **Link** | Optional FK / relation to an existing PiHerder `Server` (or fabric node later) |
| **Promote** | Operator starts manual onboard (wizard / add-server) prefilled from discovery — does not silently enable SSH jobs |

---

## 4. UX sketch (indicative)

| Surface | Intent |
|---------|--------|
| Settings / Network → Discovery | Enable opt-in, edit CIDR(s), schedule, last job status, security copy |
| **Network view** | Primary spatial or structured view of discovered devices vs known servers |
| Device detail | Ports snapshot, history, link/promote/dismiss actions |
| Jobs | Discover runs as normal audited jobs with progress/log |
| Servers / wizard | Prefill from discovery when promoting; advanced form still available |

Capture policy for screenshots: same as wiki pack (light theme, desktop default) once UI exists.

---

## 5. Runtime / packaging — **open (decide here)**

| Option | Pros | Cons |
|--------|------|------|
| **A. nmap in main/worker image** | Simple compose; one binary path | Larger image; CAP/net requirements on worker |
| **B. Separate nmap container** | Isolate privileges & deps; smaller app image | Extra service; IPC/result handoff design |
| **C. Hybrid** | Worker orchestrates; sidecar executes scan | More moving parts |

**Current lean:** **maybe B (separate container)** — not locked. Flesh:

- [ ] Compose service shape and capabilities (`NET_RAW` / `NET_ADMIN` needs?)  
- [ ] How job worker invokes scan and ingests results (volume, HTTP, CLI exec?)  
- [ ] Offline / air-gap: image still ships or documents external nmap  
- [ ] Document blast radius in ADMIN / wiki  

Do **not** block data model + UI design on this decision, but lock before freeze.

---

## 6. Data & lifecycle (to flesh)

| Topic | Default lean | Notes |
|-------|--------------|-------|
| Port inventory retention | Bounded TTL + **latest snapshot** | Avoid unbounded history |
| Re-scan upsert | Update existing device by key (IP? MAC? hostname?) | Key strategy TBD |
| Stale devices | Mark unseen after N scans; optional purge | Operator policy? |
| Auto-create | **Yes** for discovery entities | Schema + states: `new` / `linked` / `ignored` / … |
| Auto-create **Server** | **No** | Manual promote/onboard only |
| Multi-CIDR | Supported | Overlap handling TBD |
| IPv6 | Decide 0.8 vs later | Prefer explicit out if hard |

---

## 7. Security & ops

- Opt-in language in UI + ADMIN: scanning is active recon on **your** LAN.  
- Audit: who ran discover, which CIDRs, result counts, promote/link/dismiss.  
- No automatic elevation of credentials; discovery never stores SSH secrets.  
- Rate limits / concurrent scan caps so schedules cannot flood the network.  
- CI never needs a real network — parse fixtures only.

---

## 8. Acceptance (aligned with PLAN_v0.8.0)

- [ ] Configure LAN CIDR(s); run discover job (manual; schedule optional)  
- [ ] Results **auto-create** discovery records  
- [ ] **Network view** + list (address / hostname / open ports bounded)  
- [ ] Manual promote/link or dismiss; audit trail  
- [ ] Chosen deploy model documented (image vs separate container)  
- [ ] Wiki + ADMIN security notes  
- [ ] Unit tests: parse, upsert/auto-create, link helpers — no live scan in CI  
- [ ] Screenshots for network view + discovery (stream A)

---

## 9. Implementation phases (draft)

| Phase | Scope | Depends |
|-------|--------|---------|
| **N0** | This plan fleshed — model, UX, deploy decision | — |
| **N1** | Models + migrations + parse/upsert helpers + fixtures | N0 |
| **N2** | Discover job + engine integration (per deploy choice) | N1 + packaging lean |
| **N3** | List + device detail + link/dismiss + audit | N1 |
| **N4** | **Network view** | N3 |
| **N5** | Promote → wizard / server prefill | N3 + lifecycle |
| **N6** | Docs + screenshots + optional schedule | N2–N5 |

---

## 10. Open questions (resolve in this doc)

| # | Question | Lean |
|---|----------|------|
| 1 | Separate nmap container vs in-image? | Maybe separate — **decide before N2** |
| 2 | Stable device identity (IP / MAC / both)? | TBD |
| 3 | Network view: map vs grouped subnets vs fabric overlay? | TBD |
| 4 | Prefill wizard fields from discovery — which? | Hostname, IP, notes; no secrets |
| 5 | Relationship to DNS fabric “device dots”? | Later / soft |
| 6 | Schedule default off? | **Yes** — opt-in schedule |

---

## 11. Changelog

| Date | Note |
|------|------|
| 2026-07-19 | Skeleton opened with v0.8.0 kickoff: auto-create discovery + network view + manual onboard; coverage bar lives in PLAN; deploy model open |

---

**End of skeleton** — expand sections 5–7 and lock open questions before heavy implementation.
