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
| `assets/` | Logo + screenshots |

Config: [`mkdocs.yml`](../mkdocs.yml) at repo root.  
**Live site:** [https://bjorngluck.github.io/piherder/](https://bjorngluck.github.io/piherder/)

Publish: build with MkDocs → `gh-pages` branch (current Pages source), or [`.github/workflows/docs.yml`](../.github/workflows/docs.yml) if Pages is switched to GitHub Actions.

How to contribute: [developers/contributing-docs.md](developers/contributing-docs.md).
