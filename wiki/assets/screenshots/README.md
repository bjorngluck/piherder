# Screenshots

Real UI captures live here. Wiki pages reference them like:

```markdown
![Dashboard](../assets/screenshots/dashboard.png)
```

Wireframe SVGs (`*.svg`) are legacy placeholders; wiki pages now use real PNGs. You can delete unused SVGs once you are sure no external link points at them.

## Release policy

| Release | Screenshot bar |
|---------|----------------|
| **v0.6.0** (released) | **Prose only** — no PNG gate. Existing captures stay until refreshed. |
| **v0.7.0** (tagged) | Prose for wizard + compose sets; **PNG pack deferred** to 0.8 |
| **v0.8.0 RC3** (tagged) | **Full capture pack landed** + prose audit + **LAN Discovery** UI shots — [RELEASE_v0.8.0.md](../../../docs/RELEASE_v0.8.0.md) · [lan-discovery.md](../../integrations/lan-discovery.md) |
| **v0.9.0** (in progress) | Re-capture **0.9 chrome** + **HAOS** + **wizard** + **templates catalog** — [PLAN_v0.9.0.md](../../../docs/PLAN_v0.9.0.md) · [HAOS hosts](../../day-to-day/haos-hosts.md) |

**v0.8.0 pack is complete** (2026-07-22). **v0.9** recaptures + **operator testing** are **in progress** (not a tag gate until freeze) — checklist below. Prose in the wiki already describes current UI.

## Default convention (keep simple)

| Default | Value |
|---------|--------|
| Theme | **Light** |
| Viewport | **Desktop** (~1400–1600px wide) |
| Variants | Only when the UI **story** changes |

Optional extras (not a full matrix):

| Suffix | Use |
|--------|-----|
| `*-dark.png` | One showcase (e.g. dashboard or Settings) |
| `*-mobile.png` | Layout differs (Network Hosts map, PWA install) |

Do **not** capture every page in light×dark×mobile. See [Appearance](../../getting-started/appearance.md).

---

## Inventory — existing (may be stale after 0.6 UI)

| File | Page / topic | Priority | v0.8.0 capture notes |
|------|----------------|----------|----------------------|
| `dashboard.png` | Home | High | **Refresh** if cards/layout changed |
| `server-list.png` | Servers + bulk bar + ⋯ | High | **Refresh** — no footer help text; status from last checks; bulk when selected |
| `server-detail.png` | Dest cards, host status, optional LAN chip/card | High | **Refresh** (0.9 ops-hero / LAN chip; ideally one **Debian** host) |
| `server-detail-haos.png` *(new)* | HAOS host: **HAOS** chip, HA updates, version chips | High | **Capture for 0.9** — wiki [haos-hosts](../../day-to-day/haos-hosts.md) |
| `system-info-haos.png` *(new)* | System info modal: Core/OS/Supervisor + HA disk | High | **Capture for 0.9** — after **Refresh** on HAOS server |
| `ha-update-modal.png` *(optional)* | HA update apply modal (not apt steps) | Medium | Optional |
| `ssh-access.png` | SSH access expanded | Medium | **Refresh** if showing HAOS guidance / deps (`ha`, rsync) |
| `backups-page.png` | Sources + path policy | High | Spot-check |
| `jobs-page.png` | Jobs filters | Medium | **Refresh** — new job types (template deploy, stack lifecycle) |
| `templates-catalog.png` | Catalog → Templates | High | Spot-check |
| `templates-deploy.png` | Deploy wizard | High | **Refresh** — Jobs / live log vs wait modal story |
| `templates-deployment.png` | Drift / redeploy / apply config | Medium | Spot-check |
| `integrations-kuma.png` | Kuma detail | Medium | Spot-check |
| `integrations-grafana.png` | Grafana kinds | Medium | Spot-check |
| `integrations-pihole.png` | Pi-hole | Medium | Spot-check |
| `integrations-npm.png` | NPM | Medium | Spot-check |
| `certificates-list.png` | Catalog → Certificates | High | **Refresh** — setup CTA / map status chips |
| `dns-physical.png` | Network Hosts map | High | Spot-check (radar / dual layout if discovery on) |
| `dns-logical.png` | Network Path map | Medium | **Refresh** — stack expand / topology |
| `dns-hub.png` *(new or crop)* | Catalog → Network hub (`/dns`) | Medium | Optional: show **By path type** stats + service paths (not “Path mix”) |
| `services-fleet.png` | `/services` grid | Medium | Spot-check |
| `settings-status.png` | Settings → Status | Medium | Spot-check |
| `account-push.png` | PWA / push | Medium | Spot-check |
| `dashboard-dark.png` | Showcase dark (optional) | Low | Optional |
| `dns-physical-mobile.png` | Hosts map phone (optional) | Low | Optional |

