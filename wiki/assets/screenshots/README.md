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
| **v0.8.0 RC3** (active) | **Full capture pack** + prose audit + nmap UI shots — [PLAN_v0.8.0.md](../../../docs/PLAN_v0.8.0.md) |

Track status below; capture during the **0.8** cycle (wizard UI is frozen enough).

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

| File | Page / topic | Priority | v0.7.0 action |
|------|----------------|----------|----------------|
| `dashboard.png` | Home | High | **Refresh** if cards/layout changed |
| `server-list.png` | Servers + bulk bar | High | **Refresh** if bulk chrome changed |
| `server-detail.png` | Dest cards, host status | High | **Refresh** (ops-hero / cards) |
| `ssh-access.png` | SSH access expanded | Medium | Keep / refresh if panel copy changed |
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
| `dns-physical.png` | Network Hosts map | High | Spot-check |
| `dns-logical.png` | Network Path map | Medium | **Refresh** — stack expand / topology |
| `services-fleet.png` | `/services` grid | Medium | Spot-check |
| `settings-status.png` | Settings → Status | Medium | Spot-check |
| `account-push.png` | PWA / push | Medium | Spot-check |
| `dashboard-dark.png` | Showcase dark (optional) | Low | Optional |
| `dns-physical-mobile.png` | Hosts map phone (optional) | Low | Optional |

---

## v0.7.0 capture list — **new / missing** (0.6 product, no PNG yet)

Add these files under `wiki/assets/screenshots/` and wire Markdown when capturing.

| File (proposed) | UI surface | Why | Priority |
|-----------------|------------|-----|----------|
| `certificates-setup.png` | `/certificates/setup` first-cert guide | New 0.6 happy path | **High** |
| `certificates-detail.png` | Cert detail: maps, presets, path preview, sync status | Core vault UX | **High** |
| `certificates-edge-map.png` | Self-managed edge card (Apply / mapping on / Remove) | Dual TLS + renew story | **High** |
| `docker-project-lifecycle.png` | Project ⋯ Stop/Start/Restart all + confirm | Bulk lifecycle Jobs | **High** |
| `jobs-live-log.png` | JobHold live log (template deploy or stack restart) | Jobs pattern operators rely on | **High** |
| `dns-coverage.png` | `/dns/coverage` Kuma coverage | H3 shipped in 0.6 | **Medium** |
| `dns-stack-panel.png` | Path map with stack expand / side panel | Runtime topology | **Medium** |
| `add-server-wizard.png` | Multi-step add-host wizard (`/servers/new`) | **Feature on main** — capture for tag | **High** |
| `add-server-wizard-done.png` | Wizard summary / done CTAs | Same | Medium |

Wiki pages to update when PNGs land:

- [certificates](../../integrations/certificates.md) · [https-tls](../../getting-started/https-tls.md)  
- [Docker overview](../../docker/overview.md) · [jobs-audit](../../day-to-day/jobs-audit-notifications.md)  
- [dns-fabric](../../integrations/dns-fabric.md) · [add-server](../../day-to-day/add-server.md) (wizard primary path)

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
