# PR: v1.15.0-dev → main

**Title:** `v1.15.0: keep the app up, alert on a quiet host, and harden boot`

**Base:** `main` · **Head:** `v1.15.0-dev` · [QA_v1.15.0.md](QA_v1.15.0.md): walks are empty.

**State:** Draft [#101](https://github.com/bjorngluck/piherder/pull/101). Code freeze is set 2026-11-08. Notes are [RELEASE_v1.15.0.md](RELEASE_v1.15.0.md) (draft). Package **1.15.0**. The published image stays **1.14.0**. Not tagged.

QA walks are empty, so this stays a draft. Merge, the `v1.15.0` tag, the GitHub Release, and the Hub push are a separate ask.

Design: [PLAN_v1.15.0.md](PLAN_v1.15.0.md). Maintainer ticks: [QA_v1.15.0.md](QA_v1.15.0.md). Notes: [RELEASE_v1.15.0.md](RELEASE_v1.15.0.md). Screenshot list: [v1.15 pack](../wiki/assets/screenshots/README.md#v115--pack-status). Reserved, not captured.

## Test plan

- [ ] Unit suite on sqlite. CI `--cov-fail-under` is **90**
- [ ] Postgres migration job upgrades to head and downgrades to base
- [ ] `mkdocs build --strict`
- [ ] Walk [QA_v1.15.0.md](QA_v1.15.0.md), including the 1.14 regression
- [ ] `python3 scripts/check_release_sync.py` before the tag. Companions stay plugin **0.5.4** and adapter **0.4.4** until those repos move

## Ship steps

Alembic `054_job_audit_indexes` is in the tree. Do not apply it while the **1.14.0** image is still serving the database. Recreate **web**, **celery-worker**, and **celery-worker-nmap** when this ships. About says **1.15.0**.

- [ ] QA boxes signed in [QA_v1.15.0.md](QA_v1.15.0.md)
- [ ] Screenshots captured and wired. Frames do not show the footer
- [ ] End-user notes (`RELEASE_v1.15.0.md`) leave draft status
- [x] Version pull request opened from this file. It stays a draft until the QA boxes are signed
- [ ] Merge to `main`
- [ ] Tag `v1.15.0` and publish the image (`1.15.0`, `1.15`, `latest`). Pin `1.14.0` stays that release
- [ ] Public demo, only when asked
