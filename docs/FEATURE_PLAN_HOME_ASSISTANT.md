# Feature plan — Home Assistant integration (architecture + discovery)

**Status:** **v0.9.0 path 1 shipped (S2 HAOS over SSH)** — frozen for this release; REST / container HA / path 2 later  
**Ship framing:** [PLAN_v0.9.0.md](PLAN_v0.9.0.md) stream **HA**  
**Related:** [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) § Horizon 3 · [FEATURE_PLAN_INTEGRATIONS.md](FEATURE_PLAN_INTEGRATIONS.md) · [FEATURE_PLAN_HOST_LIFECYCLE.md](FEATURE_PLAN_HOST_LIFECYCLE.md) · [FEATURE_PLAN_LAN_NMAP.md](FEATURE_PLAN_LAN_NMAP.md) · [API.md](API.md) · [SPEC.md](../SPEC.md)

This document owns **architecture, product directions, and open decisions** for Home Assistant (HA). The cycle plan owns ship bar and sequencing only.

---

## 1. Problem / opportunity

Homelab operators often run **HAOS** as a first-class **Pi / appliance host** next to Docker Pis. From PiHerder’s point of view that node is a **managed Server over SSH** — not a generic Debian box with Docker Compose.

Today PiHerder:

| Capability | Status |
|------------|--------|
| SSH to HAOS (key deploy, least-priv **skipped** with guidance) | Yes |
| Backups via plain **rsync** (root / HAOS path) | Yes (if rsync present) |
| Bind Kuma **host service** monitors | Yes |
| HA as **client** of PiHerder (token REST) | Yes — [API.md](API.md) |
| Mark / auto-fingerprint HAOS | **Done** (`os_type=haos`, auto via `ha` CLI) |
| CLI facts + System Info (Core / OS / Supervisor, disk) | **Done** |
| OS update **check** / **apply** via `ha` CLI (apt equivalent) | **Done** |
| HAOS capability envelope (no Docker fleet; HA updates ≠ apt) | **Done** |
| Host deps (SSH add-on, rsync, `ha` CLI) | **Done** (copy + probes; wiki install detail later) |
| Core REST (LLAT) / Supervisor add-ons list | **No** (later) |
| HA custom component → PiHerder | **No** (≥1.0) |

**v0.9 outcome:** full **HAOS + SSH** is a first-class host: detect, auto-mark, System Info + CLI stats, OS check/apply via `ha`, honest capability bar. **Further HA integration (REST, S1 container, path 2, add-ons) is parked for a later release.**

---

## 2. Operator scenarios (product model)

| | **S1 — HA container** | **S2 — Full HAOS + SSH** |
|--|------------------------|---------------------------|
| **Where HA runs** | Container on a managed Docker host | Appliance OS (Pi / VM); HA *is* the host |
| **PiHerder Server** | Usually the Docker host | The HAOS box itself |
| **Connect** | Host SSH | **SSH add-on** (root / add-on user) + key |
| **Stats without LLAT** | Weak (image name, port) | Strong: **`ha` / `ha core` CLI** |
| **Docker fleet mgmt** | Yes on host | **Not available** (supervisor-owned; not PiHerder compose) |
| **Update check / apply** | apt (existing) | **`ha` CLI** — core + OS + supervisor (apt equivalent for HAOS) |
| **v0.9 ship** | **Out** (document only) | **In** — primary scope |
| **Later full integration** | Core REST + LLAT | REST optional; SSH CLI remains primary for host facts/ops |

### 2.1 v0.9 lock (operator decision 2026-07-23)

> **This release = S2 Full HAOS only.**  
> PiHerder manages that Pi/appliance **over SSH**. Container-only HA (S1) and REST/LLAT stay roadmap for later stages.

