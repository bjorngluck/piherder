# PiHerder v1.2.0

**Status:** **Tagged** — current production release  
**Date:** 2026-08-18  
**Git tag:** `v1.2.0`  
**Package / image version:** `1.2.0`  
**Theme:** Identity + webshell + gated demo · backup reliability · self-backup full DB DR · security remediations  
**Baseline:** [v1.1.1](RELEASE_v1.1.1.md) · [v1.1.0](RELEASE_v1.1.0.md)  
**Plan:** [PLAN_v1.2.0.md](PLAN_v1.2.0.md)  
**Operator QA:** [QA_v1.2.0.md](QA_v1.2.0.md) · wiki [v1.2.0 QA / sign-off](../wiki/operations/qa-v1.2.0.md)

**Image:** [bjorngluck/piherder](https://hub.docker.com/r/bjorngluck/piherder) — multi-arch `linux/amd64` + `linux/arm64`  
**Tags:** `1.2.0` · `1.2` · `latest` (older `1.1.1` / `1.1` / `1.0.x` pins remain valid)

---

## What’s in 1.2

### Identity — passkeys (Stream I)

| Item | Behaviour |
|------|-----------|
| Second factor | WebAuthn / passkeys **after password or SSO** — not passwordless |
| Account | Register, nickname, list, revoke (password required to remove) |
| Login | After identity: **Use passkey** or TOTP / backup code |
| Force 2FA | Satisfied by a passkey **or** TOTP |
| Requirements | HTTPS (except localhost) + matching `PIHERDER_HOSTNAME` / `PIHERDER_PUBLIC_URL` |
| Limit | Up to 10 passkeys per user |

Wiki: [2FA & force 2FA](../wiki/account-security/two-factor.md).

### Identity — SSO / OIDC (Stream S)

| Item | Behaviour |
|------|-----------|
| Protocol | Authorization code + **PKCE**; confidential client; BYO IdP |
| Settings | Issuer, client id/secret (Fernet), scopes, group → role map, default **viewer** |
| Provisioning | JIT new users; auto-link by **verified** email; Account → Connected accounts |
| Password optional | Remove password after a link; unlink requires a password in the same flow |
| 2FA | IdP is first factor only — enrolled TOTP/passkey still required |
| **Require SSO** | Password form **hidden**. `POST /auth/login` **rejected for non-admins**. **Admins stay password break-glass**. |
| `email_verified` | Missing or false is **not** treated as verified (auto-link / require-verified fail closed) |

Wiki: [SSO / OpenID Connect](../wiki/account-security/sso-oidc.md). Lab: [SSO_AUTHENTIK_TEST.md](SSO_AUTHENTIK_TEST.md).

### Web SSH console (Stream W)

Optional in-browser terminal. **Default off** (`PIHERDER_SSH_CONSOLE=false`).

| Control | Behaviour |
|---------|-----------|
| RBAC | **operator+** only (viewer **403** except public demo simulated shell) |
| Step-up | Passkey preferred; TOTP accepted; **backup codes rejected** unless env allows |
| Grant | Fleet-wide (~10 min) unless every-shell 2FA |
| Tickets | Single-use; first WS message only (not query string); Redis NX |
| Bindings | `session_version` + optional IP + device cookie; revalidate ~10s |
| Resume | Unexpected WS drop parks the PTY; logout / password change destroys parked shells |
| UI | Popup per host + multi-host `/console`; compact chrome; sticky Ctrl |
| CSP | Same-origin iframe only; compiled Tailwind (no `unsafe-eval`); `connect-src` is `'self'` + public origin / its `wss:` (no wildcard `ws:`/`wss:`) |

Wiki: [Web SSH console](../wiki/day-to-day/web-ssh-console.md).

### Public demo (Stream D)

`PIHERDER_DEMO_MODE` on a dedicated VPS — [wiki demo](../wiki/operations/demo-site.md) · [DEMO_SITE.md](DEMO_SITE.md).

- Shared **viewer**; write guard; canned jobs; no real onboard / API tokens  
- Console is **simulated** (no Paramiko / TCP)  
- No in-app RESET (ops CLI / cron only)  
- Audit visitor IPs stored as `redacted`  
- Unique Fernet / session secrets — never production host keys  

### Backup reliability (Stream B)

| Item | Behaviour |
|------|-----------|
| **B-retry** | Auto-retry rsync vanish (code 24 / partial 23); optional **soft-OK** (`PIHERDER_BACKUP_VANISHED_*`) |
| **B-DR** | Self-backup **Full** = `pg_dump -Fc` of entire Postgres + `DATA_ROOT` files (format **v6**). Config-only stays a light JSON pack. |

Wiki: [Self-backup & DR](../wiki/operations/self-backup.md) · [vanished files](../wiki/troubleshooting/backups.md#vanished-files-busy-sources).

### Network maps — direct TLS (QA-period)

Landed on `v1.2.0-dev` during operator QA. **Not** a `v1.1.1` Hub patch — stays on this train.

When a container terminates TLS itself and DNS points at the **host** (not NPM), Hosts / Path maps can now keep docker project + container + Kuma together.

| Item | Behaviour |
|------|-----------|
| Leftover NPM | If the CNAME/A target **is** the backend host, stale NPM proxy-host inventory is **not** treated as the path edge |
| Kuma URL match | A service bind whose monitor URL is the published host FQDN attaches the compose project (e.g. Frigate on `rpi5-4.example.com`) |
| **Use this project** | Stack panel **persists** `docker_project` on the path (preview chips no longer silently drop the link) and marks the path **direct** |
| Hosts map | Linked project shows as an app satellite + stack fan; unlinked host-identity paths stay host-only |

Wiki: [Network maps — Direct TLS](../wiki/integrations/dns-fabric.md#direct-tls-no-npm). QA: [11.10](QA_v1.2.0.md).

---

## Security remediations (Stream R)

Landed after the 1.2 deep review. Not new product surfaces — they close holes that already existed.

| ID | Change |
|----|--------|
| **R1** | App port **`127.0.0.1:8000`** only. `X-Forwarded-For` / `CF-Connecting-IP` honoured **only** when the TCP peer is in `PIHERDER_TRUSTED_PROXY_CIDRS`. Compose default trusts RFC1918 + loopback so bundled Caddy is trusted. |
| **R2** | Email password-reset links are built from **`PIHERDER_PUBLIC_URL` only** (Host / `X-Forwarded-Host` ignored). |
| **R3** | Compose **build** is **POST-only**, named project, `shlex.quote` paths — no raw `/path` GET fallback. |
| **R4** | Logout **bumps `session_version`** (stolen JWTs die) and destroys parked consoles. |
| **R5** | New 2FA backup codes travel in an **HttpOnly flash cookie**, not the query string. |
| **R6** | User and server **delete graphs** cover 1.2 children (passkeys, OIDC links, pins, cert targets, map edges, …). History rows stay, unlinked. |
| **R7** | New hosts default SSH user **`pi`**. Existing rows are **not** migrated. |
| **R8** | **Require SSO** is enforced (see Stream S). |
| **R9** | Herder archive download / restore paths are **confined** to the backup root. |
| **R10** | Docs / wiki honesty pass (this train). |
| **R11** | SSH **host-key pin (TOFU)**: first successful connect stores the key; later mismatch **refuses**. Reset under **SSH access** after a rebuild. |
| **R12** | OIDC GET link has **no `?ok=1`**. Viewer cannot live-SSH. `Sec-Fetch-Site: cross-site` console mint denied. |
| **R13** | Console JTI consume is **NX / never wipe-all**. Weak / default **`SECRET_KEY` refuses boot** unless `PIHERDER_ALLOW_INSECURE=true` or `DEMO_MODE`. |
| **R14** | Tailwind is **compiled CSS** (`scripts/build-tailwind.sh`, committed `app/static/css/tailwind.css`). CSP **`unsafe-eval` removed**. |

Honest residual (not freeze-blocking): template `<script>` still needs **`unsafe-inline`** (nonces in **1.3**). XSS on the herder origin is still **shell-equivalent** when the console flag is on. Trusted-device cookies **survive logout** by design. Sessions are 7-day JWTs. Roles remain three global roles (per-host ACL is 1.3).

---

## Self-backup / disaster recovery

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

## Upgrade from 1.1.x

Operator checklist: [wiki upgrades — 1.1 → 1.2](../wiki/operations/upgrades.md#11--12).

1. Take a **1.1** self-backup and keep it offline with `PIHERDER_MASTER_KEY`.  
2. `git fetch --tags && git checkout v1.2.0` (or pull `bjorngluck/piherder:1.2.0`).  
3. Confirm `.env` has a **long random `SECRET_KEY`** (web **will not boot** on the compose default unless `PIHERDER_ALLOW_INSECURE=true`).  
4. `docker compose pull && docker compose up -d` — Alembic includes **`039_ssh_hostkey_pin`**.  
5. Run **Settings → PiHerder backup → Full DR** once; copy the archive off-box; set schedule to **Full**.  
6. **Test connection** once per host to **pin** the SSH host key.  
7. Set `PIHERDER_PUBLIC_URL` if you use email password reset or SSO.  
8. Hard-refresh the browser (compiled CSS is query-busted).  

SSO / passkeys / console / demo mode are **opt-in** — they do not turn on by themselves.

---

## QA-period fixes (landed on this tag)

Operator QA on Authentik + this host. Not extra streams — they belong in **1.2.0**.

| Fix | Detail |
|-----|--------|
| Authentik `iss` slash | ID token `iss` includes a trailing slash; Settings stored it stripped. Both forms accepted. |
| Sole-admin role sync | Still not demoted. Audit **`user_role_sync_skipped`**. |
| SSO error copy | Token/email failures are no longer shown as “cancelled or denied”. |
| Account cards | Stray `<ul>` discs on Connected accounts / Passkeys (Preflight off). |
| Settings layout | Self-backup schedule/run left-aligned; timezone stacks on mobile. |
| OpenAPI UI | `/docs` and `/redoc` were blank under CSP; path-scoped CDN allow. |
| Screenshots | 1.2 pack: login SSO, Settings SSO, Account SSO/passkeys, console popup, Full DR. |

---

## Known issues (ship with awareness)

| ID | Topic | Notes |
|----|--------|--------|
| **KI-console-mobile-soft-tab** | Web console soft **Tab** on mobile | **Improved in 1.2 QA (v12):** flush IME last token, rewrite `cd.do` / `cd .do`, swallow compositionend, drop matching re-append after bash completes. Desktop unchanged. Residual exotic IMEs: Space → Backspace → Tab. Wiki: [web SSH](../wiki/day-to-day/web-ssh-console.md#known-issues). |
| **KI-csp-unsafe-inline** | CSP | **`unsafe-eval` closed** (compiled Tailwind). Inline script/style remain for template `<script>` / xterm — nonces in **v1.3**. Residual: XSS on the herder origin is still shell-equivalent when console is enabled. `/docs` and `/redoc` allow jsDelivr + Google Fonts **only on those paths** so stock FastAPI Swagger/ReDoc can load. |
| **KI-account-stepup-factors** | Account SSO unlink / passkey revoke | Unlink form prefers **TOTP or backup codes** when TOTP is enrolled (passkey works after Account step-up, or if TOTP is off). Passkey **Revoke** requires the **local password**, not TOTP/another passkey. **v1.3:** one step-up helper — any enrolled factor; password only when no 2FA. |
| **KI-ssh-hostkey-tofu** | SSH host keys | **Closed:** first connect pins; mismatch refuses; reset under SSH access. |

---

## What 1.2 is not

These stay on [PLAN_v1.3.0.md](PLAN_v1.3.0.md) — **do not expect them in this tag**:

- Per-host / per-feature roles  
- Configurable password / 2FA / console timeouts in Settings  
- Multi-identity host SSH + command-level shell audit  
- CSP nonces (drop `unsafe-inline`)  
- Passwordless passkeys · SAML · session recording  

---

## Related

- [wiki/operations/self-backup.md](../wiki/operations/self-backup.md)  
- [wiki/operations/qa-v1.2.0.md](../wiki/operations/qa-v1.2.0.md)  
- [ADMIN.md](ADMIN.md)  
- [SECURITY.md](../SECURITY.md)  
- [PLAN_v1.2.0.md](PLAN_v1.2.0.md)  
