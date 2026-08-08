# Web SSH console

## What this is

Optional **in-browser SSH terminal** to a managed host. The private key stays on PiHerder; the browser only shows a terminal (xterm.js) over an authenticated WebSocket.

**Default: off.** Enable with `PIHERDER_SSH_CONSOLE=true` and restart **web**.

**Train:** v1.2 Stream **W** · security bar is intentionally high.

## Why it exists

Operators on a tablet or locked-down PC need a shell without exporting the herder private key.

## Who can use it

| Role | Console |
|------|---------|
| **viewer** | No |
| **operator / admin** | Yes (when flag on + 2FA enrolled) |

---

## How it opens

Server detail → **Console** opens a **floating popup** over the page — the terminal itself (not a full-page help article).

| Control | Does |
|---------|------|
| **Maximize** | Full screen + slim outer bar; on mobile expands from a short bottom sheet |
| **Restore** | Back from maximize |
| **✕** | Close popup (ends shells on that host) |
| **+ Hosts** | Multi-host workspace at `/console` |
| **+ Shell** | New PTY (passkey/TOTP if step-up needed) |
| **Aa** | Font size **8–28** (hidden until tapped) |
| **···** | Extra keys (arrows, Tab, Esc, Line/Scr) |
| **Ctrl** | Sticky Ctrl — next keyboard letter is Ctrl+letter |
| **^C ^S ^X ^Q** | Common chords (others: sticky Ctrl + key) |
| **Sel / Copy / Paste** | Mobile select (drag either direction) + clipboard |
| **App switch** | Shells **park on the server** until idle/max; return **auto-resumes** |

Once a shell is connected, chrome **auto-compacts** so the terminal gets most of the screen. Tap **Aa** or **···** when you need tools or extra keys.

Explicit close (shell **✕** or popup **✕**) still ends the session (`bye`). Switching apps or backgrounding the tab does **not**.

### Multiple hosts (`/console`)

| Behaviour | Detail |
|-----------|--------|
| Open from popup | **+ Hosts**, or go to `/console?host=<id>` |
| Host tabs | Switch anytime — **inactive host tabs stay connected** (iframes stay alive; not `visibility:hidden`) |
| **+ Host** | Opens only hosts you pick — does **not** auto-reopen every host from an earlier session |
| Shell slots | **Account-wide** (default **4** concurrent PTYs across all hosts) |
| 2FA grant | **Fleet-wide**: one passkey/TOTP covers **all hosts** until expiry (~10 min) or **Lock** / **Aa → Lock step-up** |
| Grant expired | Next **+ Shell** re-shows Passkey/TOTP automatically (no need to hunt for Lock first) |

---

## End-to-end

1. Set env (below) and recreate **web**.  
2. Enroll **passkey** (preferred) and/or TOTP on **Account**.  
3. Host must already have SSH credentials (key preferred).  
4. Server → **Console** → popup → step-up if asked → **+ Shell**.  
5. Optional: **+ Hosts** → open more machines; switch host tabs freely.  
6. **Maximize** for more terminal; switch apps safely (soft resume).  
7. **✕** shell or popup when finished.

---

## Security model

| Control | Behaviour |
|---------|-----------|
| Kill switch | `PIHERDER_SSH_CONSOLE=false` by default |
| In-app only | Same-origin mint (Origin/Referer); cross-site rejected; CSP `frame-ancestors 'self'` / `frame-src 'self'` for same-origin popup iframe only |
| No ticket in URL | Ticket in the **first WebSocket message** only |
| Single-use open ticket | Cannot mint a second WS with the same ticket |
| **Soft resume** | Unexpected WS drop **parks** the SSH PTY (bound resume token); explicit **bye** destroys it |
| Multi-host keep-alive | Inactive host tabs use opacity (not `visibility:hidden`) so WebSockets stay up while you work on another host |
| Session binding | Login **`session_version`** — logout / password change / admin session revoke kills shells |
| IP binding | Default on; resume may allow IP change if **device** cookie still matches (mobile networks) |
| Device binding | HttpOnly **`console_device`** cookie |
| Continuous revalidation | Every ~10s while attached |
| Fleet 2FA grant | One step-up for all hosts; UI re-prompts when the cookie expires |
| 2FA methods | **Passkey preferred**; TOTP app OK; **backup codes rejected by default** |
| CSP | Self-hosted scripts; xterm under `/static/vendor/xterm/` |
| Limits | Concurrent + idle + max session; PEM never in browser |

