# PiHerder wiki (source)

Markdown sources for the **MkDocs Material** documentation site.

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
| `day-to-day/` | Dashboard, Services, servers, backups, updates, jobs |
| `docker/` | Host Docker browser, inventory, compose edit |
| `service-templates/` | Deploy / from-host / secrets *(not named `templates/` — MkDocs reserves that)* |
| `integrations/` | Kuma, Grafana, Pi-hole, NPM, certificates, Network maps |
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
