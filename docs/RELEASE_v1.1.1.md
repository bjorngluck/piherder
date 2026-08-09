# PiHerder v1.1.1

**Status:** **Tagged** — current production release  
**Date:** 2026-08-09  
**Git tag:** `v1.1.1`  
**Package / image version:** `1.1.1`  
**Baseline:** `v1.1.0` (2026-08-08)  
**Theme:** **Patch** — restore SSH test & host dependency check after router split  

**Prior:** [RELEASE_v1.1.0.md](RELEASE_v1.1.0.md) · [RELEASE_v1.0.0.md](RELEASE_v1.0.0.md)  
**Next development train:** [PLAN_v1.2.0.md](PLAN_v1.2.0.md) on `v1.2.0-dev`  
**Roadmap:** [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md)  
**Docs:** https://piherder-docs.hacknow.info/

**Image:** [bjorngluck/piherder](https://hub.docker.com/r/bjorngluck/piherder) — multi-arch `linux/amd64` + `linux/arm64`  
**Tags:** `1.1.1` · `1.1` · `latest` (older `1.1.0` / `1.0.x` pins remain valid)

---

## Why this release

**v1.1.0** shipped a router extract that left SSH access handlers calling `run_in_threadpool` without importing it. Operators hit:

- **Test connection** → HTTP 500 (internal error)
- **Check dependencies** → failed banner: `name 'run_in_threadpool' is not defined`
- The same gap could affect other SSH access actions that run work in a thread pool (deploy key, rotate, least-priv, post-test deps)

**v1.1.1** restores the missing Starlette import. No schema changes, no new features, no config changes.

---

## Fix

| Area | Change |
|------|--------|
| `app/routers/server_ssh.py` | Import `run_in_threadpool` from `starlette.concurrency` |

---

## Upgrade (from v1.1.0)

1. Self-backup / volume snapshot if you prefer.  
2. Keep the same **`PIHERDER_MASTER_KEY`**.  
3. Pull and restart:

   ```bash
   docker compose pull
   # or pin:
   PIHERDER_IMAGE=bjorngluck/piherder:1.1.1 docker compose up -d
   ```

4. Smoke: open a host → **SSH access → Test connection** and **Check dependencies**.

No Alembic migrations in this patch.

---

## Install (new)

Same as [v1.1.0](RELEASE_v1.1.0.md); pin **`v1.1.1`** / image `1.1.1` (or `1.1` / `latest` after publish).

```bash
git checkout v1.1.1
export PIHERDER_IMAGE=bjorngluck/piherder:1.1.1
docker compose up -d
```

---

## Verify

- [ ] Login  
- [ ] Server → **Test connection** succeeds (or returns a real SSH error, not 500)  
- [ ] Server → **Check dependencies** completes (or real remote failure text)  

---

## Not in this release

Everything else is unchanged from **v1.1.0**. Active feature work continues on **`v1.2.0-dev`**.
