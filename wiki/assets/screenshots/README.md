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

**Owner:** operator fleet testing (not CI). Replace PNGs in this directory; captions note when a figure may lag. After dropping files: `mkdocs build --strict`.

!!! tip "Production screenshots"
    Capture from image/tag **`v1.0.0`** / `bjorngluck/piherder:1.0.0` (or rebuilt `main` matching that tag).

### v1.0 priority recapture (from PLAN §8.2)

| Priority | Files | 1.0 chrome to show |
|----------|-------|---------------------|
| **P0** | `dns-hub.png` | DNS records card; Host A / **CNAME** / External filters + legend |
| **P0** | `dns-coverage.png` | Dense table desktop; optional mobile cards; Mute/Unmute chips |
| **P0** | `certificates-setup.png` | Known-edges / distribute discovery card |
| **P0** | `integrations-npm.png` | Certs as dense cards; plain “Pull into PiHerder” |
| **P0** | `account-2fa.png` | 2FA section, step-up backup codes modal, trusted-device copy |
| **P1** | `dns-logical.png`, `dns-physical.png` | Focus / clear-focus still correct |
| **P1** | `dashboard.png` | Signed-in fleet only (no anonymous empty dashboard) |
| **P2** | Remainder | Only if caption/PNG disagree |

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

1. Rebuild/restart **web** so templates + static JS/CSS match `main` (app is not bind-mounted in prod compose).  
2. Light theme · desktop width · redacted hostnames/IPs if needed.  
3. Prefer a host with **Network path + linked LAN discovery** for `server-detail.png` / `nmap-server-embed.png`.  
4. Prefer a path map with **via-NPM** routes so the NPM hub + connectors are visible (`dns-logical.png`).  
5. After saving PNGs: `mkdocs build --strict` · commit binaries + any caption tweaks together.

---

## Expected chrome (do not document old UI)

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

---

## Inventory — all PNGs (full QA review)

Review **every** file. Priority is a suggested order for the full pass; mark done locally as you replace.

### Core fleet (P0)

| File | Page / topic | Capture notes |
|------|----------------|---------------|
| `dashboard.png` | Home | Fleet summary + attention |
| `dashboard-dark.png` | Home dark showcase | Optional but review |
| `server-list.png` | Servers | Bulk bar, attention badges, no old footer help |
| `server-detail.png` | Server detail | **Network path + LAN discovery** row; Debian/Linux host |
| `server-detail-haos.png` | HAOS host | HAOS chip, HA updates, no Docker fleet emphasis |
| `system-info-haos.png` | System info modal | Core / OS / Supervisor + disk |
| `ssh-access.png` | SSH access | Key deploy, deps; HAOS `ha` + rsync hints if useful |
| `backups-page.png` | Backups | Sources + path policy |
| `jobs-page.png` | Jobs | Filters; optional job types visible |

### Docker & templates (P0–P1)

| File | Page / topic | Capture notes |
|------|----------------|---------------|
| `docker-project-lifecycle.png` | Project ⋯ lifecycle | Stop/Start/Restart all confirm |
| `templates-catalog.png` | Catalog → Templates | **OOTB** / **Yours** |
| `templates-deploy.png` | Deploy wizard | Jobs / live log story if visible |
| `templates-deployment.png` | Deployment detail | Drift, redeploy, Accept host as desired |
| `templates-from-host.png` *(optional new)* | From host | Additional files + host vars |

### Catalog / network / discovery (P0–P1)

| File | Page / topic | Capture notes |
|------|----------------|---------------|
| `dns-hub.png` | `/dns` hub | Destination cards + DNS/settings cards + paths |
| `dns-physical.png` | Hosts map | Radar / dual layout if discovery on |
| `dns-physical-mobile.png` | Hosts map phone | Optional |
| `dns-logical.png` | Path map | Prefer multi via-NPM; optional **NPM hub focused** (connectors lit) |
| `dns-stack-panel.png` | Stack expand / panel | Runtime topology |
| `dns-coverage.png` | Kuma coverage | Dense table + Bind |
| `nmap-overview.png` | LAN Overview | Modal CTAs, vuln strip |
| `nmap-devices.png` | Devices List | List \| Map chrome |
| `nmap-network.png` | Devices Map | Subnet cards |
| `nmap-schedules.png` | Schedules | Dense list + modal |
| `nmap-runs.png` | Runs | Dense list, no ID column |
| `nmap-server-embed.png` | Server LAN embed | Side-by-side with Network path |

