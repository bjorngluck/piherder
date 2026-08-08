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
| **v1.1.0** (freeze) | **Recapture priority pack below** — [RELEASE_v1.1.0.md](../../../docs/RELEASE_v1.1.0.md) · [PLAN_v1.1.0.md](../../../docs/PLAN_v1.1.0.md) |

**Owner:** operator fleet testing (not CI). Replace PNGs in this directory; captions note when a figure may lag. After dropping files: `mkdocs build --strict`.

!!! tip "Production / freeze screenshots (v1.1)"
    Capture from a rebuild of **`v1.1.0-dev`** (or image/tag **`v1.1.0`** once tagged): `docker compose build web && docker compose up -d web`.  
    App code is **not** bind-mounted in prod compose — stale containers = stale chrome in PNGs.

---

## v1.1.0 — what to update

Use this section while freezing **v1.1.0**. Mark rows done locally as you replace files.

### Must recapture (chrome changed in 1.1)

| Priority | File(s) | 1.1 chrome to show |
|----------|---------|---------------------|
| **P0** | `certificates-list.png` | Deploy targets / chips; list CTAs after rename |
| **P0** | `certificates-detail.png` | Compact deploy-target list + **Deploy** top chrome; detail modal if open |
| **P0** | `certificates-setup.png` | Setup / known-edges story if still linked; wizard-oriented copy |
| **P0** | `certificates-edge-map.png` | Edge map card (if still featured) |
| **P0** | `dns-physical.png` | **Kind icons** on nodes; optional locked **focus pop-out** (~1.30×); if possible **host ports compact** callout or mid expand |
| **P0** | `dns-logical.png` | Path map still correct; prefer NPM multi-path focus; app cards with icons if visible |
| **P0** | `dns-stack-panel.png` | **host→container** port chips; optional port **roles** |
| **P0** | `nmap-devices.png` | **Last seen**; filter chips (**Offline** / **Hidden**); Hide/Unhide if visible |
| **P0** | `nmap-network.png` / Devices Map | Same filter/offline story if chrome shared |
| **P0** | `account-2fa.png` | Trusted devices: **type**, **last IP**, ✎ rename (not always-open form) |
| **P1** | `dns-hub.png` | Hub cards still current (spot-check) |
| **P1** | `dns-coverage.png` | Spot-check density |
| **P1** | `dns-physical-mobile.png` | Hosts map phone; ports expand on touch if you can show it |
| **P1** | `server-detail.png` | Optional: **Jump host** / feature tabs / pin ★ if visible on overview |
| **P1** | `backups-page.png` | Human-readable schedule text next to cron; sources list |
| **P1** | `jobs-page.png` | Spot-check (job types unchanged) |
| **P1** | `services-fleet.png` | Generic URL / service chips if a Frigate·HA·n8n link is bound |
| **P1** | `nmap-overview.png`, `nmap-schedules.png`, `nmap-runs.png` | Spot-check; schedules show **cron_human** if present |
| **P1** | `integrations-npm.png` | Spot-check (certs dense cards still true) |
| **P2** | `dashboard.png`, `dashboard-dark.png` | Spot-check; optional ★ menu open in one shot |
| **P2** | Remainder of inventory | Only if caption/PNG disagree |

### New PNGs recommended for 1.1 (add file + wiki `![…]` when ready)

| Suggested file | Surface | Why |
|----------------|---------|-----|
| `certificates-deploy-wizard.png` | Cert deploy-target **wizard** open (mid-step) | Headline 1.1 cert story — list/detail alone under-sell wizard |
| `settings-alerts.png` | Settings → **Alerts** | SMTP + webhook (Wh-lite / H-lite) — no existing shot |
| `settings-api.png` | Settings → **API** | **Try a token** + OpenAPI/ReDoc links |
| `integrations-generic.png` | Generic URL detail (HA / Frigate / n8n / custom) | Int-gen — [generic-links](../../integrations/generic-links.md) |
| `dns-host-ports-expand.png` | Hosts map: ports expand (ports-only or by-service) | Map M4 progressive UX |
| `account-favourites.png` *(optional)* | Header ★ menu open | Favourites (J) |
| `nav-host-jump.png` *(optional)* | Docker/Backups with **Jump host** ▾ open | Host jump (K) |

