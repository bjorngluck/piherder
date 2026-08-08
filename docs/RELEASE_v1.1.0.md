# PiHerder v1.1.0

**Status:** **Ready to tag** — freeze on `v1.1.0-dev` (QA + screenshot pack + version bump)  
**Date:** 2026-08-08  
**Git branch:** `v1.1.0-dev` → merge `main` → tag `v1.1.0`  
**Package / image version:** `1.1.0`  
**Baseline:** `v1.0.0` (first production — 2026-07-28)  
**Theme:** **Elevate production** — certs · discovery · identity · operator UX · topology/maps · integrations/API  

**Plans:** [PLAN_v1.1.0.md](PLAN_v1.1.0.md) · map [FEATURE_PLAN_MAP_INTERACTIVITY.md](FEATURE_PLAN_MAP_INTERACTIVITY.md)  
**Next train:** [PLAN_v1.2.0.md](PLAN_v1.2.0.md) (WebAuthn · SSO · webshell · gated demo · backup retry)  
**Prior:** [RELEASE_v1.0.0.md](RELEASE_v1.0.0.md)  
**Roadmap:** [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · [SPEC.md](../SPEC.md)  
**Docs:** https://piherder-docs.hacknow.info/

**Image (at publish):** [bjorngluck/piherder](https://hub.docker.com/r/bjorngluck/piherder) — multi-arch `linux/amd64` + `linux/arm64`  
**Tags (at publish):** `1.1.0` · `1.1` · `latest` (keep `1.0` / `1.0.x` pins valid)

---

## Why this release

v1.0 established a production security bar. **v1.1** makes day-to-day operation sharper: certificate deploy is a real wizard, LAN discovery is manageable at scale, navigation stays out of your way, maps show ports and kinds at a glance, and light alert/mail channels land without waiting for full SSO.

No silent contract breaks. Additive minor. Same master key and upgrade path as 1.0.

---

## Highlights

### Certificates — deploy-target wizard & verify

- **Deploy target** (renamed from service map): one cert layout per target (`pair` · `combined` · `pfx`)
- Modal **wizard**: name · host · service · type · dest · perms · restart · sudoers · simulate · save
- Top **Deploy** chrome; **Replace PEM** as menu + modal
- **Sudoers** generation aligned with actual deploy stage paths
- Actionable deploy errors
- **Post-deploy verify**: host fingerprint + optional TLS endpoint probe (`openssl s_client` on any TLS port)
- **Simulate privileges** over SSH before saving
- Cert alerts: open Notifications on deploy/verify fail; auto-resolve on success (and on target/cert delete)
- Detail UI: compact list + detail modals; services grouped under host

**Wiki:** [Certificates](../wiki/integrations/certificates.md) · [Obtain ACME (education)](../wiki/integrations/certificates-obtain-acme.md)  
**Note:** Native **ACME-in-herder** remains under consideration for **≥ v1.3** — not in 1.1. Education path + external Certbot cookbook only.

### LAN Discovery hygiene

- **Last seen** (relative + absolute) on devices list and modal; offline after stale window (14 days)
- **Hide / Unhide** (ignored state); Hidden filter; hidden devices off maps + Hosts overlay
- **Purge** single device + **Purge N offline** when Offline filter active (linked must unlink first)
- Filter chips with **honest counts**; offline toolbar stat; search includes last-seen / hidden tokens
- Soft-stale offline flag only — **never auto-delete**

**Wiki:** [LAN Discovery](../wiki/integrations/lan-discovery.md)

### Identity, trusted devices & light alerts

| Piece | Behaviour |
|-------|-----------|
| **Trusted devices** | Device type (from UA), last IP, friendly rename, last used / expires, revoke one or all |
| **AB-polish** | Rename behind ✎ Edit (not always-visible form) |
| **Password policy** | Existing fixed policy (≥10, upper/lower/digit) — unchanged, still enforced |
| **SMTP (H-lite)** | Settings → **Alerts**: host/port/security/user/password (Fernet) / from / recipients; **Send test** |
| **Forgot password (G1-lite)** | Login flow when SMTP configured; hashed token, expiry, rate limit |
| **Webhook (Wh-lite)** | Settings → **Alerts**: URL + optional number/recipients/secret; filters for notifications / jobs / backups; env `WEBHOOK_*` fallback |

**Wiki:** [Users / roles](../wiki/account-security/users.md) · [Two-factor](../wiki/account-security/two-factor.md) · settings/alerts pages as shipped

### Operator UX — schedules, pins, host jump

- **Human-readable cron** (`cron_human`) + presets next to raw expressions on backup / OS / container / nmap / self-backup / stale-cleanup forms
- **★ Favourites** (per-user pins, cap 24): host features, app pages, integrations; header ★ menu; map pins deep-link `#map` so the SVG opens
- **Jump host** on Overview / Docker / Backups / Services: same feature on another fleet host; list respects feature flags

### Topology & maps

**Baseline G stream**

- Stack panel **published ports** as `host→container` chips
- Manual runtime edges can target **other host / project / container**

**Map interactivity (M1–M4, elevated into 1.1)**

| Theme | What you get |
|-------|----------------|
| **Canned icons** | Device/discovered/host kinds as glyphs (kind override follows icon) |
| **Focus pop-out** | Locked focus ~**1.30×** scale + lift; dim neighbours; pan/zoom unchanged |
| **Port roles** | Heuristics + sticky **PortAnnotation** roles (`web`, `dns`, `db`, …) |
| **Host ports expand** | Progressive **compact → ports-only → by-service**; nmap∪docker inventory |
| **Discovered devices** | Same ports expand via `nmap_device_id` |
| **Stack containers** | Published ports on stack boxes; scoped callout |
| **Desktop click fix** | Overlay clicks work with mouse (pointer capture / focus steal fixed); touch already worked |

**Not in 1.1:** custom icon pack (**M5**), full multi-target cert deploy as Job polish residual.

**Wiki:** [Network maps / DNS fabric](../wiki/integrations/dns-fabric.md)

### Integrations & API

- **Generic URL** integration (`generic_url`): presets **Home Assistant** · **Frigate** · **n8n** · **custom** — base URL, optional health path / bearer probe; bind as service chips. Not a deep vendor adapter.
- Settings → API: **Try a token** + OpenAPI / ReDoc deep links

**Wiki:** [Generic links](../wiki/integrations/generic-links.md) · [API.md](API.md)

### Backups (reliability messaging)

- Clearer rsync failure detail: prefer **concrete file/path errors** over the bare “code 23 / see previous errors” summary
- Wiki: I/O vs vanished-file diagnosis

Busy-source vanish behaviour remains a **known issue** (below).

### Docs & education

- ACME **education** path (cookbook + optional Certbot helper stance) without shipping product ACME
- Mid-train wiki/ADMIN/ROADMAP sync for streams A–D–G–I + Cap
- Operator process notes stripped from public wiki pages (v1.0 docs hygiene carried)

---

## Verify (operator smoke)

Use after upgrade; expand at freeze if needed.

- [ ] Login / logout; 2FA path if enabled  
- [ ] Account → Trusted devices: rename, revoke  
- [ ] Settings → Alerts: SMTP test (if configured); webhook fields save  
- [ ] Forgot password visible only when SMTP OK  
- [ ] ★ pin a host feature + an app map page; jump host on Docker/Backups  
- [ ] Cron fields show plain-English schedule text  
- [ ] Cert: open deploy-target wizard, simulate privileges, deploy + verify one target  
- [ ] LAN: filter Offline/Hidden; last-seen visible; hide then unhide a device  
- [ ] Hosts map: kind icons; focus pop-out; **Tap for ports** on desktop **and** touch  
- [ ] Stack panel shows host→container ports; optional cross-host edge  
- [ ] Generic URL integration: Test/Poll + Services chip  
- [ ] Settings → API: Try a token / ReDoc link  
- [ ] One server backup completes (or fails with readable rsync detail)  
- [ ] Self-backup still works with same master key  

---

## Known issues (ship with awareness)

Accepted for **v1.1.0** — not tag blockers. Tracked for **v1.2** (or later) unless noted.

| ID | Area | Issue | Destination |
|----|------|--------|-------------|
| **KI-rsync-vanished** | Backups | **Rsync can fail when source files disappear mid-transfer.** Busy trees (e.g. **Frigate NVR** and similar: recordings indexed, rotated, moved, or deleted while rsync walks the path) commonly produce rsync **code 23** (partial transfer) or **code 24** (vanished files). This is an **expected class of failure** on live media/NVR disks, not necessarily a PiHerder misconfiguration. Other sources in the same job may still succeed. | **v1.2+** — explore handling (excludes / volatility-aware paths, treat vanished as soft success where safe) and introduce a **retry mechanism**. [PLAN_v1.2.0.md](PLAN_v1.2.0.md) **B-retry**. Operator notes: [Backups troubleshooting](../wiki/troubleshooting/backups.md#vanished-files-busy-sources) · [SSH/rsync](../wiki/troubleshooting/ssh-rsync.md#backups-rsync-code-23-partial-transfer) |

### KI-rsync-vanished — operator guidance (1.1)

1. Read the job / audit error: look for `vanished`, `code 24`, or partial **code 23** under a live media tree.  
2. Re-run the backup off-peak; a second pass often completes when the tree is quieter.  
3. Prefer backing up **stable** bind mounts (config, DB dumps) separately from high-churn recording directories when practical.  
4. Distinguish from **I/O / mount** failures (`Input/output error`, ext4 `shutdown`) — those are host disk issues, not vanished-file churn.

### Residual polish (capacity / non-blocking)

Items that may remain imperfect at freeze without blocking the tag — promote into 1.1.x or 1.2 as QA dictates:

| Area | Note |
|------|------|
| Cert multi-target **as Job** (**P-job**) | Not a ship gate for 1.1 |
| Discovery worker **heartbeat** (**S-hb**) | Residual |
| Custom map icon pack (**M5**) | Roadmap |
| Screenshot pack recapture for new 1.1 surfaces | **Done** for freeze (wiki assets + new 1.1 shots) |
| Version bump in `pyproject.toml` / image | **Done** → `1.1.0` (Hub multi-arch at publish) |

---

## Intentionally not in v1.1.0

| Item | Destination |
|------|-------------|
| WebAuthn / passkeys | **v1.2** — [PLAN_v1.2.0.md](PLAN_v1.2.0.md) |
| SSO / OIDC | **v1.2** (pulled into big 1.2 train) |
| Webshell / web SSH | **v1.2** |
| Gated public demo site (`DEMO_MODE`, CF Access) | **v1.2** |
| Backup vanished-file soft-success + **retry** | **v1.2+** (**KI-rsync-vanished**) |
| ACME-in-herder (product issuance) | ≥ **v1.3** under consideration |
| Full multi-channel alert matrix, insights product, multi-tenant | Later paths |
| Live SSH / nmap / NPM / HA in CI | Still fixtures & mocks only |

---

## Upgrade from v1.0.0

1. **Self-backup** (Settings) and/or volume snapshot.  
2. Keep the same **`PIHERDER_MASTER_KEY`**.  
3. Pull the 1.1 image (or build from tag):

   ```bash
   # after tag / Hub publish
   export PIHERDER_IMAGE=bjorngluck/piherder:1.1.0
   docker compose pull
   docker compose up -d
   ```

4. Alembic applies migrations on startup (see below).  
5. Smoke the [verify list](#verify-operator-smoke).

Full checklist: [ADMIN.md](ADMIN.md) · wiki [Upgrades](../wiki/operations/upgrades.md).

### Migrations since v1.0.0

Applied automatically on app start (Alembic). New tables/columns operators should know about:

| Rev (approx.) | Purpose |
|---------------|---------|
| `032` | Cert target verify fields |
| `033` / `034` | User favourites (pins) |
| `035` | Password reset tokens |
| `036` | Port annotations (sticky port roles) |

No operator data wipe. Encrypted secrets still require the **same** master key.

### Breaking / behaviour notes

| Topic | Expectation |
|-------|-------------|
| Semver | Additive minor — no silent API contract breaks |
| REST scopes | Compatible; new UI for try-token does not change token model |
| Deploy targets | Existing cert maps continue; new wizard enforces **one layout type** per target |
| Favourites | New optional UI; empty until operators pin |
| Backups | Failures may surface **richer** rsync detail; overall success rules unchanged (any failed source → job failed) |

---

## Install (new)

```bash
git clone https://github.com/bjorngluck/piherder.git
cd piherder
git checkout v1.1.0   # after tag
cp .env.example .env  # set PIHERDER_MASTER_KEY, SECRET_KEY, etc.
export PIHERDER_IMAGE=bjorngluck/piherder:1.1.0
docker compose up -d
```

Open the app URL → **Register** the first admin (no default password).  
Guide: [Install](https://piherder-docs.hacknow.info/getting-started/install/).

---

## Quality bar (freeze)

| Gate | Target |
|------|--------|
| Unit coverage | ≥ **55%** line on `app`; CI fail-under **55** |
| E2E | Playwright on touched shells; no live lab SSH/nmap/NPM/HA in CI |
| Docs | `mkdocs build --strict` at freeze |
| Security | No relaxation of 1.0 cookie / authz / validation bars |

*(Confirm coverage number and E2E green at PR approval.)*

---

## Developer notes

- Integration branch: **`v1.1.0-dev`**  
- Package version: `pyproject.toml` + `app.version_info` → **1.1.0**  

- Publish: [PUBLISH_IMAGE.md](PUBLISH_IMAGE.md)  
- Map plan: [FEATURE_PLAN_MAP_INTERACTIVITY.md](FEATURE_PLAN_MAP_INTERACTIVITY.md)  
- Notable code areas: `certificates` · `nmap` device ops · `nav_shortcuts` / favourites · `dns_fabric` ports + mesh JS · `integrations/generic_url` · `backup` error detail · Cap alerts/SMTP/reset  

---

## Changelog summary

Product work since `v1.0.0` is the elevation train on **`v1.1.0-dev`**.

At tag:

```bash
git log v1.0.0..v1.1.0 --oneline
```

Plan history: [PLAN_v1.1.0.md](PLAN_v1.1.0.md) § changelog / decision log.

### Theme map (for PR reviewers)

| Stream | Headline commits / themes (indicative) |
|--------|----------------------------------------|
| **A** Certs | deploy-target wizard, sudoers align, verify, alerts |
| **B** Discovery | S1–S4 last-seen / hide / purge / filters |
| **C + Cap** | trusted devices, webhook, SMTP, password reset |
| **D** | cron_human, favourites, host jump |
| **G + Map** | ports, cross-host edges, icons, pop-out, progressive ports, desktop click fix |
| **I** | generic URL links, API try / ReDoc |
| **Docs** | ACME education, RELEASE known issues, wiki troubleshooting |

---

## Freeze checklist (PR approval)

Copy into the merge PR and tick:

- [ ] QA sign-off on must surfaces (certs, maps ports desktop+mobile, discovery, pins, alerts)  
- [ ] Known issues reviewed (at least **KI-rsync-vanished**)  
- [ ] Version → `1.1.0` in package + version_info  
- [ ] Unit ≥55% · E2E green · `mkdocs build --strict`  
- [ ] This document: Status → **Tagged**, date filled, smoke list confirmed  
- [ ] Merge `v1.1.0-dev` → `main` · tag `v1.1.0` · Hub multi-arch publish  
- [ ] ROADMAP + SECURITY supported versions  
- [ ] Announce / WordPress notes as needed  

---

## Residual / post-1.1

| Item | Destination |
|------|-------------|
| WebAuthn · SSO · webshell · gated demo · backup **B-retry** | **v1.2** — [PLAN_v1.2.0.md](PLAN_v1.2.0.md) |
| ACME product issuance | ≥ **v1.3** under consideration |
| Security / data-loss patches | **v1.1.x** on `main` as needed |

---

*Promote Status to **Tagged** when git tag `v1.1.0` and Hub multi-arch publish complete.*
