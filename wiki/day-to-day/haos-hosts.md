# Home Assistant OS (HAOS) hosts

## What this is

PiHerder can manage a **Home Assistant OS** appliance as a normal fleet **Server** over **SSH** (Terminal & SSH add-on). Updates, system facts, and backups use the **`ha` CLI** and plain **rsync** — not apt and not Docker Compose fleet management.

This is **path 1** for v0.9: PiHerder → HAOS via SSH. Deeper REST integration, container-only HA, and an HA→PiHerder custom component are **later** — see [FEATURE_PLAN_HOME_ASSISTANT.md](https://github.com/bjorngluck/piherder/blob/main/docs/FEATURE_PLAN_HOME_ASSISTANT.md). HA can already call PiHerder today with [API tokens](../operations/api-tokens.md).

## Why it exists

Many labs run HAOS next to Debian/Pi hosts. Operators want one dashboard for “is it up, is it backed up, are Core/OS/Supervisor updates available?” without pretending HAOS is a compose box or an apt distro.

## When to use it

- Full **HAOS** (or Supervised-like) machine with **SSH add-on** enabled  
- You manage that Pi/VM as a **Server** in PiHerder  
- **Not** for “Home Assistant is only a Docker container on another host” in 0.9 (mark the Docker host as Debian; optional later)

---

## Prerequisites on the HAOS box

| Dependency | Why |
|------------|-----|
| **Terminal & SSH** add-on (or equivalent SSH) | All path-1 work |
| **PiHerder public key** installed for the SSH user (often `root`) | Key auth |
| **`rsync` package** on the host | Directory backups (plain rsync, typically as root) |
| Network reachability | PiHerder → SSH port |

Exact package install steps for rsync may vary by HAOS version — enable the SSH add-on first, then install rsync with your usual HAOS package method. PiHerder does **not** install packages remotely.

!!! note "SSH add-on is Alpine"
    The add-on shell’s `/etc/os-release` often says **Alpine**, not `hassos`. Identity uses the **`ha` CLI**, not that file alone. System Info still shows **Home Assistant OS** from `ha host info`.

---

## End-to-end: add and verify HAOS

1. [Add a server](add-server.md) — hostname/IP, SSH user (often `root`), port.  
2. Deploy the PiHerder key via the wizard or **SSH access**; **Test connection**.  
3. **Edit → General → Host profile** → **Home Assistant OS (HAOS)** (or run an OS check and let auto-mark set `os_type=haos`).  
4. **Edit → Features** → enable **HA updates** (same flag as OS patch) and **Backups** if you want rsync. Leave **Docker / containers** off.  
5. **SSH access → Check dependencies** — expect **`ha` CLI** (not apt) when HA updates is on; **rsync** when backups is on.  
6. **System info** — Core / OS / Supervisor versions, free disk (`ha host info` / disks usage).  
7. **Actions → Check HA updates** — count of components with updates (0–3).  
8. When ready, **HA update…** — apply order **supervisor → core → OS** (confirm + audit).

**Done when:** hero shows **HAOS** chip; System info shows real versions; check job succeeds; deps overall is ok for enabled features.

!!! note "Screenshots & testing"
    **P0 recapture (operator in progress):** `server-detail-haos.png`, `system-info-haos.png` — [screenshots README](https://github.com/bjorngluck/piherder/blob/main/wiki/assets/screenshots/README.md). Until those land, the generic server-detail shot below is illustrative only. Live fleet testing of System info / HA check-apply is operator-owned (not in CI).

<figure class="ph-figure" markdown>
  ![Server detail (fleet host)](../assets/screenshots/server-detail.png)
  <figcaption>Server detail (generic pack) — on HAOS expect HAOS chip, HA updates chrome, System info with Core/OS/Supervisor + disk.</figcaption>
</figure>

---

## What PiHerder does on HAOS

| Capability | Behaviour |
|------------|-----------|
| **Identity** | Manual profile or auto-mark when `ha` works / probes confident |
| **System info** | Core, OS, Supervisor versions + update flags; disk free/used/total; usage breakdown |
| **OS check** | `ha core\|os\|supervisor info` — **not** apt |
| **OS apply** | `ha supervisor\|core\|os update` when available; opt-in via same OS patch flag / schedules |
| **Backups** | Plain **rsync** if package present (root / no sudo path) |
| **Kuma host service** | Bind UI reachability as a host service (not Docker project) |
| **Least-priv automation** | **Skipped** — guidance only |
| **Docker Compose fleet** | **Not used** — Supervisor owns containers |

### Update counts

`os_updates_count` on HAOS is the number of **components** with `update_available` (Core / OS / Supervisor), not apt package count. UI sample lines look like `core 2026.7.3 → 2026.8.0`.

### Apply order

1. Supervisor (often required before Core can move)  
2. Core  
3. OS (most disruptive; may reboot)

Schedules reuse the same check/apply machinery as Debian hosts; the backend branches on `os_type=haos`.

---

## What it does *not* do (0.9)

- Poll Home Assistant **REST** with a long-lived access token  
- Manage **add-on** updates one-by-one  
- Treat HA as a **Docker Compose** project on the appliance  
- Run **apt** upgrade on HAOS  
- Ship an HA custom component that talks to PiHerder (use [API tokens](../operations/api-tokens.md) for HA → PiHerder automations today)

---

## Related

| Topic | Page |
|-------|------|
| Add server / SSH | [Add a server](add-server.md) |
| Check vs apply | [Updates & patching](updates-and-patching.md) |
| Backups | [Backups](backups.md) |
| SSH / rsync issues | [SSH, rsync & dependencies](../troubleshooting/ssh-rsync.md) |
| Architecture plan | [FEATURE_PLAN_HOME_ASSISTANT.md](https://github.com/bjorngluck/piherder/blob/main/docs/FEATURE_PLAN_HOME_ASSISTANT.md) |
| API from HA | [API tokens](../operations/api-tokens.md) · [API.md](https://github.com/bjorngluck/piherder/blob/main/docs/API.md) |
