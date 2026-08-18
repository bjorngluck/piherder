# PiHerder v1.2.0 — operator QA / sign-off

**Branch:** `v1.2.0-dev` → `main` · tag **`v1.2.0`**  
**Code freeze:** feature-complete (Streams I / S / W / D / B / R).  
**Hub / `latest`:** **1.2.0** (`1.2` / `latest`).

Use this list as a **real operator pass**, not a unit-test dump. Tick **Pass / Fail / N/A**. Anything **Must** that fails is a ship blocker unless you explicitly accept it.

Operator-facing copy (same checklists): [wiki/operations/qa-v1.2.0.md](../wiki/operations/qa-v1.2.0.md).

---

## How to run this

| | |
|--|--|
| **Instance** | Your rebuilt 1.2 stack (this host), not Hub `latest` |
| **Browsers** | Desktop Chrome or Firefox **and** one phone (console + passkeys) |
| **Accounts** | One **admin**, one **operator** (2FA enrolled), one **viewer** |
| **Hosts** | At least one real SSH host; optional second host for multi-console |
| **Optional** | IdP (Authentik / Keycloak / Authelia) if you will ship SSO as “tested” |

Record: date · who · URL · image/commit (`docker compose exec web cat /app/app/version_info.py` or About).

Legend: **Must** = ship blocker · **Should** = fix or accept in notes · **Reg** = 1.1 behaviour that must not regress.

---

## 0. Boot, install, upgrade

**Operator sign-off:** 2026-08-18 — **Pass**.

| # | Pri | Test | Pass |
|---|-----|------|------|
| 0.1 | Must | Stack healthy: `web`, `db`, `redis`, `celery-worker`, `caddy`. About / footer shows **1.2.0-dev** (or the freeze version string). | ☐ |
| 0.2 | Must | `SECRET_KEY` is a long random. Web **starts**. | ☐ |
| 0.3 | Must | Temporarily set `SECRET_KEY` to `change-me-in-prod` (or empty/short). Web **refuses to boot** (`SystemExit`) unless `PIHERDER_ALLOW_INSECURE=true` or `DEMO_MODE`. Restore a real key after. | ☐ |
| 0.4 | Must | `curl` from another LAN host to `:8000` **fails** (bound `127.0.0.1` only). UI is via Caddy `:8888` / `:8443`. | ☐ |
| 0.5 | Must | Alembic applied, including **`039_ssh_hostkey_pin`**. No migration error in `web` logs. | ☐ |
| 0.6 | Must | Hard-refresh: layout/theme look normal (compiled Tailwind — no Play CDN, no “styles missing”). Light **and** dark. | ☐ |
| 0.7 | Must | After upgrade: **Settings → PiHerder backup → Full DR** succeeds; archive contains `database.dump`. Copy off-box. | ☐ |
| 0.8 | Should | `PIHERDER_PUBLIC_URL` matches the URL in the address bar (scheme + host + port). | ☐ |

---

## 1. Passkeys / 2FA (Stream I)

| # | Pri | Test | Pass |
|---|-----|------|------|
| 1.1 | Must | Account → **Add passkey**. Browser prompt completes. Passkey listed with nickname. | ☐ |
| 1.2 | Must | Sign out → password → **Use passkey** → dashboard. | ☐ |
| 1.3 | Must | TOTP still works on an account that has it. | ☐ |
| 1.4 | Must | **Generate new backup codes**: URL does **not** contain codes. Codes appear once on the page after POST. | ☐ |
| 1.5 | Must | **Force 2FA** on: user with neither TOTP nor passkey hits `/auth/force-2fa` after password. | ☐ |
| 1.6 | Must | Force 2FA is satisfied by **passkey only** (no TOTP). | ☐ |
| 1.7 | Should | Revoke a passkey (password required). That credential can no longer sign in. | ☐ |
| 1.8 | Should | **Trust this device** skips 2FA on next login. **Sign out does not clear trust** (by design). Revoke on Account clears it. | ☐ |
| 1.9 | Should | LAN **HTTP** (not localhost) passkey register **fails** with a clear RP/origin message. | ☐ |
| 1.10 | Reg | Password policy still enforced (10+ / mixed case / digit). | ☐ |

---

## 2. SSO / OIDC (Stream S)

Skip the whole section with **N/A** only if you will not enable SSO in production. Lab notes: [SSO_AUTHENTIK_TEST.md](SSO_AUTHENTIK_TEST.md).

