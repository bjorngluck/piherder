# Testing

```bash
# Inside compose (recommended)
docker compose run --rm --no-deps web pytest -q

# Host venv
pip install -e ".[dev]"
pytest -q
```

Unit tests live under `tests/` — no live SSH required for the main suite.

Examples: `test_rbac.py`, `test_api_tokens.py`, `test_service_templates.py`, `test_backup_paths.py`, `test_herder_backup.py`, `test_job_exclusive.py` (no double OS/container jobs), `test_server_job_lock.py` (backup mutex), …

## Before a release

1. `pytest -q` green  
2. Manual smoke: register, add server, backup, template deploy, metrics, API token  
3. See release checklist in `docs/RELEASE_v*.md` / [PLAN_v0.5.0](https://github.com/bjorngluck/piherder/blob/main/docs/PLAN_v0.5.0.md)  
