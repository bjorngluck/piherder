# v1.2.0 QA / sign-off

## What this is

The **operator freeze checklist** for **PiHerder v1.2.0**. Code on `v1.2.0-dev` is feature-complete. Hub `latest` is still **1.1.0** until this list is signed and the tag is cut.

Maintainer copy (same tests + post-tag steps): [docs/QA_v1.2.0.md](https://github.com/bjorngluck/piherder/blob/v1.2.0-dev/docs/QA_v1.2.0.md).  
What shipped: [RELEASE_v1.2.0.md](https://github.com/bjorngluck/piherder/blob/v1.2.0-dev/docs/RELEASE_v1.2.0.md).

## Why it exists

1.2 adds **passkeys, SSO, an optional web SSH console, full-DB self-backup, and a security remediation pass**. Unit tests do not replace clicking the product as you run it.

## How to run this

| | |
|--|--|
| **Instance** | Your rebuilt **1.2** stack — not the published `1.1.0` image |
| **Browsers** | One desktop **and** one phone (console + passkeys) |
| **Accounts** | **admin**, **operator** (2FA enrolled), **viewer** |
| **Hosts** | At least one real SSH host |
| **Optional** | An OIDC IdP if you will turn SSO on |

Tick **Pass / Fail / N/A**. **Must** failures block the tag unless you explicitly accept them in notes.

Legend: **Must** = ship blocker · **Should** = fix or accept · **Reg** = 1.1 behaviour that must not break.

---

## 0. Boot, install, upgrade

| # | Pri | Test | ☐ |
|---|-----|------|---|
| 0.1 | Must | Stack healthy (`web` `db` `redis` `celery-worker` `caddy`). About shows the 1.2 freeze string. | |
| 0.2 | Must | Long random `SECRET_KEY` — web **starts**. | |
| 0.3 | Must | Weak/default `SECRET_KEY` — web **refuses to boot** unless `PIHERDER_ALLOW_INSECURE` or `DEMO_MODE`. Restore a real key. | |
| 0.4 | Must | `:8000` is **loopback only** (not on the LAN). Use Caddy `:8888` / `:8443`. | |
| 0.5 | Must | Migrations include **`039_ssh_hostkey_pin`**. No Alembic error in `web` logs. | |
| 0.6 | Must | Hard-refresh: light **and** dark look styled (compiled Tailwind — no Play CDN). | |
| 0.7 | Must | **Settings → PiHerder backup → Full DR** succeeds; archive has `database.dump`. Copy off-box. | |
| 0.8 | Should | `PIHERDER_PUBLIC_URL` matches the address bar (scheme + host + port). | |

---

## 1. Passkeys / 2FA

| # | Pri | Test | ☐ |
|---|-----|------|---|
| 1.1 | Must | Account → **Add passkey** → listed with nickname. | |
| 1.2 | Must | Sign out → password → **Use passkey** → dashboard. | |
| 1.3 | Must | TOTP still works. | |
| 1.4 | Must | **New backup codes**: not in the URL; shown once after POST. | |
| 1.5 | Must | **Force 2FA** sends a user with no factor to `/auth/force-2fa`. | |
| 1.6 | Must | A **passkey-only** user satisfies Force 2FA. | |
| 1.7 | Should | Revoke passkey (password required) — that key cannot sign in. | |
| 1.8 | Should | **Trust this device** skips 2FA next time. **Sign out does not clear trust**. Revoke on Account does. | |
| 1.9 | Should | LAN HTTP (not localhost) passkey register fails clearly. | |
| 1.10 | Reg | Password policy (10+ / mixed / digit) still enforced. | |

See [2FA & force 2FA](../account-security/two-factor.md).

---

## 2. SSO / OpenID Connect

Mark the section **N/A** only if production will not enable SSO. Lab: [SSO_AUTHENTIK_TEST.md](https://github.com/bjorngluck/piherder/blob/v1.2.0-dev/docs/SSO_AUTHENTIK_TEST.md).

| # | Pri | Test | ☐ |
|---|-----|------|---|
| 2.1 | Must | Settings → SSO saved. Login shows **Continue with {name}**. | |
| 2.2 | Must | IdP redirect = `{PIHERDER_PUBLIC_URL}/auth/oidc/callback`. Happy-path login works. | |
| 2.3 | Must | Enrolled PiHerder 2FA still runs after SSO. | |
| 2.4 | Must | JIT creates a user (mapped role or default **viewer**), password off. | |
| 2.5 | Must | Auto-link only when email is **verified**. Missing `email_verified` is not verified. | |
| 2.6 | Must | Group → role map works. Last admin is not demoted away. | |
| 2.7 | Must | **Require SSO:** password form hidden; **non-admin password login rejected**; **admin password still works**. | |
| 2.8 | Must | Unlink SSO-only user **requires setting a password** in the same form. | |
| 2.9 | Must | Remove password → password login fails; SSO still works. | |
| 2.10 | Should | Failed / cancelled SSO is a clear error + `sso_login_failed` audit. | |
| 2.11 | Should | Account link URL has **no** `?ok=1`. | |
| 2.12 | Should | IdP down: admin password + [host recover](../troubleshooting/locked-out.md) still work. | |

See [SSO / OpenID Connect](../account-security/sso-oidc.md).

---

## 3. Web SSH console

Turn `PIHERDER_SSH_CONSOLE=true` only for this section.

| # | Pri | Test | ☐ |
|---|-----|------|---|
| 3.1 | Must | Flag **off** → no console. | |
| 3.2 | Must | Flag **on**, **viewer** → no live PTY. | |
| 3.3 | Must | **Operator** + 2FA → popup → **+ Shell** → real host prompt. | |
| 3.4 | Must | Backup codes **rejected** for console step-up (default). | |
| 3.5 | Must | Logout kills open **and** parked shells (within revalidate). | |
| 3.6 | Must | Open ticket is **single-use**. | |
| 3.7 | Must | Multi-host `/console`: other host tabs keep or resume shells. | |
| 3.8 | Should | App-switch resumes; `exit` / ✕ frees the slot. | |
| 3.9 | Should | 5th concurrent shell (default max 4) refused clearly. | |
| 3.10 | Should | Cross-site mint (`Sec-Fetch-Site: cross-site`) denied. | |
| 3.11 | Should | Desktop Tab OK. Mobile soft Tab may glitch — **KI-console-mobile-soft-tab** (Space → Backspace → Tab). | |
| 3.12 | Should | Public demo console is **simulated**. | |

See [Web SSH console](../day-to-day/web-ssh-console.md).

---

## 4. Host SSH, host keys, default user

| # | Pri | Test | ☐ |
|---|-----|------|---|
| 4.1 | Must | New host default SSH user is **`pi`**. Existing hosts unchanged. | |
| 4.2 | Must | First **Test connection** **pins** the host key (fingerprint on SSH access). | |
| 4.3 | Must | A different key at the same address **refuses** connect. | |
| 4.4 | Must | **Reset pin** after a rebuild → next test succeeds and re-pins. | |
| 4.5 | Must | Viewer: no live console / diagnostics / compose **build**. Cached inventory OK. | |
| 4.6 | Reg | Deploy key → Test → **Clear stored password**. | |

See [Add a server](../day-to-day/add-server.md).

---

## 5. Docker compose build

| # | Pri | Test | ☐ |
|---|-----|------|---|
| 5.1 | Must | Operator **Build** on a named project streams. | |
| 5.2 | Must | Viewer cannot start a build. | |
| 5.3 | Should | GET build-stream without POST / project is **rejected**. | |
| 5.4 | Reg | Deploy / stop / start / restart / logs / editor still work. | |

See [Docker](../docker/overview.md).

---

## 6. Sessions, logout, reset URLs

| # | Pri | Test | ☐ |
|---|-----|------|---|
| 6.1 | Must | Sign out logs out other tabs (`session_version`). | |
| 6.2 | Must | Users → **Sign out sessions** kicks that user. | |
| 6.3 | Must | Forgot-password link uses **`PIHERDER_PUBLIC_URL` only** (not a spoofed Host). | |
| 6.4 | Should | Trusted-device cookie survives logout; revoke on Account. | |

---

## 7. Delete graphs

| # | Pri | Test | ☐ |
|---|-----|------|---|
| 7.1 | Must | Delete a user with passkey + TOTP + SSO + pin + push: **no 500**. Audit kept (unlinked). | |
| 7.2 | Must | Cannot delete yourself or the last admin. | |
| 7.3 | Must | Remove server (exact name): UI gone; history unlinked; host disk untouched. | |
| 7.4 | Should | Server with cert target + map edge + template deploy deletes cleanly. | |

See [Users](../account-security/users.md) · [Remove a server](../day-to-day/remove-server.md).

---

## 8. Self-backup, archives, vanished rsync

| # | Pri | Test | ☐ |
|---|-----|------|---|
| 8.1 | Must | **Full DR** archive has `database.dump` (format v6). | |
| 8.2 | Must | **Config only** is still a light JSON pack. | |
| 8.3 | Must | Restore Full with the same master key: login, hosts, job history. | |
| 8.4 | Must | Download/restore **rejects** paths outside the backup root. | |
| 8.5 | Should | Busy-source vanish: retry / soft-OK behaves as configured. | |
| 8.6 | Should | Only **admin** can run or restore herder backup. | |

See [Self-backup & DR](self-backup.md).

---

## 9. CSP / frontend

| # | Pri | Test | ☐ |
|---|-----|------|---|
| 9.1 | Must | CSP has **no** `unsafe-eval`. No bare `ws:` / `wss:` token in `connect-src`. | |
| 9.2 | Must | Main pages render on desktop **and** phone width. | |
| 9.3 | Should | CSP on; console iframe same-origin only. | |
| 9.4 | Should | Turnstile still works if you use it. | |

```bash
curl -sI https://YOUR_PUBLIC_URL/ | tr ',' '\n' | grep -i content-security
```

---

## 10. Public demo

Against [the public demo](demo-site.md) or a local `DEMO_MODE` overlay.

| # | Pri | Test | ☐ |
|---|-----|------|---|
| 10.1 | Must | Shared viewer + banner; wiki password works. | |
| 10.2 | Must | Cannot add a host / deploy a key / create an API token. | |
| 10.3 | Must | `/docs` `/redoc` `/openapi.json` → **404**. | |
| 10.4 | Must | Console is **simulated**. | |
| 10.5 | Should | Visitor audit IPs are `redacted`. | |
| 10.6 | Should | Canned job click does not touch a real host. | |

---

## 11. RBAC + core fleet

| # | Pri | Test | ☐ |
|---|-----|------|---|
| 11.1 | Reg | Viewer reads; cannot mutate fleet / Users / Settings. | |
| 11.2 | Reg | Operator runs jobs; cannot Users / herder restore / SSO settings / tokens. | |
| 11.3 | Reg | Admin: recovery, Force 2FA, SSO, Full DR, Status. | |
| 11.4 | Reg | One host: Test connection, one backup, one OS (or HAOS) check. Job + audit. | |
| 11.5 | Reg | Docker inventory + logs. | |
| 11.6 | Should | Templates catalog / deploy wizard opens. | |
| 11.7 | Should | Network maps open. | |
| 11.8 | Should | Bell + (HTTPS) Web Push toggle. | |
| 11.9 | Should | Empty-DB first register still creates the only admin. | |

---

## 12. Accept as known (do not fail freeze)

| Item | Stance |
|------|--------|
| [KI-console-mobile-soft-tab](../day-to-day/web-ssh-console.md#known-issues) | Parked; workaround documented |
| CSP `unsafe-inline` | 1.3 nonces |
| XSS on herder origin ≈ shell if console on | Leave the flag **off** when unused |
| Trusted-device cookies survive logout | By design |
| 7-day session JWT | `ACCESS_TOKEN_EXPIRE_MINUTES` |
| No per-host ACL | Planned for 1.3 |
| Some screenshots still 1.1 | Refresh after QA (not a code blocker) |

---

## Sign-off

| Gate | Name | Date | Result |
|------|------|------|--------|
| Identity (passkeys + SSO) | | | Pass / Fail / N/A |
| Console | | | Pass / Fail / N/A (flag stays off) |
| Host keys + deletes + sessions | | | Pass / Fail |
| Self-backup Full DR | | | Pass / Fail |
| Demo | | | Pass / Fail / N/A |
| Core fleet regression | | | Pass / Fail |
| Docs match the product | | | Pass / Fail |
| **Ready to tag `v1.2.0`** | | | **Yes / No** |

When the last row is **Yes**, the maintainer tags, publishes Hub `1.2.0` / `1.2` / `latest`, and flips [Home](../index.md#release-status) to current production.

## Related

- [Upgrades](upgrades.md)  
- [Environment reference](env-reference.md)  
- [SECURITY.md](https://github.com/bjorngluck/piherder/blob/v1.2.0-dev/SECURITY.md)  
