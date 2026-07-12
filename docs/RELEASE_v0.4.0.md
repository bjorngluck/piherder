# PiHerder v0.4.0

**Status:** WIP (not tagged yet)  
**Git tag:** `v0.4.0` *(pending)*  
**Baseline:** `v0.3.0`  
**Plan:** [PLAN_v0.4.0.md](PLAN_v0.4.0.md)

This file accumulates **release notes** as fixes and features land on `main`.  
Authoritative bug IDs: PLAN §2.

---

## Theme (draft)

Post-0.3 quality (Docker deploy honesty, jobs cancel, notification lifecycle) + *templates foundation TBD*.

---

## Fixed since v0.3.0

*(Copy into the final “Highlights” section at tag time.)*

### Docker

- **B01** — Deploy no longer silent-success: records `pull` / `up -d` exit codes and output in audit; result banner on Docker page (`ba56c2b`).
- **B02** — Successful Deploy clears stack from pending updates and resolves `container_updates` when none remain (`d33f286`).
- **B03** — UI clarifies **Check updates** = pull only; **Deploy** applies images via `up -d` (`ba56c2b` + banners).

### Jobs

- **B04** — Jobs list **Cancel** button works (was blocked by `stopPropagation`; modal Cancel already worked) (`069b065`).

### Notifications

- **B05** — Successful backup always resolves open `backup_failed` alert (even if job row already terminal) (`069b065`).
- **B06** — Dismiss is idempotent if the alert was already resolved/dismissed (`069b065`).

---

## Features (TBD)

- [ ] Service templates (schema, apply, samples) — see PLAN §4–5  
- [ ] *(add as shipped)*

---

## Open / stretch (not required to tag)

- **B07** — Docker Deploy/Check as Jobs with live log  
- **B08** — Include service logo files in herder self-backup  
- **B09** — Push when alerts auto-resolve  

---

## Install (when tagged)

```bash
git fetch --tags
git checkout v0.4.0
docker compose up -d --build
```

---

## Package version

`pyproject.toml` → `0.4.0` *(at tag time; still 0.3.0 until release)*

---

## Commits to include

```text
git log --oneline v0.3.0..v0.4.0
```

As of last plan update (pre-tag):

| Commit | Summary |
|--------|---------|
| `ba56c2b` | fix(docker): surface deploy pull/up results |
| `d33f286` | fix(docker): resolve container update alert after deploy |
| `538e7e2` | docs: PLAN_v0.4.0 |
| `069b065` | fix: jobs list cancel + backup-failed dismiss/resolve |

*Append new rows here when landing further fixes.*
