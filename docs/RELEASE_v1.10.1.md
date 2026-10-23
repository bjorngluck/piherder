# PiHerder v1.10.1

**Status:** Patch
**Date:** 2026-10-23
**Git tag:** `v1.10.1`
**Package / image version:** `1.10.1`
**Baseline:** `v1.10.0`
**Theme:** Patch. certifi lock refresh

**Prior:** [RELEASE_v1.10.0.md](RELEASE_v1.10.0.md)
**Docs:** https://piherder-docs.hacknow.info/

**Image:** [bjorngluck/piherder](https://hub.docker.com/r/bjorngluck/piherder) multi-arch `linux/amd64` + `linux/arm64`
**Tags:** `1.10.1` and `1.10`. `latest` stays on **1.12.1**. The manifest digest is filled in after the image is published. Pin `1.10.0` stays that patch. Plugin **[0.5.0](https://github.com/bjorngluck/piherder-ha/releases/tag/v0.5.0)** and adapter **[0.3.1](https://github.com/bjorngluck/piherder-mcp/releases/tag/v0.3.1)** stay the companions from v1.10.0.

---

## Why this release

**v1.10.0** locked **certifi 2026.6.17**. That pin is inside [GHSA-6q48-6c7w-9gw2](https://github.com/advisories/GHSA-6q48-6c7w-9gw2). certifi before 2026.10.19 trusts emSign roots whose intermediate keys were compromised. An on-path attacker can present a certificate the bundle accepts.

**v1.10.1** refreshes the three lockfiles on the 1.10 line. No other dependency moved.

| Package | Was | Now |
|---------|-----|-----|
| certifi | 2026.6.17 | **2026.10.19** |

No schema change. No new environment variable. Recreate **web** and **celery-worker**.

---

## Upgrade (from v1.10.0)

1. Self-backup first.
2. `docker compose pull && docker compose up -d` with image `bjorngluck/piherder:1.10.1`, or `1.10` after this publish.
3. Confirm About / footer says **1.10.1**.