| # | Pri | Test | Pass |
|---|-----|------|------|
| 2.1 | Must | Settings → SSO: save issuer / client / secret. Login shows **Continue with {name}**. | ☐ |
| 2.2 | Must | Redirect URI in IdP is exactly `{PIHERDER_PUBLIC_URL}/auth/oidc/callback`. Happy-path SSO login works. | ☐ |
| 2.3 | Must | After SSO, enrolled 2FA still prompts (IdP MFA is **not** a substitute). | ☐ |
| 2.4 | Must | JIT: unknown email creates a user with mapped role (or default **viewer**), password login off. | ☐ |
| 2.5 | Must | Auto-link: existing user with **verified** same email links. Unverified / missing `email_verified` does **not** auto-link when require-verified is on. | ☐ |
| 2.6 | Must | Group → role map: operator group → operator; unmatched → default viewer. Sole admin is not demoted to zero admins. | ☐ |
| 2.7 | Must | **Require SSO on:** password form **hidden**. Non-admin `POST /auth/login` **rejected**. **Admin password login still works** (break-glass). | ☐ |
| 2.8 | Must | Account → Link / Unlink. Unlink of an SSO-only user **requires setting a password** in the same form. | ☐ |
| 2.9 | Must | Remove password after a good link → password login fails for that user; SSO still works. | ☐ |
| 2.10 | Should | Failed SSO (wrong secret / cancel) leaves a clear error and an audit `sso_login_failed`. | ☐ |
| 2.11 | Should | Explicit Account link URL has **no** `?ok=1` (no open-redirect / cache leak). | ☐ |
| 2.12 | Should | IdP down: admin password still signs in; host [recover-admin](../wiki/troubleshooting/locked-out.md) still works. | ☐ |

---

## 3. Web SSH console (Stream W)

Enable `PIHERDER_SSH_CONSOLE=true` only for this section, then decide whether production keeps it on.

| # | Pri | Test | Pass |
|---|-----|------|------|
| 3.1 | Must | Flag **off**: Console actions hidden / denied. | ☐ |
| 3.2 | Must | Flag **on**, **viewer**: Console **403** / no live PTY. | ☐ |
| 3.3 | Must | **Operator** with 2FA: Server detail → Console popup → passkey or TOTP → **+ Shell** → real prompt on the host. | ☐ |
| 3.4 | Must | Backup codes **rejected** for console step-up (default). | ☐ |
| 3.5 | Must | Logout while a shell is open (or parked): session dies; PTY gone within ~`REVALIDATE_SEC`. | ☐ |
| 3.6 | Must | Ticket is **single-use** (refresh / replay of the same open ticket fails). | ☐ |
| 3.7 | Must | Multi-host `/console`: switch host tabs; inactive host keeps the shell (or parks and resumes). | ☐ |
| 3.8 | Should | App-switch / hide tab: shell **resumes** (soft park). Typing `exit` / ✕ ends the slot. | ☐ |
| 3.9 | Should | Concurrent cap: 5th shell (default max 4) is refused clearly. | ☐ |
| 3.10 | Should | Cross-site: request mint with `Sec-Fetch-Site: cross-site` is **denied**. | ☐ |
| 3.11 | Should | Desktop Tab completion works. **Mobile soft Tab** (v12): `cd do` + Tab → `cd docker/` without a leftover `do`. Residual IME oddities: Space → Backspace → Tab. | ☐ |
| 3.12 | Should | Public demo console is **simulated** (banner; `help` / `ls` only; no live SSH). | ☐ |

---

## 4. Host SSH, host keys, default user (R7 / R11)

**Operator sign-off:** 2026-08-18 — **Pass**.

| # | Pri | Test | Pass |
|---|-----|------|------|
| 4.1 | Must | **Add server** wizard: default SSH user is **`pi`**. Existing hosts still show their previous user. | ☐ |
| 4.2 | Must | First **Test connection** on a host **pins** the key (fingerprint visible under SSH access). | ☐ |
| 4.3 | Must | After pin, a **wrong host key** (or you spoof / restore a different machine at the same IP) **refuses** connect. Message is understandable. | ☐ |
| 4.4 | Must | **Reset host-key pin** (SSH access) after a real rebuild → next Test connection succeeds and re-pins. | ☐ |
| 4.5 | Must | Viewer cannot open live diagnostics / console / compose **build**. Cached inventory / job history still readable. | ☐ |
| 4.6 | Reg | Key deploy → Test → **Clear stored password** still works. | ☐ |

---

## 5. Docker compose build (R3)

**Operator sign-off:** 2026-08-18 — **Pass**.

