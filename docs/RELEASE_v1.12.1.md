# PiHerder v1.12.1

**Status:** Patch
**Date:** 2026-10-23
**Git tag:** `v1.12.1`
**Package / image version:** `1.12.1`
**Baseline:** `v1.12.0` (2026-10-22)
**Theme:** Patch. certifi lock refresh

**Prior:** [RELEASE_v1.12.0.md](RELEASE_v1.12.0.md)
**Docs:** https://piherder-docs.hacknow.info/

**Image:** [bjorngluck/piherder](https://hub.docker.com/r/bjorngluck/piherder) multi-arch `linux/amd64` + `linux/arm64`
**Tags:** `1.12.1`, `1.12`, and `latest`. Manifest `sha256:4c88b673dfa9d94cf275487c3a6303d9113f7667c9cb43349d04c08c11d14a21`. Pin `1.12.0` stays that patch. Pin `1.11.1` / `1.11` is the previous minor image. Pin `1.10.1` / `1.10` stays valid. Plugin **[0.5.2](https://github.com/bjorngluck/piherder-ha/releases/tag/v0.5.2)**. Adapter **[0.4.3](https://github.com/bjorngluck/piherder-mcp/releases/tag/v0.4.3)**.

---

## Why this release

**v1.12.0** locked **certifi 2026.6.17**. That pin is inside [GHSA-6q48-6c7w-9gw2](https://github.com/advisories/GHSA-6q48-6c7w-9gw2). certifi before 2026.10.19 trusts emSign roots whose intermediate keys were compromised. An on-path attacker can present a certificate the bundle accepts.

**v1.12.1** refreshes the three lockfiles. No other dependency moved.

| Package | Was | Now |
|---------|-----|-----|
| certifi | 2026.6.17 | **2026.10.19** |

No schema change. No new environment variable. Recreate **web** and **celery-worker**.

---

## Upgrade (from v1.12.0)

1. Self-backup first.
2. `docker compose pull && docker compose up -d` with image `bjorngluck/piherder:1.12.1`, or `1.12` / `latest` after this publish.
3. Confirm About / footer says **1.12.1**.
