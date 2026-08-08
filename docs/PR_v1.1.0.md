# PR: v1.1.0-dev → main

**Open:** https://github.com/bjorngluck/piherder/compare/main...v1.1.0-dev?expand=1

**Title:** `release: v1.1.0 elevate production`

**Base:** `main` · **Head:** `v1.1.0-dev` · **Tag:** `v1.1.0` (at freeze tip)

---

## Summary

First minor after production **v1.0.0**. Elevates day-to-day ops without breaking contracts.

- **Certs** — deploy-target wizard, sudoers align, post-deploy verify, alerts  
- **LAN discovery** — last-seen, hide/purge, filter chips with honest counts  
- **Identity Cap** — trusted-device polish, SMTP + forgot password, webhook alerts  
- **Nav** — human cron, ★ pins, host jump  
- **Maps** — icons, focus pop-out, progressive host ports (desktop click fixed)  
- **Integrations / API** — generic URL links, Try a token / ReDoc  
- **Self-backup v4** — pins, API tokens, nmap inventory; transparent DR wiki  
- **Known issue** — **KI-rsync-vanished** (busy Frigate/NVR trees) → v1.2 retry  

Full notes: [RELEASE_v1.1.0.md](RELEASE_v1.1.0.md)

## Test plan

- [x] Local unit pack green (stack panel fix); coverage ≥55%  
- [x] Local `mkdocs build --strict`  
- [x] Local Playwright E2E 19 passed (e2e stack)  
- [ ] GitHub Actions: Tests · Docs · E2E on this PR  
- [ ] Smoke after merge: login, map ports desktop, cert wizard, pins, self-backup  

## After merge

```bash
# Hub multi-arch (maintainer)
export IMAGE=bjorngluck/piherder VERSION=1.1.0
# see docs/PUBLISH_IMAGE.md — tags 1.1.0 / 1.1 / latest
```

Optional: GitHub Release UI from tag `v1.1.0` using RELEASE_v1.1.0 highlights.
