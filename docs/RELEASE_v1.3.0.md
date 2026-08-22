# PiHerder v1.3.0

**22 August 2026.** You run the fleet; you own the policy. Password rules, 2FA, console timeouts, and alert volume live in Settings — no image rebuild. Lists scale. History Grafana never sees lives at `/reports`. Optional jailed **Host Files** and **Connect as…** when you need them.

**Image:** [bjorngluck/piherder](https://hub.docker.com/r/bjorngluck/piherder) `1.3.0` · `1.3` · `latest` (amd64 + arm64). Pins `1.2.0` / `1.2` stay valid.

---

## What’s new

### Security you configure

Settings → **Security**: password length and classes, who must enrol 2FA, grace **0–60** days, step-up windows. Every password form shows the **same** live rules. Account SSO **Unlink** is a confirmation sheet; unlink / passkey revoke accept any enrolled 2FA (passkey or TOTP). Linking Authentik works under CSP `form-action 'self'`.

Wiki: [Settings](../wiki/operations/settings.md) · [2FA](../wiki/account-security/two-factor.md) · [SSO](../wiki/account-security/sso-oidc.md)

### Console you configure — and a least-priv host user

Settings → **Console**: idle, max session, slots, ticket, park, bind, scrollback. Master enable stays `PIHERDER_SSH_CONSOLE` (default **off**).

Each host can keep a **fleet** identity (jobs + default shell/files) and an optional **privileged** identity. Console **Connect as…** uses the privileged key only when you choose it; jobs stay on fleet. Settings control who may elevate.

Optional **command audit** (default **off**): who typed what in the webshell. Redaction is heuristic — do not treat a transcript as secret-free.

Wiki: [Web SSH](../wiki/day-to-day/web-ssh-console.md)

### Lists that page and search

Servers, Docker stacks, and discovery lists get page size, pager, and free-text `q`. The token API accepts `q` / `limit` / `offset` on servers.

### Alerts you can quiet

Settings → Alerts → **Alert policy**: per-category mute, severity, debounce. Map / discovery noise does not have to shout as loud as a cert fail.

Wiki: [Alerts](../wiki/operations/alerts-email-webhooks.md)

### Reports — history Grafana cannot see

`/reports` (header, after Catalog). Backups dest size and success, OS patches applied, LAN live-per-day, Docker deploys, console session time. 7 / 30 / 90 days. Not a second Grafana; not status widgets.

Wiki: [Reports](../wiki/day-to-day/reports.md)

### Host Files — jailed manager (opt-in)

Turn on `PIHERDER_HOST_FILES` for a confined file manager on each SSH host: browse, upload/download (progress, default 512 MiB, Settings up to 32 GiB), mkdir, rename/move, recursive delete, UTF-8 edit, zip/unzip on the host, chmod/chown, search, preview, folder upload, thin Docker volumes + `docker cp`. **Limited access** (fleet jail) vs **Elevated access** (privileged). `.env` / PEM list freely; open/edit/download needs 2FA.

Not WinSCP, not dual-pane, not a backup job, not the compose editor. Default **off**. Demo never shows a real tree.

Wiki: [Host Files](../wiki/day-to-day/host-files.md)

### Settings hub

General is **cards + Edit** (phone sheets). Timezone stays on the page.

### UI stays up after a host reboot

`web` now uses `restart: unless-stopped` like db / redis / celery / caddy. Recreate once: `docker compose up -d`.

---

## Defaults (opt-in surfaces stay off)

| | Default |
|--|---------|
| Web SSH console | **off** (`PIHERDER_SSH_CONSOLE`) |
| Host Files | **off** (`PIHERDER_HOST_FILES`) |
| Command audit | **off** (Settings) |

---

## Upgrade from 1.2

1. Full DR self-backup. Keep `PIHERDER_MASTER_KEY`.  
2. Pull `bjorngluck/piherder:1.3.0` (or `git checkout v1.3.0`).  
3. `docker compose pull && docker compose up -d` — Alembic **`040_ssh_identities`** · **`041_console_transcripts`**.  
4. Compose no longer injects defaulted `PIHERDER_SSH_CONSOLE_*` knobs. Timeouts live in Settings unless you lock env.  
5. Smoke: Security · Console · Reports · optional Files if you enable the flag.

[Wiki upgrades](../wiki/operations/upgrades.md#12--13)

---

## Honest limits

| | |
|--|--|
| Phone console Tab | Residual IME on some keyboards |
| Command audit | Heuristic redaction — secrets can still land in the log |
| Host Files | Flag off until you opt in |
| Not this release | Dual-pane / zmodem, custom dashboard, per-host roles, host `tmux`/`screen`, CSP nonces, service migration (that is [v1.4](PLAN_v1.4.0.md)) |

---

From [v1.2.0](RELEASE_v1.2.0.md). Docs: [piherder-docs.hacknow.info](https://piherder-docs.hacknow.info/)
