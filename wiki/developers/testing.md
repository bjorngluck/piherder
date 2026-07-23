# Testing

## Unit tests

```bash
# Inside compose (recommended)
docker compose run --rm --no-deps web pytest -q

# Working tree newer than the image — mount tests + app
docker compose run --rm --no-deps \
  -v "$PWD/tests:/app/tests" -v "$PWD/app:/app/app" \
  web pytest -q

# Host venv (locked — matches CI/image)
pip install --require-hashes -r requirements.lock.txt
pip install --no-deps -e .
pytest -q
# Coverage (v0.9 freeze ~56% suite; CI fail-under 55 + XML artifact)
pip install pytest-cov
pytest -q --cov=app --cov-report=term-missing:skip-covered --cov-fail-under=55
```

Unit tests live under `tests/` — no live SSH required for the main suite. Default `pytest` only collects `tests/` (not `e2e/`).

| Bar | Value (v0.9 train) |
|-----|--------------------|
| **Suite freeze target** | **≥ 55%** line on `app` — **~57.4%** reached |
| **CI fail-under** | **55** (stepped 35 → 45 → 50 → 55; matches freeze bar) |
| **v1.0** | Further depth planned — no 100% target |

Examples: `test_rbac.py`, `test_api_tokens.py`, `test_service_templates.py`, **`test_template_source_badge.py`** (OOTB/Yours badges), **`test_from_host_extra_files.py`** (promtail-style sidecars + `NODE_NAME` / remote URL vars), `test_backup_paths.py`, `test_herder_backup.py`, `test_job_exclusive.py` (no double OS/container jobs; stack job types), `test_request_ip_audit.py` (Caddy XFF + audit `client_ip`), `test_dns_fabric.py` / `test_dns_fabric_core_coverage.py` (paths, Hosts/Path SVG, dual layout, spine), `test_certificates_deep.py` (edge Caddy, SSH deploy mocks, NPM renew), `test_scheduler_sync_coverage.py` (APScheduler MagicMock), `test_audit_format_branches.py`, `test_backup_status_helpers.py`, `test_jwt_tokens.py`, `test_server_job_lock.py`, `test_nmap_discovery.py` (**no live LAN scan in CI**), `test_nmap_device_classify.py`, `test_nmap_worker_guard.py`, `test_nmap_options_classify.py`, **`test_haos.py`** (HA CLI JSON envelope, disk facts, check/apply mocks — **no live HAOS in CI**), `test_server_wizard.py`, `test_http_smoke.py`, …

```bash
# Network maps only
docker compose exec -T web python -m pytest tests/test_dns_fabric.py -q
```

## Browser E2E (Playwright)

**Playwright E2E** — shell smoke + wizard journeys + B6 viewer RBAC (and nmap shells with fixtures). Suite lives in `e2e/`; details in [e2e/README.md](https://github.com/bjorngluck/piherder/blob/main/e2e/README.md).

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
- **v0.9 chrome (landed):** Devices List\|Map, Network hub modals, coverage cards, Schedules/Runs mobile, **`test_e11_templates_ootb_badges`** (Templates OOTB badge), nmap shells — see `e2e/`  

Related unit coverage: `tests/test_compose_sets.py`, `tests/test_container_annotations.py`, `tests/test_nest_projects.py`, `tests/test_haos.py`.

!!! note "Operator testing (v0.9)"
    CI covers unit + Playwright on fixtures. **Live fleet validation** (real SSH, HAOS, from-host of `grafana-monitoring`, screenshot recapture) is done by the operator outside CI — not a substitute for green unit/E2E.

## CI

| Job | When | What |
|-----|------|------|
| Unit | push/PR (app, tests, migrations, locks) | [`.github/workflows/test.yml`](https://github.com/bjorngluck/piherder/blob/main/.github/workflows/test.yml) — hashed lock + `pytest -q` |
| **E2E** | push/PR (app, e2e, compose, Dockerfile, …) | [`.github/workflows/e2e.yml`](https://github.com/bjorngluck/piherder/blob/main/.github/workflows/e2e.yml) — e2e compose set + Playwright Chromium |
| Docs | wiki / mkdocs changes | [`.github/workflows/docs.yml`](https://github.com/bjorngluck/piherder/blob/main/.github/workflows/docs.yml) |

## Before a release

1. Unit `pytest -q` green (incl. HAOS + template badge/from-host packs)  
2. **E2E** `pytest e2e -q` green (CI or local; rebuild e2e image if app templates changed)  
3. Manual / operator smoke (live fleet): add-server wizard path, HAOS System info + check, from-host with additional files, template deploy, backup, metrics, API token  
4. Screenshots: replace PNGs on the [v0.9 recapture checklist](https://github.com/bjorngluck/piherder/blob/main/wiki/assets/screenshots/README.md) (operator-owned; not in CI)  
5. See release checklist in `docs/RELEASE_v*.md` / active plan [PLAN_v0.9.0](https://github.com/bjorngluck/piherder/blob/main/docs/PLAN_v0.9.0.md) · last tag [RELEASE_v0.8.0](https://github.com/bjorngluck/piherder/blob/main/docs/RELEASE_v0.8.0.md)