### Integrations & certs (P1)

| File | Page / topic | Capture notes |
|------|----------------|---------------|
| `integrations-kuma.png` | Kuma detail | Spot-check |
| `integrations-grafana.png` | Grafana | Spot-check |
| `integrations-pihole.png` | Pi-hole | Spot-check |
| `integrations-npm.png` | NPM | Spot-check |
| `certificates-list.png` | Certs list | Setup CTA / chips |
| `certificates-setup.png` | Setup guide | Landed |
| `certificates-detail.png` | Cert detail | Maps, presets |
| `certificates-edge-map.png` | Edge map card | Landed |
| `services-fleet.png` | `/services` | Grid |

### Settings & account (P1–P2)

| File | Page / topic | Capture notes |
|------|----------------|---------------|
| `settings-status.png` | Status tab | Stack health |
| `settings-stale-cleanup.png` | Stale data cleanup | Landed |
| `account-push.png` | PWA / push | Spot-check |
| `account-2fa.png` *(optional new)* | Account 2FA | Backup-codes regenerate **modal** open |

### Optional residual

| File | Notes |
|------|--------|
| `ha-update-modal.png` | HA apply dialog |
| `jobs-live-log.png` | JobHold live log dedicated shot |
| `docker-logs-modal.png` | Logs modal with **All services** selected |

---

## Full QA pass — suggested sequence

Use this order so related chrome is consistent across pages.

1. **Shell** — dashboard (light + optional dark), server list  
2. **Host** — Debian server detail (Network/LAN row), SSH access  
3. **HAOS** — HAOS detail + System info (+ optional update modal)  
4. **LAN** — Overview (open Scan now modal once for chrome), Devices List+Map, Schedules, Runs  
5. **Network** — hub, Hosts map, Path map (NPM focus), stack panel, coverage  
6. **Docker** — lifecycle shot; optional logs All services  
7. **Templates** — catalog OOTB/Yours, deploy, deployment  
8. **Integrations / certs** — Kuma, Grafana, Pi-hole, NPM, cert list/detail/setup/edge  
9. **Ops** — Jobs, Backups, Settings status + stale cleanup  
10. **Account** — push; optional 2FA backup-codes modal  

Residuals for **post-1.0** (not screenshot blockers): full cert distribute wizard, email password reset — [PLAN_v1.0.0.md](../../../docs/PLAN_v1.0.0.md) · [RELEASE_v1.0.0.md](../../../docs/RELEASE_v1.0.0.md).

---

## How to land screenshots

**Best practice: local git → commit → push** (binaries + markdown).

```bash
git checkout main && git pull
# optional: git checkout -b docs/screenshots-0.9

python3 -m venv .venv-docs && source .venv-docs/bin/activate
pip install -r requirements-docs.txt
mkdocs serve   # http://127.0.0.1:8000

# Capture UI → save into wiki/assets/screenshots/
# Update captions only if the story changed

mkdocs build --strict
git add wiki/assets/screenshots/*.png wiki/**/*.md
git commit -m "docs(wiki): screenshot pack for 0.9 full QA"
git push
```

After merge to **`main`**, Docs Actions deploys GitHub Pages. Hard-refresh the live site.

### Checklist before commit

- [ ] PNG names match Markdown references  
- [ ] Light desktop for defaults; dark/mobile only where planned  
- [ ] Sensitive hostnames/IPs redacted if needed  
- [ ] `mkdocs build --strict` passes  
- [ ] Captions no longer say “recapture in progress” for files you just replaced  

Full style guide: [Contributing docs](../../developers/contributing-docs.md).