```text
Path 1 (0.9) — PiHerder → HAOS via SSH
  Managed Server
       │
       ├─ Prerequisites     SSH add-on enabled · rsync package · deploy PiHerder key
       ├─ Fingerprint       os-release · command -v ha · ha core / os / supervisor info
       ├─ Auto-mark         os_type / profile = haos when confident
       ├─ UI                HAOS chip · CLI-derived stats (version, updates available)
       ├─ OS check path     ha core|os|supervisor info → update_available / version_latest
       ├─ OS apply path     ha core|os|supervisor update  (opt-in, audited — apt upgrade equiv)
       └─ Capability bar    No Docker project/compose fleet mgmt
                            Backups OK if rsync · Kuma host-service OK · least-priv skip

Path 1 (later) — Core REST / LLAT (S1 + optional S2 depth)
Path 2 (later) — HA → PiHerder custom component (≥1.0)
```

**Port note:** UI default for HA is often **8123**, but operators commonly terminate TLS on **443** (or other ports). **Do not treat open 8123 as required or sufficient** for HAOS identity. SSH fingerprint is authoritative for managed servers; nmap port/kind is advisory only if used at all.

---

## 3. Install types (full matrix — 0.9 implements HAOS row)

| Install | Typical host | SSH | Supervisor / `ha` CLI | v0.9 |
|---------|--------------|-----|------------------------|------|
| **HAOS** | Appliance VM / Pi | SSH add-on | Yes | **Ship** |
| **Supervised** | Debian + Supervisor | Yes | Often yes | Out (may share probes later) |
| **Container** | Docker on managed host | Via host | No | Out (S1 later) |
| **Core** | venv / bare metal | Yes | No | Out |

---

## 4. Architecture principles (locks)

1. **PiHerder owns fleet truth**; HA owns home automation truth — do **not** mirror entities into Postgres.  
2. **0.9 path 1 = SSH-native HAOS**, not REST.  
3. **Opt-in / auto-detect with confidence:** auto-mark when SSH probes agree; allow manual override.  
4. **Honest capability envelope:** no Docker Compose fleet management on HAOS; **updates use HA CLI**, not apt.  
5. **Same product surfaces, different backend:** fleet “OS updates check” / “OS patch” for HAOS invoke `ha …` instead of `apt-get`.  
6. **Host dependencies first-class** (SSH add-on, rsync); operator may supply install notes for wiki.  
7. **Mutating updates are opt-in + audited** (same bar as `os_patch_enabled` on Debian).  
8. **Secrets later:** LLAT when REST ships; not required for 0.9.  
9. **No live HA in CI** — fixtures + mocked SSH CLI JSON only.  
10. **Bidirectional is two products:** path 2 remains ≥1.0; API tokens already work today.

---

## 5. Host dependencies (HAOS Server)

Required for PiHerder to manage an HAOS node as a Server:

| Dependency | Why | Notes |
|------------|-----|--------|
| **Terminal & SSH add-on** (or equivalent SSH access) | All path-1 work and backups | Enable in HA; expose port operator chooses; deploy PiHerder public key |
| **`rsync` package** | Directory backups (plain rsync, typically as root) | Install via HAOS packages / add-on guidance — **exact steps: operator docs when ready** |
| **Network reachability** | PiHerder → SSH port | Firewall / VLAN as for any managed host |

Already handled in product (keep / surface in HAOS UI copy):

| Behaviour | Status |
|-----------|--------|
| Least-priv automated provision | **Skipped** — HAOS guidance copy (not Debian sudoers) |
| Backup rsync path | Prefer plain rsync when sudo unavailable (root/HAOS) |
| OS patch / update check | **Done:** HA CLI backend when profile is HAOS |
| Docker base dir / compose inventory | **Not applicable** — UI discourages Docker feature on HAOS |

Wiki: [HAOS hosts](../wiki/day-to-day/haos-hosts.md) (SSH add-on + rsync + key + capability envelope). Optional deeper install steps from operator later.

---

## 6. Path 1 — v0.9 feature catalog (HAOS)

