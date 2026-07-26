# PiHerder v0.9.0

**Status:** **Tagged**  
**Date:** 2026-07-26  
**Git tag:** `v0.9.0`  
**Baseline:** `v0.8.0` (RC3 — LAN Discovery · screenshots · quality)  
**Theme:** Last pre-production — operator UX consistency · quality bar (unit ≥55%, E2E on touched surfaces) · **HAOS path 1 (SSH / `ha` CLI)**

**Plans:** [PLAN_v0.9.0.md](PLAN_v0.9.0.md) · [FEATURE_PLAN_HOME_ASSISTANT.md](FEATURE_PLAN_HOME_ASSISTANT.md) · [FEATURE_PLAN_LAN_NMAP.md](FEATURE_PLAN_LAN_NMAP.md)  
**Next:** [PLAN_v1.0.0.md](PLAN_v1.0.0.md) (first production)  
**Prior:** [RELEASE_v0.8.0.md](RELEASE_v0.8.0.md)  
**Roadmap:** [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · [SPEC.md](../SPEC.md)  
**Docs:** https://piherder-docs.hacknow.info/

**Image:** [bjorngluck/piherder](https://hub.docker.com/r/bjorngluck/piherder) — multi-arch `linux/amd64` + `linux/arm64`  
**Tags:** `0.9.0` · `0.9` · `latest`

---

## Highlights

### HAOS path 1 (SSH / `ha` CLI)

Full **Home Assistant OS** appliance support over SSH — not container HA, not REST/LLAT.

- Auto-mark / detect HAOS via `ha` CLI (SSH add-on is Alpine)
- **System Info:** Core / OS / Supervisor + disk
- **OS check & apply** via `ha` (supervisor → core → os order); reuses host update scheduling
- Host-deps copy for SSH add-on + rsync; capability bar; no Docker fleet mgmt on HAOS
- Operator wiki: [HAOS hosts](https://piherder-docs.hacknow.info/day-to-day/haos-hosts/)

### Operator UX consistency

- **LAN Discovery:** shared filter chrome; Offline flag (no auto-delete); Overview Scan now / vuln pack → **in-app modals**; Devices **List \| Map** (merged Network tab); Schedules/Runs **one dense list** all widths; server detail **Network path + LAN discovery** side-by-side
- **Catalog Network hub:** destination + DNS/settings **cards**; Host/External/Network/Adopt modals; stacked Host DNS rows; **By path type**
- **Path map:** NPM hub focus lights **all** proxied paths **and connector lines**
- **Kuma coverage:** dense bind table; mobile-friendly cards path earlier in the train
- **Templates:** **OOTB / Yours** badges + groups; from-host **additional files** + host vars; desired-file browser; **Accept host as desired**; always-write empty `.env` on deploy
- **Dense lists** across Docker, integrations, deployments, backups (one DOM model, CSS reflow)
- **Wizard micro-copy:** Connect order, HAOS/rsync hints, resume note
- **Structure cleanup:** shared compose/host_sync helpers; CSS concern split (`dns-hub`, fabric stack, ops)

### Operator QA bugs fixed (pre-tag)

| ID | Fix |
|----|-----|
| **A** | LAN **Scan now** in-app confirm (no browser `confirm`; cancel does not leave Queueing wait) |
| **B** | Version string only when **signed in** (anonymous footer brand only) |
| **C** | Path map **NPM** hub multi-path + connector focus |
| **D** | Docker logs **All services** (`__all__` → project-level `compose logs`) |
| **E** | Regenerate 2FA backup codes requires **password + TOTP/backup code** |

### Quality bar

- Unit coverage freeze **≥ 55%** line (`--cov=app`) — suite ~**57%**; CI fail-under **55**
- Playwright E2E on touched 0.9 chrome (shell, wizard, viewer RBAC, LAN shells, coverage empty CTA)
- Pipeline green at freeze: **Tests** + **E2E** + **Docs** on main

### Docs & screenshots

- Full operator wiki for HAOS, LAN 0.9 chrome, templates from-host, dense UI
- Screenshot pack recapture for 0.9 surfaces (fleet, HAOS, LAN, Network, Docker logs, templates, 2FA modal, …)

---

## Known issues (ship with awareness)

Accepted for **v0.9.0** — tracked for **v1.0** in [PLAN_v1.0.0.md](PLAN_v1.0.0.md):

| ID | Area | Issue |
|----|------|--------|
| **O** | Docker | Linked tool → browser Back can show stuck SSH “Collecting…” modal |
| **R** | Maps | Desktop second click should clear focus (mobile OK) |
| **T** | Brand | `ph_brand()` inside solid red/accent buttons hard to see |
| **U** | NPM certs | Mobile needs stackable rows (not wide scroll only) |
| **V** | Coverage | Kuma coverage columns can bleed on narrow viewports |
| **W** | Monitor | Mute chrome parity with Unmute |
| **X** | DNS | Network DNS Records labels / link-through clarity |
| **P** | Certs | Distribute sudoers + wizard-driven setup (discovery → likely v1.1 full) |

---

## Intentionally not in v0.9.0

| Horizon | Items |
|---------|--------|
| **v1.0.0** | Security/auth entry UX · known-issue burn-down · mobile cert rows · docs freeze — [PLAN_v1.0.0.md](PLAN_v1.0.0.md) |
| **Later HA** | Container HA (S1) · REST/LLAT · HA→PiHerder component · per-add-on updates |
| **Later platform** | Human-readable cron (E6) · selectable hero stats (E9) · full templates catalog redesign · web SSH · ACME-in-herder |
| **CI** | Live nmap / live HA / live SSH labs (fixtures & mocks only) |

---

## Breaking / migration notes

| Change | Action |
|--------|--------|
| **Alembic** | No new operator-facing migration gate beyond normal startup apply (0.8 nmap migrations already required) |
| **HAOS hosts** | Marked HAOS hosts skip Docker fleet mgmt; ensure SSH add-on + `ha` CLI for System Info / OS updates |
| Encrypted secrets | Same **`PIHERDER_MASTER_KEY`** required for restore and DR |
| Optional nmap | Profile `nmap` still opt-in; recreate nmap worker if fence env changed |

Existing v0.8.0 deployments: pull new image / checkout tag, keep `.env` + volumes, `docker compose up -d`.

---

## Install

```bash
git clone https://github.com/bjorngluck/piherder.git
cd piherder
git checkout v0.9.0
cp .env.example .env
# set PIHERDER_MASTER_KEY (Fernet) and SECRET_KEY — never use compose defaults in production
docker compose up -d
```

Optional pin:

```bash
export PIHERDER_IMAGE=bjorngluck/piherder:0.9.0
docker compose up -d
```

Docs: [Install](https://piherder-docs.hacknow.info/getting-started/install/) · [README](../README.md)

### Upgrade from v0.8.0

```bash
# 1) Self-backup + confirm PIHERDER_MASTER_KEY is safe offline
git fetch --tags
git checkout v0.9.0
docker compose pull
docker compose up -d
# Optional LAN Discovery:
# docker compose --profile nmap up -d
```

Migrations run on web startup. Review **HAOS** hosts and **LAN Discovery** chrome if you use those features.

---

## Package version

`pyproject.toml` / `APP_VERSION` → **`0.9.0`**

---

## Docs & tests

| Doc | Role |
|-----|------|
| [ADMIN.md](ADMIN.md) | Operator / deploy |
| [API.md](API.md) | REST `/api/v1` |
| [PUBLISH_IMAGE.md](PUBLISH_IMAGE.md) | Multi-arch publish process |
| [PLAN_v0.9.0.md](PLAN_v0.9.0.md) | Last pre-production ship plan |
| [FEATURE_PLAN_HOME_ASSISTANT.md](FEATURE_PLAN_HOME_ASSISTANT.md) | HAOS path 1 design |
| [PLAN_v1.0.0.md](PLAN_v1.0.0.md) | Next (first production) |
| Wiki | https://piherder-docs.hacknow.info/ |

**Unit tests:** full `tests/` pack — **≥ 55%** line; CI fail-under **55**.  
**E2E:** Playwright Chromium in `e2e/` (0.7–0.9 shells).

---

## Verify after upgrade

1. `docker compose ps` — web healthy; image `bjorngluck/piherder:…`
2. About page shows **0.9.0**
3. Server detail: Network path + LAN discovery side-by-side when discovery linked
4. HAOS host: System Info + HA update path (not bare apt-only)
5. Templates catalog: OOTB / Yours when both kinds present
6. Anonymous footer: **no** version string until signed in
7. Optional nmap: worker online; Scan now uses in-app confirm
8. Wiki builds: `mkdocs build --strict`

---

## Freeze checklist (maintainer)

- [x] Operator QA bugs A–E fixed
- [x] Screenshot pack recapture + wiki truth
- [x] Unit + E2E green on CI (fail-under 55)
- [x] `mkdocs build --strict`
- [x] Bump `pyproject.toml` + `APP_VERSION` → `0.9.0`
- [x] Finalize this file (Date, Status, package version)
- [x] Tag `v0.9.0` + Hub multi-arch (`0.9.0` / `0.9` / `latest`)

---

## Changelog sources

Product work since `v0.8.0` includes discovery/Network/coverage UX, dense list unification, HAOS path 1, templates OOTB/from-host, quality bar 55%+, structure cleanup, operator QA A–E, Path map connector focus, and the 0.9 screenshot pass. Full history: `git log v0.8.0..v0.9.0`.
