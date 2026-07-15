# Local setup

## Docker Compose (recommended)

Same as [Install](../getting-started/install.md). Use `Caddyfile.dev` for self-signed TLS if needed.

## Python host (partial)

```bash
python3 -m venv .venv
source .venv/bin/activate
# Locked deps (same as Docker/CI) — do not use floating pip install -e ".[dev]" for release work
pip install --require-hashes -r requirements.lock.txt
pip install --no-deps -e .
# Or: uv sync --frozen --extra dev
# Refresh pins: ./scripts/refresh-lockfiles.sh
# Need Postgres + Redis (compose up db redis) and .env
uvicorn app.main:app --reload
```

Schema: Alembic on startup, or:

```bash
alembic upgrade head
```

## Frontend assets

Build step vendors Tailwind/HTMX/Alpine. Offline image needs successful vendor at build time.

```bash
bash scripts/vendor_cdns.sh
# or VENDOR_INSECURE=1 if TLS intercept issues
```

## Docs site (this wiki)

```bash
pip install -r requirements-docs.txt
mkdocs serve
# open http://127.0.0.1:8000
```

See [Contributing docs](contributing-docs.md).
