# PiHerder wiki (source)

Markdown sources for the **MkDocs Material** documentation site.

| Line | Status |
|------|--------|
| **v0.8.0** | Tagged — baseline screenshot pack |
| **v0.9.0** | Living docs on main — last pre-production; **full operator QA + screenshot review** in progress |
| **1.0.0** | First production plan — [PLAN_v1.0.0.md](../docs/PLAN_v1.0.0.md) |

Operator pages prefer: **What this is** → **Why** → **End-to-end** → reference detail. See [contributing-docs](developers/contributing-docs.md).

**Screenshots & testing (operator):** full QA is operator-owned. **Prose tracks main**; replace PNGs under `assets/screenshots/` as you review each figure. Checklist + expected chrome + capture order: [assets/screenshots/README.md](assets/screenshots/README.md) (not published as a site page — excluded in `mkdocs.yml`).

## Preview locally

```bash
# from repo root
python3 -m venv .venv-docs
source .venv-docs/bin/activate
pip install -r requirements-docs.txt
mkdocs serve
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

Strict build (same as CI):

```bash
mkdocs build --strict
```

## Layout

| Path | Content |
|------|---------|
| `getting-started/` | Install, TLS, first login, appearance, scenarios |
| `day-to-day/` | Dashboard, Services, servers, **HAOS hosts**, backups, updates, jobs |
| `docker/` | Host Docker browser, inventory, compose edit |
| `service-templates/` | Deploy / from-host / secrets *(not named `templates/` — MkDocs reserves that)* |
| `integrations/` | Kuma, Grafana, Pi-hole, NPM, certificates, Network maps, LAN discovery |
| `account-security/` | RBAC, users, 2FA, PWA |
| `operations/` | Settings, env, DR, API, metrics, multi-worker |
| `troubleshooting/` | Common failures |
| `developers/` | Setup, architecture, schema, testing, publish |
| `assets/` | Brand marks (light/dark) + screenshots — see also `app/static/images/README.md` |

Config: [`mkdocs.yml`](../mkdocs.yml) at repo root.  
**Live site:** [https://piherder-docs.hacknow.info/](https://piherder-docs.hacknow.info/)

**Publish:** [`.github/workflows/docs.yml`](../.github/workflows/docs.yml) builds with `mkdocs build --strict` and deploys via **GitHub Actions → Pages**.

One-time repo setting (required for deploy):  
**Settings → Pages → Build and deployment → Source: GitHub Actions**  
(not “Deploy from a branch” / `gh-pages`).

**Validate while editing** (same as CI build job):

```bash
mkdocs build --strict
```

How to contribute: [developers/contributing-docs.md](developers/contributing-docs.md).
