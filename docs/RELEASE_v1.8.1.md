# PiHerder v1.8.1

**Status:** **Tagged** — current production patch  
**Date:** 2026-10-01  
**Git tag:** `v1.8.1`  
**Package / image version:** `1.8.1`  
**Baseline:** `v1.8.0` (2026-10-01)  
**Theme:** **Patch** — PyJWT and urllib3 lock refresh  

**Prior:** [RELEASE_v1.8.0.md](RELEASE_v1.8.0.md)  
**Next development train:** [PLAN_v1.9.0.md](PLAN_v1.9.0.md) on `v1.9.0-dev`  
**Docs:** https://piherder-docs.hacknow.info/

**Image:** [bjorngluck/piherder](https://hub.docker.com/r/bjorngluck/piherder) — multi-arch `linux/amd64` + `linux/arm64`  
**Tags:** `1.8.1` · `1.8` · `latest`. Pin `1.8.0` stays the previous image. Pins `1.7.0` / `1.7` stay valid.

---

## Why this release

**v1.8.0** locked **PyJWT 2.13.0** and **urllib3 2.7.0**. Those pins match GitHub Security Advisories at critical and high severity.

**v1.8.1** raises the floors and refreshes the three lockfiles:

| Package | Was | Now |
|---------|-----|-----|
| PyJWT | 2.13.0 | **2.15.1** |
| urllib3 | 2.7.0 | **2.8.0** |

No schema change. No new environment variable. Move stays off. Recreate **web** and **celery-worker**.

This tag is `main` after that lock commit. `main` already included hosted `trigger_job` for `container_start`, `container_stop`, `container_restart`, and `container_redeploy` (pull request #22). The **1.8.0** image does not. `source_filter` is the compose directory and `service` is required. Published adapter **0.2.0** still does not send those four types.

---

## Upgrade (from v1.8.0)

1. Self-backup first.
2. `docker compose pull && docker compose up -d` with image `bjorngluck/piherder:1.8.1`, or `1.8` / `latest` after this publish.
3. Confirm About / footer says **1.8.1**.
4. Move stays off unless you already set `PIHERDER_SERVICE_MIGRATE=true`.
