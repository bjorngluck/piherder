# PiHerder E2E (Playwright)

Browser end-to-end tests for the operator shell and (later) add-host wizard.

| Phase | Status |
|-------|--------|
| **A1–A2** | Login form + admin login → Dashboard |
| **A3–A6** | Primary nav, Catalog tabs, theme toggle, logout |
| **B** | B1–B5 landed (open, identity→trust, save&exit, clear-password, advanced) |

## Prerequisites

- Docker + Compose (for the app under test)
- Host Python 3.12+ with Playwright for the runner

## One-time setup (host)

```bash
# From repo root — unit lockfile + e2e extras
pip install --require-hashes -r requirements.lock.txt
pip install --no-deps -e .
pip install "pytest-playwright>=0.5"
playwright install chromium
```

## Run locally

```bash
./scripts/e2e-up.sh
export PIHERDER_E2E_BASE_URL=http://127.0.0.1:18000
pytest e2e -q
./scripts/e2e-down.sh          # keep volumes
./scripts/e2e-down.sh --volumes  # wipe e2e DB (fresh register next time)
```

Default seed admin (created on empty DB via `/auth/register`):

| | |
|--|--|
| Email | `e2e@piherder.test` |
| Password | `E2eTestPass1` |

Override with `PIHERDER_E2E_EMAIL` / `PIHERDER_E2E_PASSWORD`.

## Design notes

- **Chromium only** for v0.7
- **No live SSH** to fleet hosts
- Isolated compose project `piherder-e2e` (port **18000**, separate volumes)
- Synthetic master key / secret — not for production
- Unit tests stay under `tests/` and do not import Playwright

Ship bar: [docs/PLAN_v0.7.0.md](../docs/PLAN_v0.7.0.md) stream E.