Wire new files into the matching wiki page in the same commit as the PNG when possible.

### Can skip / low priority for 1.1 freeze

| File | Why skip unless broken |
|------|------------------------|
| `add-server-wizard*.png`, `ssh-access.png` | No major 1.1 chrome change |
| `docker-project-lifecycle.png`, `docker-logs-modal.png` | Unchanged story |
| `templates-*.png` | Templates stream not 1.1 headline |
| `ha-update-modal.png`, `system-info-haos.png`, `server-detail-haos.png` | Spot-check only if HAOS UI drifted |
| `settings-stale-cleanup.png`, `settings-status.png` | Unchanged unless Status tab copy drifted |
| `account-push.png` | Unchanged |
| Integration Kuma / Grafana / Pi-hole | Spot-check only |

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
| `*-mobile.png` | Layout differs (Hosts map, coverage, certs) |

Do **not** capture every page in light×dark×mobile. See [Appearance](../../getting-started/appearance.md).

---

## Pre-capture checklist (operator full QA)

1. Rebuild/restart **web** so templates + static JS/CSS match the branch under test (`v1.1.0-dev` / tag).  
2. Light theme · desktop width · redacted hostnames/IPs if needed.  
3. Prefer a host with **Network path + linked LAN discovery** for `server-detail.png` / `nmap-server-embed.png`.  
4. Prefer a path map with **via-NPM** routes so the NPM hub + connectors are visible (`dns-logical.png`).  
5. For **certs**: have at least one deploy target (pair or combined) so Deploy / verify chrome is real.  
6. For **maps**: discovery on + known kinds so **icons** render; expand one host’s ports if capturing `dns-host-ports-expand.png`.  
7. For **alerts/API**: SMTP or webhook fields filled with dummy values (no real secrets in PNGs).  
8. After saving PNGs: `mkdocs build --strict` · commit binaries + any caption tweaks together.

---

## Expected chrome (do not document old UI)

### Carried from 1.0 (still required)

| Surface | Must show in PNGs |
|---------|-------------------|
| **Servers list** | No footer “Status from last update checks…” line |
| **Server detail** | Dest cards; **Network path** + **LAN discovery** **side-by-side** (always open LAN card); **Open on hosts map** and **Path map** both secondary buttons |
| **LAN Overview** | **Scan now** / **Update vuln pack** open **in-app modals** (not page dump); detailed/deep confirm is an **in-app** dialog (not browser `confirm`) |
| **LAN Devices** | **List \| Map** (merged former Network tab); Offline filter chip |
| **LAN Schedules / Runs** | One dense list all widths; **no** run ID column |
| **Network hub** | Destination + DNS/settings **cards**; **By path type** (not “Path mix”); DNS records modal |
| **Path map** | List + Show/Hide map; selecting **NPM hub** highlights **all** proxied paths **and connector lines** |
| **Kuma coverage** | Dense **table** + constrained suggestion + green **Bind** (not mega-select cards) |
| **Docker containers** | Dense rows; project Logs → service picker includes **All services** |
| **Quick edit** | Compose (± Dockerfile) only; note that `.env` / sidecars are **full editor** |
| **Templates catalog** | **OOTB** / **Yours** badges (+ section groups when both kinds present) |
| **HAOS server** | HAOS chip; HA updates (not bare apt-only); System info Core/OS/Supervisor |
| **2FA Account** | Regenerate backup codes → **modal** (password + authenticator code) |
| **Footer (signed out)** | Brand only — **no** version number until signed in |

### New / changed in 1.1

