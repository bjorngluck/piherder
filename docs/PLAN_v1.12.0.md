# PiHerder v1.12.0 — notes for the next train

**Status:** **Notes.** Written 2026-10-08. Branch not cut. **v1.11.0** on `v1.11.0-dev` is still the active train.  
**Package / image version:** stays **`1.10.0`** until a later freeze. This file does not bump it.  
**Theme:** two notes, plus the two Discover items moved off v1.11  
**QA:** [QA_v1.12.0.md](QA_v1.12.0.md) (notes — **not** a walk yet)  
**Related:** [PLAN_v1.11.0.md](PLAN_v1.11.0.md) · wiki [Jobs, audit, notifications](../wiki/day-to-day/jobs-audit-notifications.md) · wiki [API tokens](../wiki/operations/api-tokens.md)

> Written while v1.11 is still open. Nothing here is built. The branch `v1.12.0-dev` is not cut.

---

## 1. Decision lock

| Topic | Lock |
|-------|------|
| Note | **Audit API and MCP reads.** An optional flag. Off unless the operator turns it on. When it is on, a `read` call from an API token or from hosted `/mcp` writes an audit row. Writes stay audited either way. The row stores the token and the client IP, and it does not store a secret |
| Note | **Host facts clutter.** The 15-minute `host_facts` snapshot writes a Jobs row and an Audit row for every host. Both views are messy. The default Jobs list and the default Audit list leave that routine snapshot out. A failed snapshot stays visible |
| Discover, moved from v1.11 | **NPM CRUD.** A note only. Proxy hosts stay read-only. Move can still retarget a backend |
| Discover, moved from v1.11 | **Remote restore.** A note only. Restore stays a reverse rsync from `/backups`. Drive, OneDrive, and the NAS are not a restore source |
| Version bump | Not this file. v1.11 freezes to `1.11.0` only when asked. v1.12 stays unopened |
| Demo | Do not point the public demo at a branch that does not exist |

---

## 2. Capture log

| Date | What changed |
|------|----------------|
| 2026-10-08 | **Notes opened.** NPM CRUD and remote restore move here from v1.11. Two new notes: an optional flag for API and MCP read audit, and `host_facts` kept out of the default Jobs and Audit lists. Nothing is built. |

---

## 3. Next

| # | Step | Status |
|---|------|--------|
| 1 | Keep these as notes until v1.11 ships and someone asks to open `v1.12.0-dev` | **Notes only** |
