# Testing

## Unit tests

```bash
# Inside compose (recommended)
docker compose run --rm --no-deps web pytest -q

# Host venv (locked — matches CI/image)
pip install --require-hashes -r requirements.lock.txt
pip install --no-deps -e .
pytest -q
```

Unit tests live under `tests/` — no live SSH required for the main suite. Default `pytest` only collects `tests/` (not `e2e/`).

Examples: `test_rbac.py`, `test_api_tokens.py`, `test_service_templates.py`, `test_backup_paths.py`, `test_herder_backup.py`, `test_job_exclusive.py` (no double OS/container jobs; stack job types), `test_request_ip_audit.py` (Caddy XFF + audit `client_ip`), `test_dns_fabric.py` (paths, Hosts/Path SVG, cloud/LAN classification, spine layout, GET-safe view, case-insensitive Docker fabric index), `test_jwt_tokens.py` (PyJWT HS256), `test_server_job_lock.py` (backup mutex), …

```bash
# Network maps only
docker compose exec -T web python -m pytest tests/test_dns_fabric.py -q
```

## Browser E2E (Playwright)

**v0.7.0 must** — shell smoke + wizard journeys (hard tag gate). Suite lives in `e2e/`; details in [e2e/README.md](https://github.com/bjorngluck/piherder/blob/main/e2e/README.md) and [PLAN_v0.7.0 stream E](https://github.com/bjorngluck/piherder/blob/main/docs/PLAN_v0.7.0.md).

```bash
# One-time on the host
pip install "pytest-playwright>=0.5"
playwright install chromium

# Compose set docker-compose.e2e.yml under project piherder (port 18000, own volumes)
# Same folder as main — not a separate Docker project card. Does not use the main app DB.
./scripts/e2e-up.sh
export PIHERDER_E2E_BASE_URL=http://127.0.0.1:18000
pytest e2e -q --browser chromium
./scripts/e2e-down.sh          # stop e2e set services; main stack left alone
./scripts/e2e-down.sh --volumes  # also wipe e2e volumes
```

- **Chromium only** for 0.7  
- Seed admin: `e2e@piherder.test` / `E2eTestPass1` (auto-register on empty e2e DB)  
- No live SSH to fleet hosts  
- **Compose set:** services `e2e-web` / `e2e-db` / `e2e-redis` in `docker-compose.e2e.yml` ([Compose sets](../docker/overview.md#compose-sets-same-folder-one-project-card))  
- **Phase A (landed):** login, primary nav, Catalog tabs, theme toggle, logout — `e2e/test_shell_login.py`, `e2e/test_shell_nav.py`  
- **Phase B (landed):** open wizard, identity→trust, save & exit, clear-password, advanced form — `e2e/test_add_server_wizard.py`  

Related unit coverage: `tests/test_compose_sets.py`, `tests/test_container_annotations.py`, `tests/test_nest_projects.py`.

## CI

| Job | When | What |
|-----|------|------|
| Unit | push/PR (app, tests, migrations, locks) | [`.github/workflows/test.yml`](https://github.com/bjorngluck/piherder/blob/main/.github/workflows/test.yml) — hashed lock + `pytest -q` |
| **E2E** | push/PR (app, e2e, compose, Dockerfile, …) | [`.github/workflows/e2e.yml`](https://github.com/bjorngluck/piherder/blob/main/.github/workflows/e2e.yml) — e2e compose set + Playwright Chromium |
| Docs | wiki / mkdocs changes | [`.github/workflows/docs.yml`](https://github.com/bjorngluck/piherder/blob/main/.github/workflows/docs.yml) |

## Before a release

1. Unit `pytest -q` green  
2. **E2E** `pytest e2e -q` green (CI or local)  
3. Manual smoke: register, add server, backup, template deploy, metrics, API token  
4. See release checklist in `docs/RELEASE_v*.md` / [RELEASE_v0.7.0](https://github.com/bjorngluck/piherder/blob/main/docs/RELEASE_v0.7.0.md)  
