# PiHerder wiki (source)

Markdown sources for the **MkDocs Material** documentation site.

**Live site:** [https://piherder-docs.hacknow.info/](https://piherder-docs.hacknow.info/)  
**Release notes:** [docs/RELEASE_v1.1.0.md](../docs/RELEASE_v1.1.0.md) (draft freeze) · [docs/RELEASE_v1.0.0.md](../docs/RELEASE_v1.0.0.md)  
**Screenshots:** [assets/screenshots/README.md](assets/screenshots/README.md) — **v1.1 recapture priorities** while freezing

Operator pages prefer: **What this is** → **Why** → **End-to-end** → reference detail. See [contributing-docs](developers/contributing-docs.md).

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
| `day-to-day/` | Dashboard, Services, servers, HAOS hosts, backups, updates, jobs |
| `docker/` | Host Docker browser, inventory, compose edit |
| `service-templates/` | Deploy / from-host / secrets *(not named `templates/` — MkDocs reserves that)* |
| `integrations/` | Kuma, Grafana, Pi-hole, NPM, certificates, Network maps, LAN discovery |
| `account-security/` | RBAC, users, 2FA, PWA |
| `operations/` | Settings, env, DR, API, metrics, multi-worker |
| `troubleshooting/` | Common failures |
| `developers/` | Setup, architecture, schema, testing, publish |
| `assets/` | Brand marks + screenshots |
| `assets/screenshots/README.md` | Capture conventions + **what to recapture per release** |

Config: [`mkdocs.yml`](../mkdocs.yml) at repo root.