| Idea | Value | Ship? |
|------|-------|-------|
| **HA1** Mark server as HAOS (+ UI chip) | Operator clarity | **Yes** |
| **HA2** Auto-detect via SSH (`os-release`, `ha` CLI) | Auto-mark HAOS node | **Yes** |
| **HA3** Read-only CLI facts (version, update flags) | Stats without LLAT | **Yes** |
| **HA-upd** OS update **check** via CLI | apt-check equivalent | **Yes** — wire into existing check path |
| **HA-upd-apply** OS **upgrade** via CLI | apt-upgrade equivalent | **Yes (opt-in)** — same feature flag / job family as os patch |
| Capability chrome: no Docker fleet mgmt | Honest UX | **Yes** |
| Host-deps callout (SSH add-on, rsync) | Onboarding | **Yes** (copy; deep steps when operator provides) |
| nmap kind / port 8123 | Weak LAN signal | **Optional / low** |
| Manual mark override | Edge cases | **Yes** if cheap |
| REST / LLAT | Rich Core API | **Out** (v1.0) |
| Container HA (S1) | Different model | **Out** |
| Other CLI writes (`ha core restart`, host reboot) | High risk | **Later** unless already covered by reboot flow |
| Supervisor **add-ons** inventory / per-addon update | Depth | **Out** (HA6) — core/OS/supervisor only in 0.9 |
| Path 2 custom component | HA → PiHerder | **Out** (HA5 ≥1.0) |

### 6.1 Data model (lean Option A)

```text
Server:
  os_type: "haos" | …   # reuse existing field where possible
  os_patch_enabled      # same flag as Debian — gates apply path for HA CLI updates
  os_updates_count      # aggregate “components with update available” (see §6.4)
  ha (json or columns — TBD at implement):
    marked_by: manual | detected
    detected_at
    detection_method: ssh | manual
    core_version, core_version_latest, core_update_available
    os_version, os_version_latest, os_update_available
    supervisor_version, supervisor_version_latest, supervisor_update_available
    machine / channel   # if present
    last_facts_at
    last_facts_error
    base_url            # optional deep link; operator-set
```

No Integration row required in 0.9.

### 6.2 SSH discovery & facts (read-only)

Timeout-bounded; **no** package install from PiHerder; mocked in unit tests.

| Probe | Use |
|-------|-----|
| `cat /etc/os-release` | On true HAOS host: `ID=hassos`. **SSH add-on** often reports **Alpine** — do not require hassos here |
| `command -v ha` | CLI present (primary signal from add-on) |
| `ha core info` | Core version + **update_available** / version_latest |
| `ha os info` | HAOS version + update flags |
| `ha supervisor info` | Supervisor version + update flags |
| `ha host info` | Kernel, operating_system, **disk_free / disk_used / disk_total** |
| `ha host disks usage` | Usage breakdown for System Info |

**JSON:** `ha … --raw-json` returns `{"result":"ok","data":{…}}` — always unwrap `data`. Prefer structured facts in `os_updates_summary` / diagnostics cache; never store full CLI dumps long-term.

**Auto-mark policy (proposed):**

- If `os-release` says HAOS **or** (`ha` present **and** `ha core info` succeeds) → set `os_type=haos`, `marked_by=detected`.  
- Prefer on connect / host-facts / scheduled **os_update_check** job path.  
- Manual override if we expose mark control.

### 6.3 Capability envelope (HAOS Server UI)

When server is HAOS:

| Surface | Behaviour |
|---------|-----------|
| **Docker** projects / compose inventory / fleet start-stop | **Unavailable** — supervisor-managed, not PiHerder compose |
| **OS update check** | **Available** — HA CLI backend (not apt) |
| **OS upgrade / patch apply** | **Available if** `os_patch_enabled` — HA CLI update commands; confirm + audit |
| **Least-priv provision** | Skip + existing guidance |
| **Backups** | Available if rsync works; call out package dep |
| **Host services** / Kuma bind | Available |
| **SSH access** panel | HAOS-oriented copy (add-on + key + rsync) |
| **Stats** | CLI-derived strip: versions + which of core/OS/supervisor can update |

### 6.4 HA CLI as apt equivalent (update check + apply)

**Product rule:** if the node is HAOS, the fleet **OS check** and **OS upgrade** surfaces stay; the **implementation** switches from apt to `ha` / supervisor CLI.

#### Check (maps to `check_os_updates` / `os_update_check` jobs)

