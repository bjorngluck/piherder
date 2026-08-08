# PiHerder v1.1.0

**Status:** **Draft** — freeze / tag planned (end of QA on `v1.1.0-dev`)  
**Git branch:** `v1.1.0-dev` → merge `main` → tag `v1.1.0`  
**Package / image version (at tag):** `1.1.0`  
**Baseline:** `v1.0.0` (first production)  
**Theme:** Elevate production — certs · discovery · identity · operator UX · topology/maps · integrations/API  

**Plans:** [PLAN_v1.1.0.md](PLAN_v1.1.0.md) · next train [PLAN_v1.2.0.md](PLAN_v1.2.0.md)  
**Prior:** [RELEASE_v1.0.0.md](RELEASE_v1.0.0.md)  
**Roadmap:** [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md)

**Image (at publish):** [bjorngluck/piherder](https://hub.docker.com/r/bjorngluck/piherder) — multi-arch  
**Tags (at publish):** `1.1.0` · `1.1` · `latest` (keep `1.0` / `1.0.x` pins valid)

---

## Highlights (summary)

Operator-facing elevation on the v1.0 production base. Full stream detail lives in [PLAN_v1.1.0.md](PLAN_v1.1.0.md); fill this section at freeze from `git log v1.0.0..v1.1.0`.

- **Certs** — deploy-target wizard, verify, sudoers alignment  
- **LAN Discovery** — last-seen, hide, purge, filters  
- **Identity / alerts** — trusted-device detail, SMTP + forgot password (lite), webhook UI  
- **Operator UX** — human cron, favourites, host jump  
- **Maps / topology** — ports, edges, map interactivity (icons, focus pop-out, progressive host ports)  
- **Integrations / API** — generic URL links, API try / ReDoc polish  

---

## Known issues (ship with awareness)

Accepted for **v1.1.0** — not blocking tag. Tracked for **v1.2** (or later) unless noted.

| ID | Area | Issue | Destination |
|----|------|--------|-------------|
| **KI-rsync-vanished** | Backups | **Rsync can fail when source files disappear mid-transfer.** Busy trees (e.g. **Frigate NVR** and similar: recordings indexed, rotated, moved, or deleted while rsync walks the path) commonly produce rsync **code 23** (partial transfer) or **code 24** (vanished files). This is an **expected class of failure** on live media/NVR disks, not necessarily a PiHerder misconfiguration. Other sources in the same job may still succeed. | **v1.2+** — explore handling (excludes / volatility-aware paths, treat vanished as soft success where safe) and introduce a **retry mechanism**. See [PLAN_v1.2.0.md](PLAN_v1.2.0.md) residual **B-retry**. Operator notes: [wiki troubleshooting — Backups](../wiki/troubleshooting/backups.md#vanished-files-busy-sources) · [SSH/rsync](../wiki/troubleshooting/ssh-rsync.md#backups-rsync-code-23-partial-transfer) |

### KI-rsync-vanished — operator guidance (1.1)

1. Read the job / audit error: look for `vanished`, `code 24`, or partial **code 23** naming paths under a live media tree.  
2. Re-run the backup off-peak; a second pass often completes when the tree is quieter.  
3. Prefer backing up **stable** bind mounts (config, DB dumps) separately from high-churn recording directories when practical.  
4. Distinguish from **I/O / mount** failures (`Input/output error`, ext4 `shutdown`) — those are host disk issues, not vanished-file churn.

---

## Intentionally not in v1.1.0

| Item | Destination |
|------|-------------|
| WebAuthn / passkeys, SSO/OIDC, webshell, gated demo | **v1.2** — [PLAN_v1.2.0.md](PLAN_v1.2.0.md) |
| Backup vanished-file soft-success + retry | **v1.2+** (this RELEASE known issue) |
| ACME-in-herder | ≥ **v1.3** under consideration |

---

## Upgrade from v1.0.0

1. Self-backup (Settings) and/or volume snapshot.  
2. Keep the same **`PIHERDER_MASTER_KEY`**.  
3. Pull `1.1.0` (or build from tag), `docker compose up -d`.  
4. Smoke: login, one backup, maps, cert deploy if used, discovery filters.

Full checklist: [ADMIN.md](ADMIN.md) · wiki [Upgrades](../wiki/operations/upgrades.md).

---

## Changelog summary

Product work since `v1.0.0` is the elevation train on **`v1.1.0-dev`**.  
At tag: `git log v1.0.0..v1.1.0` · plan: [PLAN_v1.1.0.md](PLAN_v1.1.0.md).
