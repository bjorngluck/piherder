# Web SSH console

## What this is

Optional **in-browser SSH terminal** to a managed host. The private key stays on PiHerder; the browser only shows a terminal (xterm.js) over an authenticated WebSocket.

**Default: off.** Enable with `PIHERDER_SSH_CONSOLE=true` and restart web.

**Train:** v1.2 Stream **W** · security bar is intentionally high.

## Why it exists

Operators on a tablet or locked-down PC need a shell without exporting the herder private key.

## Who can use it

| Role | Console |
|------|---------|
| **viewer** | No |
| **operator / admin** | Yes (when flag on + 2FA) |

Path: server detail → **Console** (only shown when the flag is enabled).

---

## End-to-end

1. Set env (see below) and restart **web**.  
2. Enroll **passkey** (preferred) and/or TOTP on **Account**.  
3. Open a server that already has an SSH key deployed.  
4. **Console** → step-up with **passkey** or **authenticator TOTP** → **New shell**.  
5. Optional: **Minimize** one shell and open another (up to the per-user cap).  
6. **Close shell** or leave the page when done — there is **no resume**.

---

## Security model (session cannot be stolen or resumed)

| Control | Behaviour |
|---------|-----------|
| Kill switch | `PIHERDER_SSH_CONSOLE=false` by default |
| In-app only | Same-origin Origin/Referer; cross-site mint rejected; CSP blocks embedding |
| No ticket in URL | Ticket sent in the **first WebSocket message** only |
| **No resume** | Ticket is **single-use**; closed PTY cannot reconnect |
| Session binding | Ticket includes login **`session_version`** |
| IP binding | Default on — different client IP cannot open/continue the shell |
| Device binding | HttpOnly **`console_device`** cookie pins the browser |
| Continuous revalidation | Every ~10s: session still valid, IP/device still match, still operator+ |
| 2FA step-up | **Passkey preferred**; TOTP app OK; **backup codes rejected by default** |
| CSP | Scripts/styles self-hosted; xterm vendored under `/static/vendor/xterm/` |

**Residual risk:** XSS on the PiHerder origin can act as the logged-in user until revalidation. Prefer HTTPS, keep the flag off when unused.

### 2FA recommendations

| Method | Console |
|--------|---------|
| **Passkey (WebAuthn)** | Preferred — primary button when enrolled |
| **TOTP app** | Accepted |
| **Backup codes** | **Not accepted** unless `PIHERDER_SSH_CONSOLE_ALLOW_BACKUP_CODES=true` |

Optional stricter modes:

```bash
PIHERDER_SSH_CONSOLE_REQUIRE_PASSKEY=true          # if passkeys enrolled, TOTP alone fails
PIHERDER_SSH_CONSOLE_REQUIRE_2FA_EVERY_SHELL=true  # re-2FA every New shell
```

---

## Multi-shell

| Control | Effect |
|---------|--------|
| **New shell** | Another PTY tab (counts toward concurrent cap) |
| **Tabs** | Switch between shells |
| **Minimize active** | Hide pane; connection stays live |
| **Restore** | Show a minimized shell again |
| **Close shell** | End that PTY only |
| **Lock step-up** | Clear short grant; next shell needs 2FA again |

Default max **2** shells per user (`PIHERDER_SSH_CONSOLE_MAX_PER_USER`).

---

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `PIHERDER_SSH_CONSOLE` | `false` | Master enable |
| `PIHERDER_SSH_CONSOLE_REQUIRE_2FA_EVERY_SHELL` | `false` | 2FA every New shell |
| `PIHERDER_SSH_CONSOLE_ALLOW_BACKUP_CODES` | `false` | Allow backup codes for step-up |
| `PIHERDER_SSH_CONSOLE_PREFER_PASSKEY` | `true` | UI promotes passkey |
| `PIHERDER_SSH_CONSOLE_REQUIRE_PASSKEY` | `false` | Passkey only if enrolled |
| `PIHERDER_SSH_CONSOLE_BIND_IP` | `true` | Bind ticket/shell to client IP |
| `PIHERDER_SSH_CONSOLE_BIND_DEVICE` | `true` | Bind to `console_device` cookie |
| `PIHERDER_SSH_CONSOLE_REVALIDATE_SEC` | `10` | Continuous check interval |
| `PIHERDER_SSH_CONSOLE_TICKET_SEC` | `60` | Ticket TTL |
| `PIHERDER_SSH_CONSOLE_IDLE_SEC` | `900` | Idle disconnect |
| `PIHERDER_SSH_CONSOLE_MAX_SEC` | `3600` | Max session length |
| `PIHERDER_SSH_CONSOLE_MAX_PER_USER` | `2` | Concurrent shells / user |
| `PIHERDER_SSH_CONSOLE_MAX_GLOBAL` | `10` | Instance-wide concurrent shells |
| `PIHERDER_SSH_CONSOLE_GRANT_MIN` | `10` | Minutes of multi-shell grant after 2FA |

Also: **CSP** (`PIHERDER_CSP=true`) should stay on in production.

Compose passes these through `docker-compose.yml` (`x-piherder-app-env`). Set them in `.env` and recreate **web**.

```bash
# Enable console (example)
PIHERDER_SSH_CONSOLE=true
docker compose up -d web
```

---

## Related

- [2FA & force 2FA](../account-security/two-factor.md)  
- [Roles](../account-security/roles.md)  
- [Environment reference](../operations/env-reference.md)  
- [SECURITY.md](https://github.com/bjorngluck/piherder/blob/main/SECURITY.md)  
- [Add a server](add-server.md) (SSH key first)  
