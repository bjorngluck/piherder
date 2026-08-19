# Upgrades

## What this is

How to move a running compose install to a newer **git tag or `main`**, pull the published image, and let Alembic migrate the database.

## Why a checklist

Upgrades change code *and* schema. A self-backup + unchanged master key is the difference between a smooth pull and an unrecoverable encrypted store.

!!! tip "Prefer tags"
    Prefer **tagged production releases** (`v1.2.0` / later `1.2.x`). Treat untagged `main` as moving. See [Home](../index.md#release-status) · [RELEASE_v1.2.0.md](https://github.com/bjorngluck/piherder/blob/main/docs/RELEASE_v1.2.0.md).

```bash
# Config DR first
# Settings → PiHerder backup → run now
# Also snapshot Postgres volume if you can

git fetch --tags
git checkout v1.2.0   # or later 1.2.x
docker compose pull
docker compose up -d
# Alembic runs on web startup
# optional pin: PIHERDER_IMAGE=bjorngluck/piherder:1.2.0 docker compose up -d
```

## Checklist

- [ ] Self-backup successful (**admin** — Settings → PiHerder backup)  
- [ ] `PIHERDER_MASTER_KEY` unchanged and backed up offline  
- [ ] Read [RELEASE notes](https://github.com/bjorngluck/piherder/blob/main/docs/RELEASE_v1.2.0.md) for the version you jump to  
- [ ] `docker compose ps` healthy (image `bjorngluck/piherder:…`)  
- [ ] Smoke: login, Users recovery (if multi-user), one server, maps/ports, optional template  
- [ ] Hard-refresh browser once after UI/CSS deploys (query-busted stylesheets)  


## 1.1 → 1.2

Jump from **v1.1.x** to **v1.2.0**. Full notes: [RELEASE_v1.2.0.md](https://github.com/bjorngluck/piherder/blob/main/docs/RELEASE_v1.2.0.md).

1. Take a **1.1** self-backup (config pack) and keep it offline with `PIHERDER_MASTER_KEY`.  
2. Read the release notes. Pre-1.2 “full” archives are **not** a Postgres dump.  
3. Confirm `.env` has a **long random `SECRET_KEY`**. The 1.2 web process **will not boot** on the compose default (`change-me-in-prod`) unless you set `PIHERDER_ALLOW_INSECURE=true` (lab only).  
4. `git fetch --tags && git checkout v1.2.0` (or pull the `1.2.0` image).  
5. `docker compose pull && docker compose up -d` — Alembic runs on web start (includes **`039_ssh_hostkey_pin`**).  
6. Immediately run **Settings → PiHerder backup → Full DR** once and copy the archive off-box. Set the schedule to **Full**.  
7. **Test connection** once per host — first success **pins** the SSH host key. Later key changes are refused until you **reset the pin** under SSH access (rebuilds). Existing `ssh_username` values are **not** rewritten (new hosts default to **`pi`**).  
8. Hard-refresh the browser (compiled Tailwind CSS; no Play CDN).  
9. New / changed env (Compose has defaults):  
   - `PIHERDER_SSH_CONSOLE` — web SSH, **default off**  
   - `PIHERDER_CSP` — default on; **no `unsafe-eval`** (compiled CSS)  
   - `PIHERDER_TRUSTED_PROXY_CIDRS` — Compose trusts RFC1918 + loopback so Caddy can pass visitor IPs  
   - `PIHERDER_ALLOW_INSECURE` — **leave false** on a real fleet  
   - Web port is **`127.0.0.1:8000`** only (not published on the LAN)  
10. Set `PIHERDER_PUBLIC_URL` if you use email password reset or SSO (reset links and OIDC redirects are built from it only).  
11. Smoke: login, one host, one job, optional console (if you enabled it).

SSO / passkeys / demo mode are **new optional surfaces** — they do not turn on by themselves.

## 1.2 → 1.3 train (`v1.3.0-dev`)

Package version stays **1.2.0** until freeze. Settings policy is JSON in `appsetting`. **Slice 4** adds Alembic **`040_ssh_identities`** (fleet + optional privileged SSH keys per host).

If you track the integration branch:

1. Self-backup first (**Full DR**).  
2. `git fetch && git checkout v1.3.0-dev && docker compose pull && docker compose up -d` (or rebuild local).  
3. **Compose change:** bundled compose **no longer injects defaulted** `PIHERDER_SSH_CONSOLE_*` knobs (idle, max, bind, …). Only the master enable `PIHERDER_SSH_CONSOLE` stays in compose. Timeouts live in **Settings → Console**. 2FA / grant live in **Settings → Security**.  
4. If you previously relied on those vars in `.env` **and** compose interpolation, they will not reach the container until you add that one line to compose `environment` (env then **locks** the knob). Otherwise Settings apply.  
5. Smoke: Settings → Security (password / 2FA) · Settings → Console (including **who may open privileged**) · one host **SSH access** (fleet identity still there; optional privileged card) · optional live shell if the flag is on.  
6. Alembic **`040_ssh_identities`** copies each host’s existing username + key into a **fleet** identity. `Server.ssh_username` is unchanged. Jobs keep using fleet.

Hub **`main` / 1.2.x** is unchanged until the 1.3 freeze.

## Breaking notes

Read the release doc for the version you jump to (migrations, new env keys, behaviour changes).
