# PR: v1.5.0-dev → main

**Title:** `v1.5.0: Move on Celery, Reports layout`

**Base:** `main` · **Head:** `v1.5.0-dev` · **Tag:** `v1.5.0` (after merge)

**State:** **Ready** — package **1.5.0**. Kill switch stays **false**. Operator QA signed 2026-09-18.

---

## Summary

Fifth minor after production **v1.4.0**. Move runs on the **Celery worker** so recycling **web** does not abort a copy. Reports cards can be pinned / hidden / reordered; Move jobs card. Expired session → Sign in. Host reboot ignores logind inhibitors.

User-facing notes: [RELEASE_v1.5.0.md](RELEASE_v1.5.0.md). Design: [PLAN_v1.5.0.md](PLAN_v1.5.0.md). Maintainer ticks: [QA_v1.5.0.md](QA_v1.5.0.md).

| Stream | Highlights |
|--------|------------|
| **M-worker** | `app.tasks.service_migrate` on backup worker; dual-host backup mutex; web recycle safe; worker recycle fails running Move |
| **N3a** | `/reports` pin / hide / reorder (cookie `ph_reports_layout`) |
| **N3b** | Move jobs card (count / fail / last dest) |
| **M-hb** | JobHold / `_flush_job_progress` on the worker |
| **Q** | Unit **~70.6%**; CI fail-under **70** |
| **B-login-json** | HTML 303 Sign in; HTMX `HX-Redirect`; `/api/v1` JSON 401 |
| **B-reboot-i** | `systemctl reboot --ignore-inhibitors` |
| **M-flag** | **C — stay false** at tag |

Discover write-ups (not Should): CSP-n, HA-p2, M-undo, W-mux, Brand, J-runtime (→ **v1.7**).

## Migrations (apply on deploy)

None. Last rev is still **`042`** (`ComposeProjectMeta`).

## Test plan

### Freeze / CI
- [x] Unit suite green; coverage **≥ 70%** (fail-under **70**)
- [ ] Playwright E2E (Should — wizard chrome; no live two-host in CI)
- [x] `mkdocs build --strict`
- [x] Upgrade path: no new Alembic; recreate **web** + **celery-worker**

### Operator QA (sign-off — [QA_v1.5.0.md](QA_v1.5.0.md))
- [x] Recycle **web** mid-Move · recycle **worker** mid-Move
- [x] N3a / N3b Reports
- [x] B-login-json · B-reboot-i
- [x] 1.4 regression (lock, Direct TLS, NPM, leftover, policy/Files/console)

## Out of scope (deferred)

- Default-on Move (**M-flag C**)
- Auto-rollback (**M-undo** → v1.6)
- HA HACS plugin (**HA-p2** → v1.6)
- Remaining jobs on Celery (**J-runtime** → v1.7)
- Host `tmux`/`screen` · branding · CSP nonces · **AC-fg**

## Merge checklist

- [x] Operator QA complete 2026-09-18
- [x] Version bump `app/version_info.py` + `pyproject.toml` → **1.5.0**
- [x] Flip [RELEASE_v1.5.0.md](RELEASE_v1.5.0.md) + wiki Home to current production
- [ ] Merge `v1.5.0-dev` → `main`
- [ ] Tag **`v1.5.0`** · Hub `1.5.0` / `1.5` / `latest`
- [ ] Keep `1.4` / `1.4.0` pins valid
- [x] Kill switch `PIHERDER_SERVICE_MIGRATE` stays **false** at tag

## After merge

Hub publish per [PUBLISH_IMAGE.md](PUBLISH_IMAGE.md). Deploy public demo from `v1.5.0` (or `main` after tag). GitHub Release body = [RELEASE_v1.5.0.md](RELEASE_v1.5.0.md).