**Residual risk:** XSS on the PiHerder origin can act as the logged-in user. Prefer HTTPS; leave the flag off when unused.

### Soft resume (app switch)

When the browser suspends the tab or drops the WebSocket:

1. Server **keeps the SSH session** (slot still counted).  
2. Output while away is buffered (~256 KB).  
3. On return, the client sends `{"type":"resume","resume":"…"}` and reattaches.  
4. Resume is **not** attempted while a multi-host iframe is the inactive tab (avoids burning the resume token).  
5. Still ends on **idle** / **max session** / explicit close / logout.

Optional hard cap on park window: `PIHERDER_SSH_CONSOLE_HOLD_SEC` (default **0** = until idle/max only).

### 2FA recommendations

| Method | Console |
|--------|---------|
| **Passkey (WebAuthn)** | Preferred |
| **TOTP app** | Accepted |
| **Backup codes** | **Not accepted** unless `PIHERDER_SSH_CONSOLE_ALLOW_BACKUP_CODES=true` |

```bash
PIHERDER_SSH_CONSOLE_REQUIRE_PASSKEY=true          # if passkeys enrolled, TOTP alone fails
PIHERDER_SSH_CONSOLE_REQUIRE_2FA_EVERY_SHELL=true  # re-2FA every New shell (no fleet grant)
```

---

## Multi-shell (same host)

| Control | Effect |
|---------|--------|
| **+ Shell** | Another PTY tab (counts toward concurrent cap) |
| **Shell tabs** | Switch between shells on this host |
| **Close shell (✕)** | End that PTY only (`bye`) |
| **Lock** / **Aa → Lock step-up** | Clear fleet grant; next shell needs 2FA again |

Default max **4** shells per user (`PIHERDER_SSH_CONSOLE_MAX_PER_USER`), shared across all hosts.

---

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `PIHERDER_SSH_CONSOLE` | `false` | Master enable |
| `PIHERDER_SSH_CONSOLE_REQUIRE_2FA_EVERY_SHELL` | `false` | 2FA every New shell (no fleet grant) |
| `PIHERDER_SSH_CONSOLE_ALLOW_BACKUP_CODES` | `false` | Allow backup codes for step-up |
| `PIHERDER_SSH_CONSOLE_PREFER_PASSKEY` | `true` | UI promotes passkey |
| `PIHERDER_SSH_CONSOLE_REQUIRE_PASSKEY` | `false` | Passkey only if enrolled |
| `PIHERDER_SSH_CONSOLE_BIND_IP` | `true` | Bind ticket/shell to client IP |
| `PIHERDER_SSH_CONSOLE_BIND_DEVICE` | `true` | Bind to `console_device` cookie |
| `PIHERDER_SSH_CONSOLE_REVALIDATE_SEC` | `10` | Continuous check interval |
| `PIHERDER_SSH_CONSOLE_TICKET_SEC` | `60` | Open-ticket TTL |
| `PIHERDER_SSH_CONSOLE_IDLE_SEC` | `900` | Idle disconnect (also ends parked shells) |
| `PIHERDER_SSH_CONSOLE_MAX_SEC` | `3600` | Max session length |
| `PIHERDER_SSH_CONSOLE_MAX_PER_USER` | `4` | Concurrent shells / user (all hosts) |
| `PIHERDER_SSH_CONSOLE_MAX_GLOBAL` | `20` | Instance-wide concurrent shells |
| `PIHERDER_SSH_CONSOLE_SCROLLBACK` | `2000` | Default xterm scrollback lines |
| `PIHERDER_SSH_CONSOLE_HOLD_SEC` | `0` | Max park after WS drop (`0` = idle/max only) |
| `PIHERDER_SSH_CONSOLE_GRANT_MIN` | `10` | Fleet-wide multi-host grant after 2FA (minutes) |

Also: keep **CSP** on in production (`PIHERDER_CSP=true`).

```bash
# Enable console (example)
PIHERDER_SSH_CONSOLE=true
docker compose up -d web
```

Full catalog: [Environment reference](../operations/env-reference.md) · sample: [console-and-backup.env.example](https://github.com/bjorngluck/piherder/blob/v1.2.0-dev/docs/console-and-backup.env.example).

---

## Related

- [2FA & force 2FA](../account-security/two-factor.md)  
- [Roles](../account-security/roles.md)  
- [Environment reference](../operations/env-reference.md)  
- [SECURITY.md](https://github.com/bjorngluck/piherder/blob/main/SECURITY.md)  
- [Add a server](add-server.md) (SSH key first)  
