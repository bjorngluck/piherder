# PiHerder v1.13.0: notes

**Status:** **Notes only.** Not a train. No branch. Nothing here is built.  
**Date noted:** 2026-10-19  
**Opens after:** [v1.12.0](PLAN_v1.12.0.md) ships. Do not cut `v1.13.0-dev` until then.  
**Package:** stays on the version v1.12 freezes. This note does not bump it.

> One item, not voted. v1.12 Docker prune does not clear builder cache.

---

## 1. Notes

### Docker

| Tier | Note |
|------|------|
| Untiered | **Build cache.** The host Docker page, the token API, and hosted MCP can prune the Docker build cache and the buildx cache. Same shape as the v1.12 prune job: `edit` scope on the API and MCP, one audit row per run. It is not part of `docker_prune`. `both` on that job stays dangling images and exited containers. No schedule on this note |

---

## 2. Capture log

| Date | What changed |
|------|----------------|
| 2026-10-19 | **Build cache.** Noted from the v1.12 prune work. The page, the token API, and hosted MCP should clear the Docker build cache and the buildx cache. Not on the v1.12 train. Not built. |

---

*Noted 2026-10-19. Not opened.*