| # | Pri | Test | Pass |
|---|-----|------|------|
| 5.1 | Must | **Build** a named compose project as operator: stream works. | ☐ |
| 5.2 | Must | Viewer **cannot** start a build stream. | ☐ |
| 5.3 | Should | GET `/servers/{id}/docker/build-stream` (no POST / no project) is **rejected** (no path-injection fallback). | ☐ |
| 5.4 | Reg | Deploy / stop / start / restart / logs / full editor still work. | ☐ |

---

## 6. Sessions, logout, reset URLs (R2 / R4 / R5)

**Operator sign-off:** 2026-08-18 — **Pass**.

| # | Pri | Test | Pass |
|---|-----|------|------|
| 6.1 | Must | Sign out → other open tab / refresh is **logged out** (`session_version` bump). | ☐ |
| 6.2 | Must | Admin **Sign out sessions** on Users kicks that user immediately. | ☐ |
| 6.3 | Must | Forgot-password email (SMTP on): link host is **`PIHERDER_PUBLIC_URL`**, not a spoofed `Host` / `X-Forwarded-Host`. | ☐ |
| 6.4 | Should | Trusted-device cookie still works after logout (by design). Revoke on Account. | ☐ |

---

## 7. Delete graphs (R6)

**Operator sign-off:** 2026-08-18 — **Pass** (partial coverage; accepted).

| # | Pri | Test | Pass |
|---|-----|------|------|
| 7.1 | Must | Delete a **non-admin** user who has passkey + TOTP + SSO link + pin + push: **no 500**. User gone. Audit / notifications remain (unlinked). API tokens they created still exist until revoked. | ☐ |
| 7.2 | Must | Cannot delete yourself or the last admin. | ☐ |
| 7.3 | Must | Remove a **server** (type exact name): UI gone; jobs/audit remain unlinked; LAN device stays (link cleared); **host disk untouched**. | ☐ |
| 7.4 | Should | Server with cert target + map edge + template deployment deletes without 500. | ☐ |

---

## 8. Self-backup, archive paths, vanished rsync (B / R9)

**Operator sign-off:** 2026-08-18 — **Pass**.

| # | Pri | Test | Pass |
|---|-----|------|------|
| 8.1 | Must | **Full DR** run: archive has `database.dump`; manifest `kind=pg_dump_full` / format v6. | ☐ |
| 8.2 | Must | **Config only** run still produces a light JSON pack (not a substitute for Full). | ☐ |
| 8.3 | Must | Restore Full (lab or dry-run): same `PIHERDER_MASTER_KEY`; login + hosts + job history present. | ☐ |
| 8.4 | Must | Download / restore **rejects** a path outside the herder backup root (`../` etc.). | ☐ |
| 8.5 | Should | Busy-source backup (or known vanish path): retry happens; soft-OK can mark success (defaults). | ☐ |
| 8.6 | Should | Viewer / operator **cannot** run or restore herder self-backup. | ☐ |

---

## 9. CSP / frontend (R14)

**Operator sign-off:** 2026-08-18 — **Pass**.

| # | Pri | Test | Pass |
|---|-----|------|------|
| 9.1 | Must | Response CSP has **no** `unsafe-eval`. `connect-src` has **no** bare `ws:` / `wss:` token (origin `wss://your.host` is OK). | ☐ |
| 9.2 | Must | Dashboard, Servers, Docker, Settings, Account, Console chrome all render (no unstyled collapse). Desktop **and** a phone width. | ☐ |
| 9.3 | Should | `PIHERDER_CSP=true` (default). Console iframe only same-origin. | ☐ |
| 9.4 | Should | Turnstile (if configured) still loads. | ☐ |

Quick check:

```bash
curl -sI https://YOUR_PUBLIC_URL/ | tr ',' '\n' | grep -i content-security
```

---

## 10. Public demo (Stream D)

**Operator sign-off:** 2026-08-18 — **Pass**.