| Surface | Must show in PNGs |
|---------|-------------------|
| **Certs** | **Deploy target** language (not “service map”); top **Deploy**; wizard or compact target list; optional verify result |
| **Hosts map** | **Canned kind icons**; focus **pop-out** when selected; progressive **ports** chrome if capturing expand |
| **Stack panel** | Published ports as **host→container** chips |
| **LAN Devices** | **Last seen**; **Hidden** filter; purge affordance only if you intentionally document it |
| **Account** | Trusted device **type + last IP**; rename behind **✎ Edit** |
| **Schedules** | Plain-English cron next to raw expression (backups / nmap / etc.) |
| **Nav** | Optional: header **★** pins; **Jump host** on feature pages |
| **Settings → Alerts** | SMTP + webhook blocks (if new PNG) |
| **Settings → API** | Try a token / docs links (if new PNG) |
| **Generic URL** | Preset kind + Test/Poll (if new PNG) |

---

## Inventory — all PNGs (full QA review)

Review **every** file over a full pass. For **v1.1 freeze**, prioritise the [Must recapture](#must-recapture-chrome-changed-in-11) table first; use P2 for the rest only if stale.

### Core fleet

| File | Page / topic | Capture notes |
|------|----------------|---------------|
| `dashboard.png` | Home | Fleet summary + attention; optional ★ menu |
| `dashboard-dark.png` | Home dark showcase | Optional but review |
| `server-list.png` | Servers | Bulk bar, attention badges, no old footer help |
| `server-detail.png` | Server detail | **Network path + LAN discovery** row; jump/tabs if showing 1.1 nav |
| `server-detail-haos.png` | HAOS host | HAOS chip, HA updates, no Docker fleet emphasis |
| `system-info-haos.png` | System info modal | Core / OS / Supervisor + disk |
| `ssh-access.png` | SSH access | Key deploy, deps; HAOS `ha` + rsync hints if useful |
| `backups-page.png` | Backups | Sources + path policy + **cron_human** |
| `jobs-page.png` | Jobs | Filters; optional job types visible |

### Docker & templates

| File | Page / topic | Capture notes |
|------|----------------|---------------|
| `docker-project-lifecycle.png` | Project ⋯ lifecycle | Stop/Start/Restart all confirm |
| `templates-catalog.png` | Catalog → Templates | **OOTB** / **Yours** |
| `templates-deploy.png` | Deploy wizard | Jobs / live log story if visible |
| `templates-deployment.png` | Deployment detail | Drift, redeploy, Accept host as desired |
| `templates-from-host.png` *(optional)* | From host | Additional files + host vars |

### Catalog / network / discovery

| File | Page / topic | Capture notes |
|------|----------------|---------------|
| `dns-hub.png` | `/dns` hub | Destination cards + DNS/settings cards + paths |
| `dns-physical.png` | Hosts map | **Icons** + dual layout if discovery on; optional focus pop-out |
| `dns-physical-mobile.png` | Hosts map phone | Optional; ports expand on touch |
| `dns-logical.png` | Path map | Prefer multi via-NPM; optional **NPM hub focused** |
| `dns-stack-panel.png` | Stack expand / panel | **host→container** ports + roles |
| `dns-coverage.png` | Kuma coverage | Dense table + Bind |
| `dns-host-ports-expand.png` *(new 1.1)* | Hosts map ports | Compact / ports-only / by-service |
| `nmap-overview.png` | LAN Overview | Modal CTAs, vuln strip |
| `nmap-devices.png` | Devices List | Last seen + Offline/Hidden filters |
| `nmap-network.png` | Devices Map | Subnet cards |
| `nmap-schedules.png` | Schedules | Dense list + **cron_human** |
| `nmap-runs.png` | Runs | Dense list, no ID column |
| `nmap-server-embed.png` | Server LAN embed | Side-by-side with Network path |

### Integrations & certs

| File | Page / topic | Capture notes |
|------|----------------|---------------|
| `integrations-kuma.png` | Kuma detail | Spot-check |
| `integrations-grafana.png` | Grafana | Spot-check |
| `integrations-pihole.png` | Pi-hole | Spot-check |
| `integrations-npm.png` | NPM | Spot-check |
| `integrations-generic.png` *(new 1.1)* | Generic URL | HA / Frigate / n8n / custom |
| `certificates-list.png` | Certs list | Deploy-target era chips / CTAs |
| `certificates-setup.png` | Setup guide | Landed / wizard-oriented |
| `certificates-detail.png` | Cert detail | Deploy targets, **Deploy**, modals |
| `certificates-deploy-wizard.png` *(new 1.1)* | Wizard open | Mid-step simulate / sudoers |
| `certificates-edge-map.png` | Edge map card | Spot-check |
| `services-fleet.png` | `/services` | Grid + optional generic bindings |

### Settings & account

| File | Page / topic | Capture notes |
|------|----------------|---------------|
| `settings-status.png` | Status tab | Stack health |
| `settings-stale-cleanup.png` | Stale data cleanup | Spot-check |
| `settings-alerts.png` *(new 1.1)* | Alerts | SMTP + webhook |
| `settings-api.png` *(new 1.1)* | API | Try a token + ReDoc |
| `account-push.png` | PWA / push | Spot-check |
| `account-2fa.png` | Account 2FA + trusted devices | Type, IP, ✎ rename; backup-codes modal optional |
| `account-favourites.png` *(optional new)* | ★ menu | Grouped pins |
| `nav-host-jump.png` *(optional new)* | Jump host | Feature page ▾ |

### Optional residual

| File | Notes |
|------|--------|
| `ha-update-modal.png` | HA apply dialog |
| `jobs-live-log.png` | JobHold live log dedicated shot |
| `docker-logs-modal.png` | Logs modal with **All services** selected |

---

## Full QA pass — suggested sequence (v1.1)

Use this order so related chrome is consistent across pages.

1. **Shell** — dashboard (light + optional dark); optional ★ menu open  
2. **Host** — Debian server detail (Network/LAN row, jump tabs), SSH access  
3. **HAOS** — only if you still maintain those PNGs  
4. **LAN** — Overview; Devices List (**last seen / filters**); Map; Schedules (**cron_human**); Runs  
5. **Network** — hub; Hosts map (**icons / pop-out / ports expand**); Path map; stack panel (**port chips**); coverage  
6. **Certs** — list, detail, **wizard**, setup, edge  
7. **Integrations** — generic URL new shot; spot-check Kuma/Grafana/Pi-hole/NPM  
8. **Docker / templates** — spot-check only unless broken  
9. **Ops** — Jobs, Backups (**cron_human**), Settings **Alerts** + **API** + status/stale  
10. **Account** — 2FA + trusted devices; optional favourites  

Next train (not 1.1 screenshot blockers): WebAuthn · SSO · webshell · demo mode — [PLAN_v1.2.0.md](../../../docs/PLAN_v1.2.0.md) · [RELEASE_v1.1.0.md](../../../docs/RELEASE_v1.1.0.md).

---

## How to land screenshots

**Best practice: local git → commit → push** (binaries + markdown).

```bash
git checkout v1.1.0-dev && git pull
# optional: git checkout -b docs/screenshots-1.1

python3 -m venv .venv-docs && source .venv-docs/bin/activate
pip install -r requirements-docs.txt
mkdocs serve   # http://127.0.0.1:8000

# Capture UI → save into wiki/assets/screenshots/
# Wire any *new* filenames into wiki pages
# Update captions only if the story changed

mkdocs build --strict
git add wiki/assets/screenshots/*.png wiki/**/*.md
git commit -m "docs(wiki): screenshot pack for v1.1.0"
git push
```

After merge to the docs deploy branch / tag, hard-refresh the live site.

### Checklist before commit

- [ ] PNG names match Markdown references  
- [ ] New 1.1 files have a wiki `![…]` (or stay unlinked only if still WIP)  
- [ ] Light desktop for defaults; dark/mobile only where planned  
- [ ] Sensitive hostnames/IPs redacted if needed  
- [ ] No real SMTP passwords, API tokens, or PEM material in frames  
- [ ] `mkdocs build --strict` passes  
- [ ] Captions no longer say “recapture in progress” for files you just replaced  

Full style guide: [Contributing docs](../../developers/contributing-docs.md).
