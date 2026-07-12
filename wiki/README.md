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
| `getting-started/` | Install, TLS, first login |
| `day-to-day/` | Servers, backups, updates, jobs |
| `docker/` | Host Docker browser |
| `service-templates/` | Deploy / from-host / secrets *(not named `templates/` — MkDocs reserves that)* |
| `integrations/` | Kuma, Grafana |
| `account-security/` | RBAC, 2FA, PWA |
| `operations/` | Env, DR, API, metrics |
| `troubleshooting/` | Common failures |
| `developers/` | Setup, architecture, schema |
| `assets/` | Logo + screenshots |

Config: [`mkdocs.yml`](../mkdocs.yml) at repo root.  
Publish: [`.github/workflows/docs.yml`](../.github/workflows/docs.yml) → GitHub Pages — **enabled at v0.5.0 RC** when the repo is made **public** (free Pages does not work on private repos). Until then, use `mkdocs serve` only.

How to contribute: [developers/contributing-docs.md](developers/contributing-docs.md).