| Component | Probe (illustrative) | Signal |
|-----------|----------------------|--------|
| Core | `ha core info` | `update_available`, `version` → `version_latest` |
| OS | `ha os info` | same shape |
| Supervisor | `ha supervisor info` | same shape |

**Fleet rollup (proposed):**

- `os_updates_count` = number of components with `update_available` (0–3), **or** a small integer “updates ready” the dashboard already understands.  
- UI sample / detail: list `core`, `os`, `supervisor` with current → latest (not apt package names).  
- `supported: true` for `os_type=haos`.  
- `reboot_pending`: only if HA CLI/host info exposes a clear flag; otherwise leave false / separate host reboot flow — do not invent.

No `apt-get update` on HAOS.

#### Apply (maps to `os_patch` job / upgrade steps)

| Intent | CLI (illustrative) | Notes |
|--------|--------------------|--------|
| Update Core to latest | `ha core update` | Long-running; Core restarts |
| Update OS to latest | `ha os update` | May reboot host |
| Update Supervisor | `ha supervisor update` | Often required **before** Core can move |
| Pin version (optional later) | `ha core update --version x.y.z` | Out of 0.9 unless free |

**Step model vs Debian:**

| Debian `os_patch` steps | HAOS analogue |
|-------------------------|---------------|
| `update` (apt update) | Refresh facts / ensure supervisor reachable (cheap `ha … info`) |
| `upgrade` | Apply selected components: supervisor → core → os (safe default order) |
| `full-upgrade` | **Not used** (or same as upgrade) |
| `autoremove` | **N/A** — omit on HAOS |

**Default apply order (proposed lock):**

1. **Supervisor** if update available (unblocks Core).  
2. **Core** if update available.  
3. **OS** if update available (most disruptive; may reboot).

Operator UI should show **what will run** before confirm (component list), not a silent apt log.

**Controls:**

- Respect **`os_patch_enabled`** (or rename copy to “HA updates” on HAOS servers without a second flag).  
- Confirm dialog + audit job (existing os_patch patterns).  
- Long SSH timeouts; stream CLI output into existing os_patch progress log if possible.  
- Failures: surface CLI stderr; do not fall back to apt.  
- **Schedules:** existing os_update_check / os_patch schedules work if backend branches on `os_type`.

**Explicitly out of HA-upd 0.9:**

- Per-add-on updates (`ha addons update …`) — HA6 later.  
- Forced version pins in UI (CLI supports it; product later).  
- Auto-apply without `os_patch_enabled` / without confirm for interactive path.

### 6.5 Future REST (out of 0.9)

Document only: Core `GET /api/`, `/api/config` with LLAT; Supervisor HTTP for add-ons. Optional alternate to CLI later. **0.9 path is SSH + `ha` CLI only.**

---

## 7. Path 2 — HA → PiHerder (post-0.9)

Unchanged: custom component / add-on ≥1.0. Operators can already use API tokens ([API.md](API.md)). Not part of 0.9 ship bar.

---

## 8. Relationship to existing systems

| System | Interaction |
|--------|-------------|
| **SSH onboarding** | Extend HAOS guidance: SSH add-on + **rsync** + key; keep least-priv skip |
| **Backup** | Plain rsync path; fail with actionable “install rsync” if missing |
| **OS patching** | Branch on `os_type=haos`: check/apply via `ha` CLI; reuse jobs, flags, dashboard counts |
| **Docker inventory** | Do not present HAOS as a compose fleet host |
| **Integrations / Kuma** | Host-service bind remains the “is HA UI up?” path |
| **LAN Discovery** | Port ≠ identity; optional weak kind only |
| **API tokens** | Path 2 client only |

---

## 9. Open decisions