---

## v0.7 / v0.8 surfaces — **landed** (wired in wiki)

| File | UI surface | Status |
|------|------------|--------|
| `certificates-setup.png` | `/certificates/setup` first-cert guide | **Landed** |
| `certificates-detail.png` | Cert detail: maps, presets, path preview, sync status | **Landed** |
| `certificates-edge-map.png` | Self-managed edge card | **Landed** |
| `docker-project-lifecycle.png` | Project ⋯ Stop/Start/Restart all + confirm | **Landed** |
| `dns-coverage.png` | `/dns/coverage` Kuma coverage | **Landed** |
| `dns-stack-panel.png` | Path map with stack expand / side panel | **Landed** |
| `dns-hub.png` | Catalog → Network hub (`/dns`) | **Landed** |
| `nmap-overview.png` | LAN Discovery Overview | **Landed** |
| `nmap-devices.png` | Devices list | **Landed** |
| `nmap-network.png` | Network view | **Landed** |
| `nmap-schedules.png` | Schedules | **Landed** |
| `nmap-runs.png` | Runs table (no ID column) | **Landed** |
| `nmap-server-embed.png` | Server detail LAN card | **Landed** |
| `settings-stale-cleanup.png` | Settings → Stale data cleanup | **Landed** |
| `add-server-wizard.png` | Multi-step add-host wizard | **Landed** |
| `add-server-wizard-done.png` | Wizard done CTAs | **Landed** |

Optional residual (not a tag gate): `jobs-live-log.png` for JobHold live log if a future recapture wants a dedicated shot.

### Pre-tag chrome already on main (do not document old UI)

| Surface | Expected in PNGs |
|---------|------------------|
| **Servers list** | **No** footer line about “Status from last update checks / checkboxes / ⋯” |
| **Catalog → Network hub** | Stat **By path type** (Host / App / NPM) — not “Path mix” |
| **LAN Discovery → Runs** | Columns: intensity, status, hosts, ports, job, finished — **no run `#id`** |
| **LAN Devices** | **List \| Map** (merged former Network tab) |
| **HAOS server detail** | HAOS chip; “HA updates” not bare apt-only wording; no Docker fleet emphasis |
| **System info (HAOS)** | Home Assistant Core / OS / Supervisor cards; disk from `ha host` |

### v0.9 screenshot recapture checklist (operator — in progress)

**Owner:** operator fleet testing (not CI). Replace PNGs under this directory; wiki captions already mention when a figure may lag.