Run against [piherder-demo.hacknow.info](https://piherder-demo.hacknow.info) **or** a local `DEMO_MODE` overlay.

| # | Pri | Test | Pass |
|---|-----|------|------|
| 10.1 | Must | Shared viewer logs in; **demo banner** visible; wiki password works. | ☐ |
| 10.2 | Must | Cannot add a real server / deploy a key / create an API token. | ☐ |
| 10.3 | Must | `/docs` · `/redoc` · `/openapi.json` → **404**. | ☐ |
| 10.4 | Must | Console (if opened) is **simulated**. | ☐ |
| 10.5 | Should | Audit IPs for visitor actions show `redacted` (column **and** details body — console `ip=…`). | ☐ |
| 10.6 | Should | Canned job click succeeds without touching a real host. | ☐ |

---

## 11. RBAC + core fleet regression

**Operator sign-off:** 2026-08-18 — **Pass** (not every item fully validated; accepted).

| # | Pri | Test | Pass |
|---|-----|------|------|
| 11.1 | Reg | **Viewer:** read dashboard / servers / jobs / docker inventory; **cannot** start backup, patch, deploy, Users, Settings mutate. | ☐ |
| 11.2 | Reg | **Operator:** fleet jobs work; **cannot** Users / herder restore / SSO settings / API tokens. | ☐ |
| 11.3 | Reg | **Admin:** Users recovery, Force 2FA, SSO, Full DR, Status. | ☐ |
| 11.4 | Reg | One host: **Test connection**, manual **backup**, **OS update check** (or HAOS `ha` check). Job + audit rows appear. | ☐ |
| 11.5 | Reg | Docker inventory + container logs (signed-in). | ☐ |
| 11.6 | Should | Template catalog still lists; deploy wizard opens (do not have to apply). | ☐ |
| 11.7 | Should | Network maps / Path map open. | ☐ |
| 11.8 | Should | Notifications bell + (if HTTPS) Web Push toggle still works. | ☐ |
| 11.9 | Should | First-register-on-empty-DB story unchanged on a throwaway compose project. | ☐ |
| 11.10 | Should | **Direct TLS (no NPM):** a container that serves HTTPS on the **host FQDN** (e.g. Frigate on `rpi5-4…`). Stack → **Use \<project\> for this path**. Hosts map shows the app satellite + container; path is **not** via NPM even if NPM still lists the old proxy host. Kuma bind scoped to that Docker project. | ☐ |

---

## 12. Accept as known (do **not** fail the freeze)

| Item | Stance |
|------|--------|
| **KI-console-mobile-soft-tab** | Improved in QA (soft-key **v12**); residual IME may remain |
| CSP `unsafe-inline` | 1.3 nonces |
| XSS on herder origin ≈ shell if console on | Leave flag **off** when unused; prefer HTTPS + 2FA |
| Trusted-device cookies survive logout | Documented |
| 7-day session JWT | Documented (`ACCESS_TOKEN_EXPIRE_MINUTES`) |
| No per-host ACL | 1.3 **AC-fg** |
| Screenshots may still be 1.1 | Refresh shortly (accepted for freeze; not a code blocker) |

---

## Sign-off

| Gate | Name | Date | Result |
|------|------|------|--------|
| Identity (I + S) | | 2026-08-18 | **Pass** — SSO (Authentik) + 2FA / passkeys |
| Console (W) | | 2026-08-18 | **Pass** |
| Sessions / logout / reset URLs (R2 / R4 / R5) | | 2026-08-18 | **Pass** |
| Delete graphs (R6) | | 2026-08-18 | **Pass** (partial; accepted) |
| Host keys + default user (R7 / R11) | | 2026-08-18 | **Pass** |
| Docker compose build (R3) | | 2026-08-18 | **Pass** |
| Self-backup Full DR (B / R9) | | 2026-08-18 | **Pass** |
| CSP / frontend (R14) | | 2026-08-18 | **Pass** |
| Demo (D) | | 2026-08-18 | **Pass** |
| Core fleet regression | | 2026-08-18 | **Pass** (not every item; accepted) |
| Boot / install / upgrade | | 2026-08-18 | **Pass** |
| Docs / wiki / screenshots | | | Screenshot refresh pending |
| **Ready to tag `v1.2.0`** | | | **No** — screenshot refresh (non-blocker; say Yes when you want the tag) |

### After **Yes** (maintainer — not part of operator browsing)

1. ~~Bump `app/version_info.py` + `pyproject.toml` → `1.2.0`.~~ **Done.**  
2. ~~Flip this file + [RELEASE_v1.2.0.md](RELEASE_v1.2.0.md) + wiki Home to **Tagged / current production**.~~ **Done.**  
3. Merge `v1.2.0-dev` → `main` · tag `v1.2.0` · Hub `1.2.0` / `1.2` / `latest`.  
4. Refresh marketing screenshots if you have capacity.  
5. Do **not** start 1.3 work on this tag.

---

## Related

- [RELEASE_v1.2.0.md](RELEASE_v1.2.0.md)  
- [PLAN_v1.2.0.md](PLAN_v1.2.0.md)  
- [SECURITY.md](../SECURITY.md)  
- [wiki upgrades](../wiki/operations/upgrades.md)  
