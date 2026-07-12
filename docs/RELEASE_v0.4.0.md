# PiHerder v0.4.0

**Status:** WIP (not tagged yet)  
**Git tag:** `v0.4.0` *(pending)*  
**Baseline:** `v0.3.0`  
**Plan:** [PLAN_v0.4.0.md](PLAN_v0.4.0.md) · [FEATURE_PLAN_TEMPLATES.md](FEATURE_PLAN_TEMPLATES.md)

This file accumulates **release notes** as fixes and features land on `main`.  
Authoritative bug IDs: PLAN §2.

---

## Theme (draft)

Post-0.3 quality (Docker deploy honesty, jobs cancel, notification lifecycle) + **service templates foundation** (wizard, OOTB packs, encrypted desired state).

---

## Fixed since v0.3.0

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

## Features

- [x] Service templates — schema, builtin catalog, import, deploy wizard  
- [x] OOTB templates: NPM, Uptime Kuma, Pi-hole, Grafana (volume + boolean vars)  
- [x] Variable types: **boolean**, **volume** (named / project folder / host path)  
- [x] Desired config V1 (encrypted secrets; edit + redeploy)  
- [x] Host picker with inventory service counts  
- [x] From-host templatize (volumes, ports, booleans, secrets)  
- [x] Step-up 2FA for secret cleartext; optional 2FA gate for deploy (Settings → Security)  
- [x] Template badge on Docker stacks; gate raw compose edit for template-managed projects  
- [x] Wait modal on template preview / deploy / redeploy / from-host pull  
- [x] Manual DNS checklist in post-deploy steps  


---

## Open / stretch (not required to tag)

- **B07** — Docker Deploy/Check as Jobs with live log (→ 0.4.x)  
- **B08** — Include service logo files in herder self-backup (→ 0.4.x)  
- **B09** — Push when alerts auto-resolve  
- Template deploy as background Job with live log (stretch; wait modal covers sync UX for now)  

### Deferred to v0.4.x / v0.5.0

- Config drift schedule · Docker secrets · NPM connector · git template pull  
- Restore + last known config · production wikis · Docker Hub multi-arch  

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

As of last plan update:

| Commit | Summary |
|--------|---------|
| `ba56c2b` | fix(docker): surface deploy pull/up results |
| `d33f286` | fix(docker): resolve container update alert after deploy |
| `538e7e2` | docs: PLAN_v0.4.0 |
| `069b065` | fix: jobs list cancel + backup-failed dismiss/resolve |
| `4686009` | docs: track post-0.3 fixes for release notes |

*Append new rows here when landing further fixes/features.*