| Priority | File / shot | Why | Suggested page |
|----------|-------------|-----|----------------|
| **P0** | `server-detail-haos.png` | HAOS chip, HA updates, no Docker fleet emphasis | [haos-hosts](../../day-to-day/haos-hosts.md) |
| **P0** | `system-info-haos.png` | Core / OS / Supervisor + disk | [haos-hosts](../../day-to-day/haos-hosts.md) |
| **P1** | `server-detail.png` | 0.9 ops-hero / LAN chip (Debian host) | [add-server](../../day-to-day/add-server.md) |
| **P1** | `nmap-devices.png` | List \| Map merge | [lan-discovery](../../integrations/lan-discovery.md) |
| **P1** | `nmap-overview.png` / `nmap-runs.png` / `nmap-schedules.png` | Overview modals, Runs/Schedules chrome | [lan-discovery](../../integrations/lan-discovery.md) |
| **P2** | `dns-hub.png` / `dns-coverage.png` | Network hub settings strip; coverage cards | [dns-fabric](../../integrations/dns-fabric.md) |
| **P2** | `ssh-access.png` | HAOS guidance / `ha` + rsync deps | [add-server](../../day-to-day/add-server.md) |
| **P2** | `server-list.png` | Attention badges after HA check | day-to-day servers |
| **P2** | `templates-catalog.png` | **OOTB** / **Yours** badges + section groups | [templates overview](../../service-templates/overview.md) |
| **P2** | `add-server-wizard.png` / `add-server-wizard-done.png` | Connect order, Features help, Done CTAs | [add-server](../../day-to-day/add-server.md) |
| Optional | `ha-update-modal.png` | Apply dialog story | [updates](../../day-to-day/updates-and-patching.md) |
| Optional | `templates-from-host.png` *(new)* | Editor with Additional files + `NODE_NAME` | [from-host](../../service-templates/from-host.md) |

**Pre-capture checklist (operator testing):** rebuild web image so UI matches main; use light desktop; redact private hostnames if needed; run `mkdocs build --strict` after dropping PNGs.

---

## Capture tips

1. Use a **non-production** or redacted fleet (no private IPs/hostnames you care about).  
2. Prefer **light** theme; toggle once for optional dark showcase.  
3. Crop browser chrome if noisy; keep page chrome (nav) when it teaches navigation.  
4. PNG, reasonable size (avoid multi‑MB full-desktop dumps).  
5. Name files **kebab-case** matching the tables above.

## How to land screenshots in the wiki

**Best practice: local git repo → commit → push** (same as any code change). Do **not** rely on the live GitHub Pages “edit in browser” for binary PNGs.

### Recommended workflow

```bash
# 1. Branch (optional) and pull
git checkout main && git pull
git checkout -b docs/screenshots-0.7

# 2. Preview wiki while you work
python3 -m venv .venv-docs && source .venv-docs/bin/activate
pip install -r requirements-docs.txt
mkdocs serve   # http://127.0.0.1:8000

# 3. Capture from your running PiHerder UI (browser)
#    Save PNGs into:
#      wiki/assets/screenshots/

# 4. Point Markdown at the PNG
#    e.g. wiki/index.md → assets/screenshots/dashboard.png

# 5. Strict build (catches broken links / missing files)
mkdocs build --strict

# 6. Commit binaries + markdown together
git add wiki/assets/screenshots/*.png wiki/**/*.md
git commit -m "docs(wiki): screenshot pack for 0.7.0"
git push -u origin docs/screenshots-0.7
# Open PR, or merge to main if you are the maintainer
```

After merge to **`main`**, the **Docs** GitHub Action builds MkDocs and deploys **GitHub Pages** (see `.github/workflows/docs.yml`). Wait a minute, hard-refresh the live site.

### Why not only GitHub web UI?

| Approach | Pros | Cons |
|----------|------|------|
| **Local + commit** (recommended) | Preview with `mkdocs serve`, batch many PNGs, strict build, one PR | Needs git on your machine |
| GitHub “Upload file” / web editor | Quick one-off | Poor for many images; hard to preview Material theme; easy to break paths |
| External CMS | — | Not used; wiki is git-native |

### Checklist before commit

- [ ] PNG names match references in Markdown  
- [ ] Light desktop for defaults; optional dark/mobile only where planned  
- [ ] Sensitive hostnames/IPs redacted if needed  
- [ ] `mkdocs build --strict` passes  
- [ ] Wireframe caption badge removed when real PNG lands  

Full style guide: [Contributing docs](../../developers/contributing-docs.md).
