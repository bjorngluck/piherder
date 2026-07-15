# Screenshots

Real UI captures live here. Wiki pages reference them like:

```markdown
![Dashboard](../assets/screenshots/dashboard.png)
```

Wireframe SVGs (`*.svg`) are interim placeholders until PNGs exist.

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

## Recommended inventory (RC)

| File | Page / topic | Priority |
|------|----------------|----------|
| `dashboard.png` | Home | High |
| `server-list.png` | Servers + bulk bar | High |
| `server-detail.png` | Dest cards, host status | High |
| `ssh-access.png` | SSH access expanded | Medium |
| `backups-page.png` | Sources + path policy | High |
| `jobs-page.png` | Jobs filters | Medium |
| `templates-catalog.png` | Catalog → Templates | High |
| `templates-deploy.png` | Deploy wizard | High |
| `templates-deployment.png` | Drift / redeploy / apply config | Medium |
| `integrations-kuma.png` | Kuma detail | Medium |
| `integrations-grafana.png` | Grafana kinds | Medium |
| `integrations-pihole.png` | Pi-hole | Medium |
| `integrations-npm.png` | NPM | Medium |
| `certificates-list.png` | Catalog → Certificates | High |
| `dns-physical.png` | Network Hosts map | High |
| `dns-logical.png` | Network Path map | Medium |
| `services-fleet.png` | `/services` grid | Medium |
| `settings-status.png` | Settings → Status | Medium |
| `account-push.png` | PWA / push | Medium |
| `dashboard-dark.png` | Showcase dark (optional) | Low |
| `dns-physical-mobile.png` | Hosts map phone (optional) | Low |

## Capture tips

1. Use a **non-production** or redacted fleet (no private IPs/hostnames you care about).  
2. Prefer **light** theme; toggle once for optional dark showcase.  
3. Crop browser chrome if noisy; keep page chrome (nav) when it teaches navigation.  
4. PNG, reasonable size (avoid multi‑MB full-desktop dumps).  
5. Name files **kebab-case** matching the table above.

## How to land screenshots in the wiki

**Best practice: local git repo → commit → push** (same as any code change). Do **not** rely on the live GitHub Pages “edit in browser” for binary PNGs.

### Recommended workflow

```bash
# 1. Branch (optional) and pull
git checkout main && git pull
git checkout -b docs/screenshots-rc

# 2. Preview wiki while you work
python3 -m venv .venv-docs && source .venv-docs/bin/activate
pip install -r requirements-docs.txt
mkdocs serve   # http://127.0.0.1:8000

# 3. Capture from your running PiHerder UI (browser)
#    Save PNGs into:
#      wiki/assets/screenshots/

# 4. Point Markdown at the PNG (replace .svg wireframes)
#    e.g. wiki/index.md → assets/screenshots/dashboard.png

# 5. Strict build (catches broken links / missing files)
mkdocs build --strict

# 6. Commit binaries + markdown together
git add wiki/assets/screenshots/*.png wiki/**/*.md
git commit -m "docs(wiki): real screenshots for dashboard and network maps"
git push -u origin docs/screenshots-rc
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
