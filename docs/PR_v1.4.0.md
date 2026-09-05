# PR: v1.4.0-dev → main

**Title:** `v1.4.0: Move a service (feature freeze, pending QA)`

**Base:** `main` · **Head:** `v1.4.0-dev` · **Tag:** `v1.4.0` (after sign-off, not this merge yet)

**State:** **Draft** — **dev freeze**. Code changes from here are QA, screenshots, and bugfixes only. Package version stays **1.3.0** until the bump/tag step.

---

## Summary

Fourth minor after production **v1.3.0**. Operators can **move a Docker Compose project** host→host as one audited job (lock, preflight, stop, copy, dest up, DNS or NPM backend, validate, leftover). Public demo **Files** is a canned tree.

User-facing notes: [RELEASE_v1.4.0.md](RELEASE_v1.4.0.md). Design and Must IDs stay in [PLAN_v1.4.0.md](PLAN_v1.4.0.md). Maintainer ticks: [QA_v1.4.0.md](QA_v1.4.0.md).

| Stream | Highlights |
|--------|------------|
| **M1** | Per-project **Lock to this host** (hardware / operator / infra). HAOS always locked. |
| **M2** | Preflight: dest facts, port clash, leftover dest, NPM match, hardware warn. |
| **M3 / M5** | Stop source → herder rsync → dest `up -d`. Job on **web** (not Celery). |
| **M4 / M-npm** | Direct CNAME → dest, or NPM `forward_host` PUT (proxy-host binding is enough). |
| **M6 / M7** | TLS / Kuma when rows exist; maps, Kuma, Grafana **container** chips, cert clone follow dest. |
| **M8 / M-rm** | Leftover: stopped (default) · `compose down` · optional source remove + named volumes. |
| **M9** | Devices / privileged / host-network → warn + ack. |
| **D-F** | Demo simulated Files. Real SFTP still demo never. |
| **Q** | Unit coverage **~62%**; CI fail-under **62**. |

## Migrations (apply on deploy)

| Rev | Purpose |
|-----|---------|
| `042` | `ComposeProjectMeta` (host lock) |

## Test plan

### Freeze / CI
- [x] Unit suite green; coverage **≥ 62%** (fail-under **62**)
- [ ] Playwright E2E (Should — wizard chrome; no live two-host in CI)
- [ ] `mkdocs build --strict`
- [ ] Upgrade path: Alembic `042` on existing 1.3 hosts

### Operator QA (sign-off — [QA_v1.4.0.md](QA_v1.4.0.md))
- [ ] Host lock + HAOS refuse
- [ ] NPM-fronted Move (PUT `forward_host`)
- [ ] Direct TLS Move
- [ ] Leftover / optional source remove (disposable stack)
- [ ] Rebind / Path map / Grafana container chips
- [ ] Demo Files canned tree
- [ ] 1.3 regression
- [ ] Wiki screenshot pack P0/P1

## Out of scope (deferred)

- Live / zero-downtime cutover (**M-live**)
- ACME-in-herder · full NPM CRUD · auto-rollback
- Richer Files token API
- Host `tmux`/`screen` (**W-mux**) · fine-grained roles (**AC-fg**) · N3 · CSP nonces

## Merge checklist

- [ ] Operator **Ready to bump `1.4.0` and tag = Yes** (after QA + screenshots)
- [ ] Version bump `app/version_info.py` + `pyproject.toml` → **1.4.0**
- [ ] Flip [RELEASE_v1.4.0.md](RELEASE_v1.4.0.md) + wiki Home to current production
- [ ] Merge `v1.4.0-dev` → `main`
- [ ] Tag **`v1.4.0`** · Hub `1.4.0` / `1.4` / `latest`
- [ ] Keep `1.3` / `1.3.0` pins valid

## After merge

Hub publish per [PUBLISH_IMAGE.md](PUBLISH_IMAGE.md). Kill switch review: leave `PIHERDER_SERVICE_MIGRATE` **false** at tag unless GA-enough.
