# PiHerder v1.2.0

**Status:** **In development** on `v1.2.0-dev` (not yet tagged)  
**Theme:** Big identity + webshell + gated demo · backup reliability · **self-backup full DB DR**  
**Baseline:** [v1.1.0](RELEASE_v1.1.0.md)  
**Plan:** [PLAN_v1.2.0.md](PLAN_v1.2.0.md)

---

## Self-backup / disaster recovery (operators)

### Upgrade note for anyone on **&lt; v1.2.0**

Self-backup archives from **v1.0.x** and **v1.1.x** are **control-plane JSON packs**, not complete database dumps. Do **not** treat a pre-1.2 “full” `.tar.gz` as the only copy of job history or unbounded audit.

Documented as **KI-self-backup-not-full-db** on [RELEASE_v1.1.0.md](RELEASE_v1.1.0.md). Full table: [wiki Self-backup & DR — limitations before v1.2.0](../wiki/operations/self-backup.md#limitations-before-v120).

| Pre-v1.2 “Full” self-backup | Reality |
|-----------------------------|---------|
| Mechanism | JSON row snapshots + small files under `DATA_ROOT` |
| **Jobs** | **Never** included or restored |
| **Audit** | Optional “full” mode only, **capped** (~thousands of rows) |
| **Notifications** | **Capped** recent window |
| **Nmap scan runs** | **Excluded** (devices/schedules only in later JSON formats) |
| Host **rsync** trees | Always out of scope (separate product) |
| Sole DR after hard wipe? | **No** — fleet identity/secrets yes; full historical DB **no** |

### What v1.2.0 changes

| Mode | v1.2.0 behaviour |
|------|------------------|
| **Full DR** (recommended schedule default) | **`pg_dump -Fc` of the entire Postgres database** (`database.dump` in the archive) + avatars/logos · format **v6** · `kind=pg_dump_full` · restore via **`pg_restore`** |
| **Config only** | Light JSON control-plane snapshot — **not** sole DR |

Image includes **`postgresql-client-16`** (matches compose `postgres:16`). Same **`PIHERDER_MASTER_KEY`** still required for Fernet-encrypted fields after restore.

**Action after upgrading to 1.2:** run **Full DR** once, copy the archive off-box, set schedule mode to **Full**. Keep pre-1.2 archives only as historical control-plane packs.

---

## Other 1.2 themes (summary — expand at freeze)

- WebAuthn / passkeys as second factor  
- SSO / OIDC  
- Web SSH console (flag default off)  
- Gated public demo (`DEMO_MODE`, shared **viewer**, write guard)  
- Backup vanished-file retry (B-retry)  

Full freeze wording lands when the train tags.

---

## Known issues (ship with awareness)

| ID | Topic | Notes |
|----|--------|--------|
| **KI-console-mobile-soft-tab** | Web console soft **Tab** on mobile | Desktop physical + soft Tab OK. On mobile browsers, soft Tab can leave IME mid-token or re-append the short fragment after bash path completion (`docker/do`, `piherder/pi`). **Workaround:** Space → Backspace → soft Tab, or use a physical keyboard. Path completion also depends on the **remote SSH user** home layout. Parked (not freeze-blocking). Wiki: [web-ssh-console § Known issues](../wiki/day-to-day/web-ssh-console.md#known-issues). |
| **KI-ssh-hostkey-tofu** | SSH host keys | **Closed on `v1.2.0-dev`:** first connect pins; mismatch refuses; reset under SSH access. |
| **KI-csp-unsafe-eval** | CSP | Policy is on, but Tailwind Play still needs `script-src 'unsafe-inline' 'unsafe-eval'`. XSS on the herder origin remains shell-equivalent when console is enabled. |

### Stream R — freeze remediations (landed on `v1.2.0-dev`)

Trusted-proxy client IP + loopback `:8000` · reset URLs from `PIHERDER_PUBLIC_URL` · POST-only quoted compose build · logout bumps `session_version` · backup codes out of the query string · user/server delete graphs · default SSH user `pi` · require-SSO + strict `email_verified` · confined herder archive paths.

---

## Related

- [wiki/operations/self-backup.md](../wiki/operations/self-backup.md)  
- [ADMIN.md](ADMIN.md) § PiHerder self-backup  
- [PLAN_v1.2.0.md](PLAN_v1.2.0.md) stream **B-DR**  