| # | Question | Lean (2026-07-23) |
|---|----------|-------------------|
| **D1** | Profile field vs reuse `os_type=haos`? | Prefer align with **`os_type`** + small `ha` facts blob if needed |
| **D2** | REST in 0.9? | **No** |
| **D3** | Auto-mark on every facts refresh? | **Yes** when probes confident |
| **D4** | nmap `home_assistant` kind? | **Low priority** — port unreliable (443 etc.) |
| **D5** | Which CLI fields on UI? | Per-component version + latest + update flag; avoid raw dumps |
| **D6** | Hide vs disable Docker UI? | **Disable / empty state** with one-line reason |
| **D7** | Operator install doc for rsync / SSH add-on | **Stub now**; full steps when operator provides |
| **D8** | `os_updates_count` meaning on HAOS? | Count of updatable components (core/os/supervisor), not package count |
| **D9** | Apply all available vs pick components? | **0.9 lean:** apply all available in supervisor→core→os order; component picker later if needed |
| **D10** | Reuse `os_patch_enabled` flag? | **Yes** — same switch; label copy “HA updates” on HAOS chrome |

---

## 10. Phases

| Phase | Content | Window |
|-------|---------|--------|
| **P0** | Architecture + **S2 lock** + CLI update model (this doc) | **Done** |
| **P1** | HA1 mark + chip + capability chrome (no Docker fleet) | **Done** |
| **P2** | HA2/HA3 SSH detect + auto-mark + CLI version/update facts | **Done** |
| **P3** | **HA-upd** check path + **HA-upd-apply** opt-in apply | **Done** |
| **P3b** | System Info: HA versions + `ha host` disk / usage | **Done** |
| **P4** | Host-deps copy + wiki install detail | **Partial** — SSH modal guidance; operator wiki steps when ready |
| **P5** | REST + S1 container | **Later** (post-0.9) |
| **P6** | Path 2 component; add-on updates; version pins UI | **Later** |

Unit tests: pure parsers for `ha * info` fixtures + branch in `check_os_updates` / apply. E2E: HAOS chrome only if UI landed — no live HAOS.

---

## 11. Explicit non-goals (0.9)

- Container-only HA (S1) as a first-class profile.  
- Core REST / LLAT / Supervisor HTTP API (CLI is the path).  
- Replacing HA dashboards, energy, history, or entity sync.  
- PiHerder-driven Docker **compose** management on HAOS.  
- Running **apt** on HAOS (use `ha` instead).  
- Per-add-on update matrix in 0.9.  
- Automated least-priv like Debian.  
- Unaudited CLI mutations; apply stays behind `os_patch_enabled` + confirm.  
- Live Home Assistant in GitHub Actions.  
- Shipping an HA add-on inside the PiHerder image.

---

## 12. Security bar

| Topic | Bar |
|-------|-----|
| SSH | Existing key model; root/add-on user common on HAOS |
| Probes | Read-only CLI for check/facts; timeouts; no remote package install |
| Apply | Confirm + audit; long timeouts; log streaming; order supervisor→core→os |
| Future LLAT | Fernet; never log |
| Other host actions | Reboot / restart: existing or later flows; step-up where destructive |
| RBAC | Viewer: read chips/stats; admin/operator: mark + SSH config |

---

## 13. Changelog (planning)

| Date | Note |
|------|------|
| 2026-07-23 | Document opened; path 1 lean mark/SSH; path 2 ≥1.0; REST lean v1.0 |
| 2026-07-23 | **Scope lock:** v0.9 = **S2 Full HAOS over SSH** only; auto-mark + CLI stats; deps SSH add-on + rsync; no Docker fleet mgmt; port not identity (443 common); S1/REST later |
| 2026-07-23 | **HA updates:** OS check/apply for HAOS use **`ha` core/os/supervisor CLI** as apt equivalent; reuse `os_patch_enabled` + jobs; apply order supervisor→core→os; add-ons later |
| 2026-07-23 | **Implement start:** `app/services/haos.py`; `os_patching` / host_deps / jobs auto-mark; server UI chip + HA update modal; unit `tests/test_haos.py` |
| 2026-07-23 | System Info: unwrap HA CLI JSON `data` envelope; disk via `ha host info` + `disks usage`; busybox `df` fallback; live-verified on HAOS |
| 2026-07-23 | **v0.9 HA path 1 closed** — further HA integration deferred to later release |

**End of feature plan** — living; implement against §2.1 and §6.
