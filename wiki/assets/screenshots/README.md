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
| **v1.4.0** | **Tagged** — pack **landed 2026-09-06**. Maintainer QA: [QA_v1.4.0.md](../../../docs/QA_v1.4.0.md). Theme: [Move a service](../../docker/service-migration.md) · [RELEASE](../../../docs/RELEASE_v1.4.0.md) |
| **v1.5.0** | **Tagged** 2026-09-18 — reuse Move JobHold pack; Reports pin/hide + Move jobs card recapture if chrome drifted. [RELEASE](../../../docs/RELEASE_v1.5.0.md) · [QA](../../../docs/QA_v1.5.0.md) |
| **v1.6.0** | **Captured** 2026-09-25 and wired into the wiki. [§ v1.6](#v160--pack-status). [RELEASE](../../../docs/RELEASE_v1.6.0.md) · [QA](../../../docs/QA_v1.6.0.md) |

**Owner:** operator fleet testing (not CI). Replace PNGs in this directory; captions note when a figure may lag. After dropping files: `mkdocs build --strict`.

!!! tip "Capture from the freeze branch"
    Production pack: rebuild **`v1.5.0`**: `docker compose build web celery-worker && docker compose up -d`.  
    App code is **not** bind-mounted — stale containers = stale chrome.  
    About / footer **1.5.0**.  
    Move wizard shots need `PIHERDER_SERVICE_MIGRATE=true` then recreate **web**.  
    **v1.6 captures** are in the tree (footer **1.6.0** after the version bump). See [§ v1.6](#v160--pack-status).

---

## v1.6.0 — pack status {#v160--pack-status}

**Captured** 2026-09-25. Figures are on [Web SSH](../../day-to-day/web-ssh-console.md), [Home Assistant](../../integrations/home-assistant.md), [System Info](../../day-to-day/system-info.md), and [Move a service](../../docker/service-migration.md). 1.4 and 1.5 PNGs stay unless a row below says replace.

About / footer on these shots may still read **1.5.0** if they were taken before the package bump. Light theme, desktop width. Mask tokens. No PEMs, backup codes, or SMTP passwords.

### New files (save these)

| Pri | File | Surface | Must show | Wire into (after the file exists) |
|-----|------|---------|-----------|-------------------------------------|
| **P0** | `console-mux-features.png` | Server **Edit → Features** on a Debian/Pi | **Console mux (tmux / screen)** checkbox, default off is fine. Do not shoot HAOS (the box is hidden there) | [Web SSH](../../day-to-day/web-ssh-console.md) · [Add a server](../../day-to-day/add-server.md) |
| **P0** | `console-mux-session.png` | Web SSH, that host’s mux **on**, `tmux` or `screen` present | Banner/note that the shell attached with mux (`mux tmux` or screen). Shell can be just a prompt | [Web SSH](../../day-to-day/web-ssh-console.md) |
| **P0** | `ha-hacs-config.png` | HA config flow for PiHerder | Base URL filled. Token **masked** | [Home Assistant → PiHerder](../../integrations/home-assistant.md) |
| **P0** | `ha-fleet-card.png` | Lovelace card `custom:piherder-dashboard-card` | Header: PiHerder logo + name. Fleet totals (hosts, CPU, containers, memory, disk). One host **expanded**. Chips: Host, Docker, Backups, Alerts, Audit | [Home Assistant → PiHerder](../../integrations/home-assistant.md) |
| **P0** | `ha-fleet-sensors.png` | HA **Devices** | Fleet device (counts, Plugin **0.3.0** if visible) and one host device | [Home Assistant → PiHerder](../../integrations/home-assistant.md) |
| **P1** | `ha-visit-host.png` | Browser after the host device **Visit** | Address bar on `{origin}/servers/{id}` (the herder host page, not HA history) | [Home Assistant → PiHerder](../../integrations/home-assistant.md) |
| **P1** | `ha-slice1b-sensors.png` | Same host device, sensors list | At least one container sensor and disk % (service up/down if a monitor exists). No start/stop button | [Home Assistant → PiHerder](../../integrations/home-assistant.md) |
| **P1** | `system-info-snapshot.png` | Herder **System Info** modal | Stored pretty OS, CPU cores/load, memory, disk. The **refresh icon** is in the modal header (no second button on the host page) | [System Info](../../day-to-day/system-info.md) |
| **P1** | `docker-migrate-jobhold-undo.png` | JobHold after a Move failed at cutover / rebind / validate | Button **Undo** or **Confirm undo**, plus a bit of the preview log. Disposable stack only | [Move a service](../../docker/service-migration.md) · [Jobs](../../day-to-day/jobs-audit-notifications.md) |

### Do not recapture for 1.6

| File | Why leave it |
|------|----------------|
| `console-popup.png` | 1.5 PTY chrome. Mux gets its **own** file so this shot stays the plain console |
| `docker-migrate-jobhold.png` | Green Move. Undo is a **separate** file |
| `docker-migrate-jobhold-start-source.png` | Pre-flip **Start source stack** is unchanged |
| `docker-migrate-wizard.png`, `docker-migrate-preflight.png`, `docker-host-lock.png` | Move wizard chrome unchanged |
| `system-info-haos.png` | Path 1 HAOS modal. The new shot is the snapshot modal on a normal host |
| `reports.png`, `server-detail.png`, `server-list.png`, `jobs-page.png` | Unchanged unless a 1.6 control is actually in that frame and looks wrong |
| `demo-files.png` | Demo Files story unchanged. CSP is a response header, not a screen |
| Login / settings / certs / maps / LAN | Unchanged |

### Capture sequence (while you walk QA)

1. **Mux off or on, Features** — `console-mux-features.png` before you hide the checkbox story.  
2. **Mux on + shell** — `console-mux-session.png`. Then finish the Hide / ✕ checks; you do not need a second shot of an empty `tmux ls`.  
3. **System Info** — refresh icon once so CPU/memory are filled, then `system-info-snapshot.png`.  
4. **HA** — config flow (`ha-hacs-config.png`, token masked) → devices (`ha-fleet-sensors.png`) → Visit (`ha-visit-host.png`) → card (`ha-fleet-card.png`) → host sensors (`ha-slice1b-sensors.png`).  
5. **Undo** — only if you stage a post-flip failure: `docker-migrate-jobhold-undo.png`. Do not overwrite `docker-migrate-jobhold.png`.  
6. Drop the PNGs here, add the `![…]` figures on the pages in the table, `.venv-docs/bin/mkdocs build --strict`, commit PNGs and captions together.

---

## v1.4.0 — pack status {#v140--pack-status}

**Landed 2026-09-06.** Wired into [Move a service](../../docker/service-migration.md), [Docker](../../docker/overview.md), [Host Files](../../day-to-day/host-files.md), [Public demo](../../operations/demo-site.md), stack panel on [Network maps](../../integrations/dns-fabric.md). 1.3 PNGs stay valid unless a row below says recapture.

### New 1.4 surfaces (landed)

| Pri | Suggested file | Surface | Must show | Wire into |
|-----|----------------|---------|-----------|-----------|
| **P0** | `docker-migrate-wizard.png` | Docker ⋯ **Move to another host…** | Hero **Move \<project\>**; dest select **To (Docker hosts only)**; HAOS and source absent | [Move a service](../../docker/service-migration.md) |
| **P0** | `docker-migrate-preflight.png` | Same wizard after dest pick | **Ready for copy**; dest folder path; leftover radios (**Leave stopped** checked); **Move service**. Wait overlay not required in frame | [Move a service](../../docker/service-migration.md) |
| **P0** | `docker-host-lock.png` | Locked project ⋯ | **Unlock…**; **Move to another host…** disabled | [Move a service](../../docker/service-migration.md) · [Docker](../../docker/overview.md) |
| **P0** | `docker-migrate-jobhold.png` | JobHold after Move | Title **Move \<project\>**; live log; **Succeeded** or **Failed** until **Close** (closeMode hold) | [Move a service](../../docker/service-migration.md) · [Jobs](../../day-to-day/jobs-audit-notifications.md) |
| **P1** | `docker-migrate-preflight-adopt.png` | Preflight on an NPM-only app (no fabric DNS row) | **Adopt into fabric** checkbox **unchecked**; warn that Move will PUT `forward_host` | [Move a service](../../docker/service-migration.md) · [NPM](../../integrations/npm.md) |
| **P1** | `docker-migrate-jobhold-start-source.png` | JobHold after copy or dest-up **fail** | **Start source stack** visible. Disposable stack only — or skip if you cannot stage a fail | [Move a service](../../docker/service-migration.md) |
| **P1** | `demo-files.png` | Public demo **Files** | Simulated / canned banner; folder list; no SFTP. Viewer account OK | [Host Files](../../day-to-day/host-files.md) · [Demo](../../operations/demo-site.md) |

### Recaptured — chrome changed in 1.4

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

1. Rebuild **web** on `v1.4.0`; flag **on**.  
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

1. Rebuild/restart **web** so templates match **`v1.4.0`**. About / footer **1.4.0**.  
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

### New / changed in 1.6 (files not in the tree until captured)

| Surface | Must show in PNGs |
|---------|-------------------|
| **Edit → Features** | **Console mux** on a Debian/Pi (hidden on HAOS — do not use HAOS for this shot) |
| **Web SSH** | Mux-attached banner (`tmux` or `screen`), separate from `console-popup.png` |
| **HA config flow** | Base URL; token masked |
| **HA fleet card** | Totals, expanded host, section chips |
| **HA devices** | Fleet device + host device; Visit lands on `/servers/{id}` |
| **HA host sensors** | Container / service / disk from snapshots; no start/stop |
| **System Info** | Stored CPU, memory, disk; refresh is the modal header icon |
| **Move JobHold** | Undo is its own file. Do not replace the green Move shot |

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
git checkout v1.5.0-dev && git pull
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
