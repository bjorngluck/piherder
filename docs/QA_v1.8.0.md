# PiHerder v1.8.0 — operator QA / sign-off

**Branch:** `v1.8.0-dev` → `main` · tag **`v1.8.0`** (cut after merge)  
**Code freeze:** not set  
**Package:** stays **`1.7.0`** until freeze  
**Operator QA:** not started  
**Pull request:** none. Open a draft only when asked

This file is **maintainer-only** (repo `docs/`). It is **not** published on the operator wiki.

Plan: [PLAN_v1.8.0.md](PLAN_v1.8.0.md). 1.7 sign-off stays [QA_v1.7.0.md](QA_v1.7.0.md). Do not re-open those boxes here.

Plugin work is [bjorngluck/piherder-ha](https://github.com/bjorngluck/piherder-ha). It is not inside the PiHerder image. Do not redeploy the public demo onto this branch.

---

## HA-vis

Walk on a Home Assistant that has the new plugin build. Hard-refresh after the Lovelace resource URL changes. Token masked in every screenshot.

- [ ] Host card gauges and stats are readable without opening PiHerder. Confirm actions still match the token (`jobs` / `edit` / feature flags)  
- [ ] Host 24-hour sparkline draws from Home Assistant history of the snapshot sensors  
- [ ] Resources card 24-hour series draws for memory %, disk %, and CPU load. It does not SSH  
- [ ] Updates card still shows OS and container counts from the stored snapshot  
- [ ] A `read` token shows sensors and no buttons  
- [ ] Fleet card from 0.3.0 still loads  

## 1.7 regression

- [ ] Hosted `POST /mcp` still answers with the same bearer token  
- [ ] Exclusive jobs still run on Celery. About / footer still **1.7.0** until the version bump  
- [ ] Move stays off. Console mux stays opt-in and off on HAOS and the demo  
