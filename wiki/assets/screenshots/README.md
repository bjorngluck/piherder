# Screenshots

Real UI captures live here. Wiki pages reference them like:

```markdown
![Dashboard](../assets/screenshots/dashboard.png)
```

Wireframe SVGs (`*.svg`) are legacy placeholders; wiki pages use real PNGs. You can delete unused SVGs once no external link points at them.

## Release policy

| Release | Screenshot bar |
|---------|----------------|
| **v0.6.0–v0.7.0** | Historical — PNG pack deferred / prose-first |
| **v0.8.0 RC3** (tagged) | Full pack landed — [RELEASE_v0.8.0.md](../../../docs/RELEASE_v0.8.0.md) |
| **v0.9.0** (tagged) | Operator recapture pack for 0.9 chrome — [RELEASE_v0.9.0.md](../../../docs/RELEASE_v0.9.0.md) |
| **v1.0.0** (tagged) | Full pack in place (operator-confirmed) — [PLAN_v1.0.0.md §8.2](../../../docs/PLAN_v1.0.0.md) · [RELEASE_v1.0.0.md](../../../docs/RELEASE_v1.0.0.md) |
| **v1.1.0** | Freeze pack landed — [RELEASE_v1.1.0.md](../../../docs/RELEASE_v1.1.0.md) |
| **v1.2.0** | Prior Hub — screenshot pack **landed** 2026-08-18. [RELEASE](https://github.com/bjorngluck/piherder/blob/main/docs/RELEASE_v1.2.0.md) |
| **v1.3.0** | Prior Hub — pack **landed** 2026-08-22. Maintainer QA: [QA_v1.3.0.md](https://github.com/bjorngluck/piherder/blob/main/docs/QA_v1.3.0.md) (not the operator wiki). [RELEASE](https://github.com/bjorngluck/piherder/blob/main/docs/RELEASE_v1.3.0.md) |
| **v1.4.0** | **Current train** (`v1.4.0-dev`) — pack **not landed**. Maintainer QA: [QA_v1.4.0.md](../../../docs/QA_v1.4.0.md). Theme: [Move a service](../../docker/service-migration.md) |

**Owner:** operator fleet testing (not CI). Replace PNGs in this directory; captions note when a figure may lag. After dropping files: `mkdocs build --strict`.

!!! tip "Capture from the freeze branch"
    Rebuild **`v1.4.0-dev`**: `docker compose build web && docker compose up -d web`.  
    App code is **not** bind-mounted — stale containers = stale chrome.  
    About / footer stay **1.3.0** until the freeze version bump.  
    Move wizard shots need `PIHERDER_SERVICE_MIGRATE=true` then recreate **web**.

---

## v1.4.0 — pack status {#v140--pack-status}

**Not landed.** 1.3 PNGs stay valid unless a row below says recapture. Do **not** redo Settings / Reports / Files / certs / LAN / templates unless chrome actually drifted.

Move wizard has **no** wiki figure yet — do not add `![…]` until the PNG exists (`mkdocs --strict` will fail on a missing file).

### Must capture — new 1.4 surfaces (no PNG yet)

| Pri | Suggested file | Surface | Must show | Wire into |
|-----|----------------|---------|-----------|-----------|
| **P0** | `docker-migrate-wizard.png` | Docker ⋯ **Move to another host…** | Hero **Move \<project\>**; dest select **To (Docker hosts only)**; HAOS and source absent | [Move a service](../../docker/service-migration.md) |
| **P0** | `docker-migrate-preflight.png` | Same wizard after dest pick | **Ready for copy**; dest folder path; leftover radios (**Leave stopped** checked); **Move service**. Wait overlay not required in frame | [Move a service](../../docker/service-migration.md) |
| **P0** | `docker-host-lock.png` | Project ⋯ **Lock to this host…** | Confirm modal: reason Hardware / Operator / Infrastructure + optional note | [Move a service](../../docker/service-migration.md) · [Docker](../../docker/overview.md) |
| **P0** | `docker-migrate-jobhold.png` | JobHold after Move | Title **Move \<project\>**; live log; **Succeeded** or **Failed** until **Close** (closeMode hold) | [Move a service](../../docker/service-migration.md) · [Jobs](../../day-to-day/jobs-audit-notifications.md) |
| **P1** | `docker-migrate-preflight-adopt.png` | Preflight on an NPM-only app (no fabric DNS row) | **Adopt into fabric** checkbox **unchecked**; warn that Move will PUT `forward_host` | [Move a service](../../docker/service-migration.md) · [NPM](../../integrations/npm.md) |
| **P1** | `docker-migrate-jobhold-start-source.png` | JobHold after copy or dest-up **fail** | **Start source stack** visible. Disposable stack only — or skip if you cannot stage a fail | [Move a service](../../docker/service-migration.md) |
| **P1** | `demo-files.png` | Public demo **Files** | Simulated / canned banner; folder list; no SFTP. Viewer account OK | [Host Files](../../day-to-day/host-files.md) · [Demo](../../operations/demo-site.md) |

### Must recapture — chrome changed in 1.4

| Pri | File(s) | 1.4 chrome to show |
|-----|---------|---------------------|
| **P0** | `docker-project-lifecycle.png` | Project ⋯ includes **Lock to this host…** and **Move to another host…**. Locked **Move** disabled with reason is ideal |
| **P1** | `dns-logical.png` | Path map **name → NPM → host → app** when the app shares the NPM host (n8n-class). Do not recapture if the 1.3 shot already shows an NPM hub |
| **P2** | `jobs-page.png` | Only if a `service_migrate` row or type chip is worth showing |
| **P2** | `dns-stack-panel.png` | **locked** badge on a locked compose project, if the panel is in frame |

### Spot-check only (1.3 pack still good)

| File | Why skip unless broken |
|------|------------------------|
| 1.3 Settings / Reports / Files / identity (`settings-*`, `reports.png`, `host-files*.png`, SSO / passkeys) | Landed 2026-08-22 |
| Certs (`certificates-*.png`) | Unchanged story |
| LAN (`nmap-*.png`) | Unchanged |
| Integrations / templates | Unchanged unless you want dest-follow in a Grafana/NPM caption |
| `backups-page.png`, `account-*`, `nav-host-jump.png` | Unchanged |
| HAOS shots | Spot-check — HAOS still never in Move dest list |
| Other `docker-*.png` | Recapture inventory only if pager chrome is in frame |

### Capture sequence (v1.4 freeze)

1. Rebuild **web** on `v1.4.0-dev`; flag **on**.  
2. **Lock modal** — `docker-host-lock.png` (Frigate or any unlocked project; Unlock after if you did not mean to keep it).  
3. **Docker ⋯** — recapture `docker-project-lifecycle.png` (Lock + Move in the menu).  
4. **Move wizard** — dest picker `docker-migrate-wizard.png` → preflight `docker-migrate-preflight.png` (leftover **stopped**).  
5. **Adopt** — NPM-only project: `docker-migrate-preflight-adopt.png` (leave the box off).  
6. **JobHold** — one green Move (disposable or a stack you intend to move): `docker-migrate-jobhold.png`. Optional fail + **Start source stack**.  
7. **Path map** — recapture `dns-logical.png` only if same-host NPM is not already obvious.  
8. **Demo Files** — `demo-files.png` on the public demo (or lab with demo mode), then leave lab Files flag as it was.  
9. Wire new filenames into the wiki pages in the table · `mkdocs build --strict` · commit PNGs + captions together.

Maintainer freeze clicks (not wiki): [QA_v1.4.0.md](../../../docs/QA_v1.4.0.md). Cap (not this pack): ACME-in-herder · M-live · full NPM CRUD · auto-rollback.

---

## v1.3.0 — pack status

**Landed 2026-08-22.** New PNGs are wired into the wiki pages below. Optional rows (`host-files-mobile.png`, `console-connect-as.png`) stay deferred — captions do not claim those files exist.

Existing **1.2** PNGs stay valid unless a row below says recapture. Do **not** redo certs / maps / LAN / templates unless chrome actually drifted.

### Landed 1.3 surfaces (do not recapture for 1.4 unless chrome drifted)

| Pri | Suggested file | Surface | Must show | Wire into |
|-----|----------------|---------|-----------|-----------|
| **P0** | `settings-hub.png` | Settings → **General** | Hub **cards** (Security, Console, Files, SSO, Cleanup) + timezone on the page — not one giant scroll | [Settings](../../operations/settings.md) |
| **P0** | `settings-security.png` | General → Security **Edit** | Password rules + force-2FA / grace. **Redact** nothing secret here; no backup codes | [Settings](../../operations/settings.md) · [2FA](../../account-security/two-factor.md) |
| **P0** | `settings-console.png` | General → Console **Edit** | Idle / slots + **who may elevate** + command-audit off (default). Flag may be off in the shot | [Settings](../../operations/settings.md#console) · [Web SSH](../../day-to-day/web-ssh-console.md) |
| **P0** | `settings-files.png` | General → Files **Edit** | Transfer cap (512 MiB default). Kill switch is env — card can say off | [Settings](../../operations/settings.md) · [Host Files](../../day-to-day/host-files.md) |
| **P0** | `reports.png` | `/reports` | Hero + 7/30/90 + at least Backups tab (empty-state OK) | [Reports](../../day-to-day/reports.md) |
| **P0** | `host-files.png` | `/servers/{id}/files` | Real **fleet nav**; ops **hero**; **Limited access**; path **no `//`**, green slashes; list in the pane. Flag **on** for this shot only | [Host Files](../../day-to-day/host-files.md) |
| **P0** | `server-detail.png` | Host overview | Recapture: **Files** button next to Console (Console only if that flag is on) | [Dashboard](../../day-to-day/dashboard-and-services.md) |
| **P1** | `host-files-mobile.png` | Files on a **phone** | **Maximize** (hero hidden); Folders slide-out; long-press not required in frame | [Host Files](../../day-to-day/host-files.md) |
| **P1** | `host-files-preview.png` | Image preview | ‹ › + loading overlay if you can catch it; otherwise ‹ › on a PNG | [Host Files](../../day-to-day/host-files.md) |
| **P1** | `ssh-access.png` | SSH access | Recapture: **fleet** identity + optional **privileged** card (empty is honest) | [Add server](../../day-to-day/add-server.md) |
| **P1** | `server-list.png` | Servers | Recapture: pager + search `q` | [Dashboard](../../day-to-day/dashboard-and-services.md) |
| **P1** | `settings-alerts.png` | Settings → Alerts | Recapture: **Alert policy** card + Edit (severity / mute) | [Alerts](../../operations/alerts-email-webhooks.md) |
| **P2** | `host-files-editor.png` | Files editor | Gutter + Wrap + Save — any small YAML | [Host Files](../../day-to-day/host-files.md) |
| **P2** | `console-connect-as.png` | Console | **Connect as…** / Privileged (flag on, 2FA). Optional | [Web SSH](../../day-to-day/web-ssh-console.md) |

### Must recapture — chrome changed in 1.3

| Pri | File(s) | 1.3 chrome to show |
|-----|---------|---------------------|
| **P0** | `server-detail.png` | **Files** button (dest-card is gone) |
| **P1** | `ssh-access.png` | Fleet + privileged identities |
| **P1** | `server-list.png` | Page size + `q` |
| **P1** | `settings-alerts.png` | Policy hub card |
| **P2** | `dashboard.png` | Optional if Reports appears in the header |

### Spot-check only (1.2 pack still good)

| File | Why skip unless broken |
|------|------------------------|
| 1.2 identity pack (`login-sso`, `settings-sso`, `account-sso`, `account-passkeys`, `console-popup`, `settings-self-backup`) | Landed 2026-08-18 |
| Certs (`certificates-*.png`) | Unchanged story |
| Maps (`dns-*.png`) | Unchanged unless you want alert-severity chrome |
| LAN (`nmap-*.png`) | Unchanged |
| Integrations / templates | Unchanged |
| `settings-api.png`, `settings-status.png`, `settings-stale-cleanup.png` | Hub wrap only; recapture cleanup if the card looks wrong |
| `backups-page.png`, `jobs-page.png` | Unchanged |
| `account-push.png`, `account-favourites.png`, `nav-host-jump.png` | Unchanged |
| HAOS shots | Spot-check |
| `docker-*.png` | Recapture inventory only if pager chrome is in frame |

### v1.2.0 pack (closed)

Landed 2026-08-18. Do not reopen unless a 1.3 recapture row above names the file. Historical capture notes: git history of this README at `v1.2.0`.

---

## Default convention

| Default | Value |
|---------|--------|
| Theme | **Light** |
| Viewport | **Desktop** (~1400–1600px wide) |
| Variants | Only when the UI **story** changes |

Optional extras (not a full matrix):

| Suffix | Use |
|--------|-----|
| `*-dark.png` | One showcase (e.g. dashboard) |
| `*-mobile.png` | Layout differs (console soft keys, Hosts map, coverage) |

Do **not** capture every page in light×dark×mobile. See [Appearance](../../getting-started/appearance.md).

---

## Pre-capture checklist (operator)

1. Rebuild/restart **web** so templates match **`v1.4.0-dev`**. About / footer stay **1.3.0** until freeze bump.  
2. Light theme · desktop width · redact hostnames/IPs if needed.  
3. **Move** shots: `PIHERDER_SERVICE_MIGRATE=true`, then recreate **web**. Do not photograph `.env` bodies, PEMs, or NPM passwords.  
4. **1.3 Files** recapture only: `PIHERDER_HOST_FILES=true` for those shots, then restore **off**.  
5. **Console / privileged:** flag on only for Connect-as shots.  
6. After saving PNGs: add `![…]` on the wiki pages in the **v1.4** table · `mkdocs build --strict` · commit binaries + captions together.

**Do not include in frames:** client secrets, SMTP passwords, API tokens, PEM material, backup codes, live `database.dump` paths you would not publish.

---

## Expected chrome (do not document old UI)

### Carried from 1.0 / 1.1 (still required if you recapture those pages)

| Surface | Must show in PNGs |
|---------|-------------------|
| **Servers list** | No footer “Status from last update checks…” line |
| **Server detail** | Dest cards; **Network path** + **LAN discovery** side-by-side |
| **Certs** | Deploy-target language; top **Deploy**; wizard or compact target list |
| **Hosts map** | Kind icons; progressive ports if capturing expand |
| **Account 2FA** | Backup codes via **modal** (not query string); trusted device type + last IP + ✎ |
| **Footer (signed out)** | Brand only — **no** version until signed in |
| **Settings → Alerts / API** | SMTP + webhook; Try a token / docs links |

### New / changed in 1.2

| Surface | Must show in PNGs |
|---------|-------------------|
| **Login** | **Continue with {display name}** when SSO is enabled |
| **Settings → General** | **SSO / OpenID Connect** card (issuer, map, redirect URI) |
| **Account** | **Passkeys** list; **Connected accounts (SSO)** |
| **2FA step-up** | **Use passkey** after password **or** SSO (not passwordless) |
| **Console** | Popup or `/console`; step-up then **+ Shell**; mobile soft-key **row** if capturing phone |
| **PiHerder backup** | **Full DR** (`pg_dump`) vs Config only |
| **Add server** | Default SSH user **`pi`** |
| **SSH access** | **Pinned host key** + reset |

### New / changed in 1.3

| Surface | Must show in PNGs |
|---------|-------------------|
| **Settings hub** | Cards + Edit modal (not a single scroll of policy) |
| **Security policy** | Password rules + 2FA / grace |
| **Console settings** | Idle / slots / who may elevate / command audit (default off) |
| **Files settings** | Transfer cap |
| **Reports** | `/reports` history (not Grafana) |
| **Host Files** | Button on host overview; explorer with fleet nav; **Limited access** / **Elevated access**; green path slashes; Maximize on phone |
| **Servers list** | Pager + search |

---

## Inventory — all PNGs

### 1.3 new (add file + wiki `![…]` when captured)

| File | Page / topic | Capture notes |
|------|----------------|---------------|
| `settings-hub.png` | Settings General | Cards |
| `settings-security.png` | Security modal | Password / 2FA policy |
| `settings-console.png` | Console modal | Limits + elevate + audit |
| `settings-files.png` | Files modal | Cap |
| `reports.png` | Reports | 7/30/90 + a tab |
| `host-files.png` | Files explorer | Hero + Limited access + nav |
| `host-files-mobile.png` | Files phone | Maximize |
| `host-files-preview.png` | Preview | ‹ › |
| `host-files-editor.png` *(optional)* | Editor | Gutter |
| `console-connect-as.png` *(optional)* | Console | Privileged |

### 1.2 new (add file + wiki `![…]` when captured)

| File | Page / topic | Capture notes |
|------|----------------|---------------|
| `login-sso.png` | Sign in | SSO button + password form |
| `login-2fa-passkey.png` | 2FA step-up | Use passkey + TOTP |
| `login-require-sso.png` *(optional)* | Require SSO | Password form hidden |
| `settings-sso.png` | Settings → General | SSO card; redact secret |
| `settings-self-backup.png` | Settings → PiHerder backup | Full DR |
| `account-passkeys.png` | Account → Passkeys | Named key + Add |
| `account-sso.png` | Account → Connected accounts | Link state |
| `console-popup.png` | Console popup | Unlocked PTY |
| `console-mobile.png` | Console phone | Soft keys |
| `demo-banner.png` *(optional)* | Public demo | Viewer banner only |

### Core fleet

| File | Page / topic | Capture notes |
|------|----------------|---------------|
| `dashboard.png` | Home | Recapture if 1.2 chrome / compiled CSS |
| `dashboard-dark.png` | Home dark showcase | Recapture with light pair |
| `server-list.png` | Servers | Spot-check; Console ⋯ only if flag on |
| `server-detail.png` | Server detail | Recapture if Console CTA or 1.2 tabs show |
| `server-detail-haos.png` | HAOS host | Spot-check |
| `system-info-haos.png` | System info modal | Spot-check |
| `ssh-access.png` | SSH access | **Recapture:** pinned host key + reset |
| `add-server-wizard.png` | Add server | **Recapture:** default user **`pi`** |
| `add-server-wizard-done.png` | Wizard done | Spot-check `pi@host` |
| `backups-page.png` | Backups | Spot-check |
| `jobs-page.png` | Jobs | Spot-check |

### Docker & templates

| File | Page / topic | Capture notes |
|------|----------------|---------------|
| `docker-project-lifecycle.png` | Project ⋯ lifecycle | Spot-check |
| `templates-catalog.png` | Catalog → Templates | Spot-check |
| `templates-deploy.png` | Deploy wizard | Spot-check |
| `templates-deployment.png` | Deployment detail | Spot-check |
| `templates-from-host.png` *(optional)* | From host | Spot-check |

### Catalog / network / discovery

| File | Page / topic | Capture notes |
|------|----------------|---------------|
| `dns-hub.png` | `/dns` hub | Spot-check |
| `dns-physical.png` | Hosts map | Spot-check |
| `dns-physical-mobile.png` | Hosts map phone | Optional |
| `dns-logical.png` | Path map | Optional recapture if showing direct-TLS / **Use project** |
| `dns-stack-panel.png` | Stack panel | Spot-check |
| `dns-coverage.png` | Kuma coverage | Spot-check |
| `dns-host-ports-expand.png` | Hosts map ports | Spot-check |
| `nmap-overview.png` | LAN Overview | Spot-check |
| `nmap-devices.png` | Devices List | Spot-check |
| `nmap-network.png` | Devices Map | Spot-check |
| `nmap-schedules.png` | Schedules | Spot-check |
| `nmap-runs.png` | Runs | Spot-check |
| `nmap-server-embed.png` | Server LAN embed | Spot-check |

### Integrations & certs

| File | Page / topic | Capture notes |
|------|----------------|---------------|
| `integrations-kuma.png` | Kuma detail | Spot-check |
| `integrations-grafana.png` | Grafana | Spot-check |
| `integrations-pihole.png` | Pi-hole | Spot-check |
| `integrations-npm.png` | NPM | Spot-check |
| `integrations-generic.png` | Generic URL | Spot-check |
| `certificates-list.png` | Certs list | Spot-check |
| `certificates-setup.png` | Setup guide | Spot-check |
| `certificates-detail.png` | Cert detail | Spot-check |
| `certificates-deploy-wizard.png` | Wizard open | Spot-check |
| `certificates-edge-map.png` | Edge map card | Spot-check |
| `services-fleet.png` | `/services` | Spot-check |

### Settings & account (1.1 + 1.2)

| File | Page / topic | Capture notes |
|------|----------------|---------------|
| `settings-status.png` | Status tab | Spot-check |
| `settings-stale-cleanup.png` | Stale data cleanup | Spot-check |
| `settings-alerts.png` | Alerts | Spot-check |
| `settings-api.png` | API | Spot-check |
| `settings-sso.png` | **New 1.2** — SSO | See must-capture |
| `settings-self-backup.png` | **New 1.2** — Full DR | See must-capture |
| `account-push.png` | PWA / push | Spot-check |
| `account-2fa.png` | TOTP + trusted devices | Recapture or pair with `account-passkeys.png` |
| `account-passkeys.png` | **New 1.2** — Passkeys | See must-capture |
| `account-sso.png` | **New 1.2** — Connected accounts | See must-capture |
| `account-favourites.png` | ★ menu | Spot-check |
| `nav-host-jump.png` | Jump host | Spot-check |

### Optional residual

| File | Notes |
|------|--------|
| `ha-update-modal.png` | HA apply dialog |
| `jobs-live-log.png` | JobHold live log dedicated shot |
| `docker-logs-modal.png` | Logs modal with **All services** selected |
| `docker-build.png` *(optional 1.2)* | Compose **Build** stream — only if you want R3 in the wiki |

---

## Capture sequence (v1.3 freeze)

1. **Settings hub** — `settings-hub.png` then Security / Console / Files modals  
2. **Reports** — `/reports`  
3. **Host overview** — recapture `server-detail.png` (Files button)  
4. **Files** — flag on: desktop explorer, optional phone Maximize + preview; then restore **`PIHERDER_HOST_FILES=false`**  
5. **Lists / SSH** — pager on Servers; fleet + privileged on SSH access  
6. **Alerts** — policy card if chrome drifted  
7. **1.2 pack** — only if a caption/PNG disagree  

Maintainer freeze clicks (not wiki): [QA_v1.3.0.md](https://github.com/bjorngluck/piherder/blob/v1.3.0-dev/docs/QA_v1.3.0.md) (historical). Current train: [QA_v1.4.0.md](../../../docs/QA_v1.4.0.md). Cap (not this pack): ACME · M-live · full NPM CRUD.

---

## How to land screenshots

**Best practice: local git → commit → push** (binaries + markdown).

```bash
git checkout v1.4.0-dev && git pull
# optional: git checkout -b docs/screenshots-1.4

python3 -m venv .venv-docs && source .venv-docs/bin/activate
pip install -r requirements-docs.txt
mkdocs serve   # http://127.0.0.1:8000

# Capture UI → save into wiki/assets/screenshots/
# Wire any *new* filenames into wiki pages (table above)
# Update captions only if the story changed

mkdocs build --strict
git add wiki/assets/screenshots/*.png wiki/**/*.md
git commit -m "docs(wiki): screenshot pack for v1.4.0"
git push
```

After merge to the docs deploy branch / tag, hard-refresh the live site.

### Checklist before commit

- [ ] PNG names match Markdown references  
- [ ] New 1.4 files have a wiki `![…]` (or stay unlinked only if still WIP)  
- [ ] Light desktop for defaults; dark/mobile only where planned  
- [ ] Sensitive hostnames/IPs redacted if needed  
- [ ] No real SMTP passwords, API tokens, client secrets, backup codes, or PEM material in frames  
- [ ] `mkdocs build --strict` passes  
- [ ] Captions no longer say “recapture in progress” for files you just replaced  

Full style guide: [Contributing docs](../../developers/contributing-docs.md).
