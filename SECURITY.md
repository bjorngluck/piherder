# Security policy

## Supported versions

| Version | Support |
|---------|---------|
| **v1.3.x** | **Current production** line ([RELEASE_v1.3.0.md](docs/RELEASE_v1.3.0.md) · [PLAN_v1.3.0.md](docs/PLAN_v1.3.0.md)) |
| **v1.2.x** | Prior production; prefer upgrade to **v1.3.x** ([RELEASE_v1.2.0.md](docs/RELEASE_v1.2.0.md) · [PLAN_v1.2.0.md](docs/PLAN_v1.2.0.md)) |
| **v1.1.x** | Prior production; prefer upgrade to **v1.3.x** ([RELEASE_v1.1.1.md](docs/RELEASE_v1.1.1.md) · [RELEASE_v1.1.0.md](docs/RELEASE_v1.1.0.md)) |
| **v1.0.x** | Prior production; prefer upgrade to **v1.3.x** ([RELEASE_v1.0.0.md](docs/RELEASE_v1.0.0.md)) |
| **`main`** | Development tip; security fixes land here first |
| **v0.9.x and older** | Best-effort; prefer upgrade to latest production |

Security fixes are applied on the default branch (`main`) and released as **v1.3.x** (or later) patch tags when warranted. Prefer the latest **1.3.x** tag for production.

## Reporting a vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Instead:

1. Email the maintainer via the address listed on [github.com/bjorngluck](https://github.com/bjorngluck) / the project website, **or**
2. Use [GitHub private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing/privately-reporting-a-security-vulnerability) on [bjorngluck/piherder](https://github.com/bjorngluck/piherder) if enabled for the repository.

Include:

- Description of the issue and impact  
- Steps to reproduce (PoC)  
- Affected version / commit if known  
- Whether you plan a public write-up (please coordinate disclosure)

We aim to acknowledge reports within a few days and will work with you on a fix and coordinated disclosure.

## Security model (summary)

| Asset | Protection |
|-------|------------|
| `PIHERDER_MASTER_KEY` | Host `.env` only — never commit |
| SSH private keys / optional passwords | Fernet-encrypted in DB |
| User passwords | bcrypt + password policy (min 10, upper/lower/digit; soft max ~72 characters) |
| 2FA secrets | Fernet-encrypted TOTP; hashed backup codes; WebAuthn public keys only (passkeys, v1.2+) |
| OIDC client secret | Fernet-encrypted in `appsetting` (v1.2+ Stream S); no IdP tokens stored at rest |
| OIDC identity links | `(issuer, subject)` → user; email soft-match for auto-link only |
| API tokens (`ph_…`) | Stored as hashes only; shown once at create/rotate; scopes + optional IP allowlist |
| Sessions | JWT cookie (HS256 via **PyJWT** + cryptography); **HttpOnly**, **SameSite=Lax**, `path=/`, **Secure** when public URL is HTTPS |
| Cross-origin browser POSTs | Same-origin middleware (Origin/Referer host match when present); Bearer `/api/v1` skipped |
| **Content-Security-Policy (v1.2+)** | Default **on** (`PIHERDER_CSP=true`): `default-src 'self'`, `object-src 'none'`, `frame-ancestors 'self'` (same-origin console modal only), `frame-src 'self'`, form-action self, **connect-src `'self'` + public origin / its `wss:` only** (no wildcard `ws:`/`wss:`). **No `unsafe-eval`** (Tailwind is compiled CSS). Inline script/style still allowed for template `<script>` / xterm — nonces in a later train. Report-Only: `PIHERDER_CSP_REPORT_ONLY=true`. Also: `X-Frame-Options: SAMEORIGIN`, `X-Content-Type-Options: nosniff`, `Referrer-Policy`, `Permissions-Policy` (incl. `publickey-credentials-get=(self)` for passkeys in console iframe). |
| Auth rate limits | Login / 2FA / register limited per IP (disabled only via `PIHERDER_DISABLE_AUTH_RATE_LIMIT` for E2E) |
| Streams (SSE) | Docker logs/build, backup/OS progress require session; build stream is **operator+** |
| Input hygiene | Risk-based validators on paths, hostnames, SSH users, cron, action allowlists (`app/services/input_validation.py`) |
| Transport | HTTPS via Caddy + operator-supplied PEMs recommended for production |
| SSH host keys (v1.2+) | First successful connect **pins** the remote host key (TOFU). Later mismatch refuses. Reset under SSH access after a rebuild. |
| Weak `SECRET_KEY` | Process **refuses to start** unless `PIHERDER_ALLOW_INSECURE=true` or `DEMO_MODE` (lab only). |
| Live host SSH from UI | Docker logs / diagnostics / console / compose build: **operator+**. Viewers see cached inventory and job history, not a live PTY. |

Further detail: [SPEC.md](SPEC.md) · [docs/ADMIN.md](docs/ADMIN.md) · [wiki roles](wiki/account-security/roles.md) · [PLAN_v1.0.0.md](docs/PLAN_v1.0.0.md).

## Dependencies & supply chain

| Practice | Status |
|----------|--------|
| Declared deps | `pyproject.toml` — minimum versions / ranges (`>=`) for package metadata |
| Resolver lock | **`uv.lock`** — full resolved graph + hashes (source of truth) |
| Pip export | **`requirements.lock.txt`** (runtime + `[dev]`) and **`requirements.runtime.lock.txt`** (runtime only) — generated with hashes via `scripts/refresh-lockfiles.sh` |
| Docker image | `pip install --require-hashes -r requirements.lock.txt` then `pip install --no-deps --no-build-isolation -e .` ([Dockerfile](Dockerfile)) |
| CI | Same locked install ([`.github/workflows/test.yml`](.github/workflows/test.yml)) |
| JWT library | **PyJWT[crypto]** (HS256). Former `python-jose` / transitive `ecdsa` removed |
| Vulnerability scan | Run `pip-audit` periodically (and/or Dependabot); deepen in [ROADMAP quality track](docs/ROADMAP_ECOSYSTEM.md#quality--platform-post-rc--post-10-first-production) |
| Intentional patching | Change `pyproject.toml` if needed → `./scripts/refresh-lockfiles.sh` → tests + `pip-audit` → commit **all three** lock artifacts → rebuild images |

**Do not** `pip install -e ".[dev]"` for release/RC images without the lock — that re-resolves floating versions from PyPI.

## Public demo sandbox (`PIHERDER_DEMO_MODE`)

When `PIHERDER_DEMO_MODE=true` (e.g. **https://piherder-demo.hacknow.info**):

| Risk | Control |
|------|---------|
| Shared-account vandalism / data pollution | Shared login is **viewer-only** + demo write guard; optional Access/Turnstile; scheduled force re-seed (host cron) |
| Published shared password | **Expected** for the public sandbox — not a secret. Rotate via `.env` + re-seed + update live wiki ([demo-site](wiki/operations/demo-site.md)) |
| API scrape / automation | Token create and Bearer auth **hard-blocked** |
| Fake “I onboarded my Pi” | Wizard / SSH test / key deploy blocked |
| Accidental lab access | No real keys in seed; **console is simulated only** (no Paramiko/TCP); job mutations are canned; demo VPS isolated from home lab |
| Origin bypass | Prefer CF Tunnel or firewall to CF IPs only |

Demo must use **unique** Fernet/session secrets and never hold decryptable production host keys. Maintainer runbook: [docs/DEMO_SITE.md](docs/DEMO_SITE.md). User-facing credentials: [wiki/operations/demo-site.md](wiki/operations/demo-site.md).

## Operational recommendations

- Use a unique strong `PIHERDER_MASTER_KEY` and `SECRET_KEY` (see [`.env.example`](.env.example) for the full env catalog). Web **refuses to start** if `SECRET_KEY` looks like a stock/dev default unless `PIHERDER_ALLOW_INSECURE=true` or `DEMO_MODE`.  
- Prefer SSH key auth; clear any stored SSH passwords after deploy. After upgrade, **Test connection** once per host to pin the SSH host key; reset the pin only when you rebuilt the machine.  
- Do not run with `PIHERDER_ALLOW_INSECURE=true` against a real fleet.  
- Enable 2FA for admin accounts (TOTP and/or **passkeys**); consider **Force 2FA** in Settings. Treat **trusted devices** as full session risk until revoked. Passkeys need HTTPS + matching `PIHERDER_HOSTNAME` / `PIHERDER_PUBLIC_URL` (except localhost).  
- If using **SSO / OIDC** (v1.2+): keep at least one **break-glass local admin password**; map IdP groups carefully (default role is **viewer**); treat PiHerder 2FA as defense-in-depth after the IdP (SSO does not skip enrolled 2FA). See [wiki SSO](wiki/account-security/sso-oidc.md) · [FEATURE_PLAN_SSO_OIDC.md](docs/FEATURE_PLAN_SSO_OIDC.md).  
- **CSP (v1.2+):** leave `PIHERDER_CSP=true` in production. Use `PIHERDER_CSP_REPORT_ONLY=true` only while validating a tighter policy. Console assets (xterm) are vendored under `/static/vendor/xterm/` so they need no CDN allowlist. When Turnstile is configured, CSP allows `https://challenges.cloudflare.com` for script/frame/connect.  
- **Turnstile (optional):** set both `PIHERDER_TURNSTILE_SITE_KEY` and `PIHERDER_TURNSTILE_SECRET_KEY` to require a challenge on `POST /auth/login`. Empty keys leave login unchanged (lab-friendly). Recommended on the public demo.  
- **Web SSH console** (v1.2+): default **off** (`PIHERDER_SSH_CONSOLE=false`). Designed as **in-app only** (not a public remote API):
  - **operator+ only** (viewer 403); session cookie required (no Bearer `/api/v1` console)
  - Ticket mint requires **same-site browser** Origin/Referer; rejects `Sec-Fetch-Site: cross-site`
  - WebSocket requires **Origin == Host**; open-ticket in first WS message only (not query string)
  - Open ticket is **single-use**; **soft resume** after unexpected WS drop parks the SSH PTY (bound resume token, same user/host/session/device) until idle/max (or Settings park hold / `PIHERDER_SSH_CONSOLE_HOLD_SEC` if set); explicit close (`bye`) destroys the PTY
  - Ticket / resume bound to **login `session_version`**, **client IP** (default on; mobile resume may allow IP change if device cookie still matches), and **console device cookie** (default on)
  - **Continuous revalidation** (~every 10s while attached): session still valid, bindings still match, user still operator+ — else PTY killed
  - Logout / password change / admin “sign out sessions” invalidates open and parked shells within one revalidation / claim check
  - **2FA step-up (recommended: WebAuthn/passkey)**: passkey preferred in UI; TOTP app accepted; **backup codes rejected by default** (Settings → Security; env `PIHERDER_SSH_CONSOLE_ALLOW_BACKUP_CODES` locks). Successful step-up issues a **fleet-wide** grant cookie (all hosts, ~10 min unless Settings grant window / env). Optional require-passkey / every-shell 2FA in Settings (env locks when set)
  - Concurrent + idle + max session limits (Settings → Console; env wins if set); PEM never in browser; CSP allows **same-origin** console iframe only (`frame-ancestors 'self'`)
  - UI: floating popup per host; multi-host workspace at `/console` (inactive host tabs keep WebSockets via opacity, not `visibility:hidden`); compact chrome (Aa / ···); sticky Ctrl + common chords
  - **Known UX (mobile):** soft **Tab** IME leftovers improved in 1.2 QA (v12); residual exotic IMEs — **KI-console-mobile-soft-tab**; see wiki web-ssh-console Known issues
  - Residual risk: XSS on herder origin is still shell-equivalent; IP bind can break mobile networks (turn off in Settings → Console, or set `PIHERDER_SSH_CONSOLE_BIND_IP=false` to lock)
  - **Command audit** (v1.3, default **off**): server-side PTY tap, Fernet body, operator+ read, viewer 403, demo never stores. Redaction of password prompts / some tokens is heuristic — secrets can still be captured. Optional **require on every session** refuses a live shell if recording cannot start. JSON config-only herder backups skip transcript bodies.  
- **Host Files** (v1.3, default **off**, `PIHERDER_HOST_FILES=false`): jailed SFTP explorer. Operator+; viewer 403 on a real herder. Public **demo** serves a **canned** tree only (no SFTP; viewers may browse; writes refused). Default identity **fleet** (docker_base or home, never `/`). Optional **privileged** identity (same elevate RBAC + 2FA grant as console; jail `/` minus `/proc` `/sys` `/dev` `/run`). UI: edit (512 KiB UTF-8; privileged save of root-owned files via `sudo -n tee`), zip/unzip (zip-slip refused), chmod/chown (names; `sudo -n` when privileged is not root), name + content search, image/hex preview (‹› in folder), move, folder upload, thin `docker cp` / volume open. `.env` / PEM / key files **list**; open/edit/download/preview/content-search requires the same 2FA grant as privileged Files. API scope `files` is fleet list/get/put/mkdir/rename/empty-delete only (richer API → v1.4+ under consideration). Uploads stream (default 512 MiB; Settings cap up to 32 GiB). Audit `host_file_*` records path + bytes + sha256, never the body.
- **Service migrate** (v1.4, default **off**, `PIHERDER_SERVICE_MIGRATE=false`): operator+; viewer 403; demo never copies. Dual-host exclusive with backup + stack mutate. Staging under `BACKUP_ROOT/_migrate/{job_id}` (mode 700; wiped on success). Path jail; leftover **remove** requires extra ack and never wipes dest. Audit preview/start without PEM / `.env` / NPM password bodies. Wiki: [Move a service](wiki/docker/service-migration.md).  


- Put PiHerder behind trusted TLS; restrict network access where possible. Set `PIHERDER_PUBLIC_URL=https://…` so session cookies get the **Secure** flag (or force `COOKIE_SECURE=true`), OIDC redirect URIs match, and **email password-reset links** use that origin only (Host / `X-Forwarded-Host` are ignored).  
- **Sign out** bumps `session_version` (stolen JWTs die; live consoles close within the revalidate interval — Settings → Console, default 10s; parked PTYs are destroyed). Trusted-device cookies still survive logout by design.  
- Do **not** publish the app port on the LAN. Stock compose binds `127.0.0.1:8000` only; use Caddy (`:8888` / `:8443`). Forwarded client IPs (`X-Forwarded-For` / `CF-Connecting-IP`) are honoured only when the TCP peer is in `PIHERDER_TRUSTED_PROXY_CIDRS` (Compose sets RFC1918 + loopback so Caddy is trusted).  
- Set `METRICS_TOKEN` if `/metrics` is reachable beyond a private scrape network.  
- Treat API tokens like passwords; revoke compromised tokens immediately.  
- Leave `CORS_ORIGINS` empty unless a browser on another origin must call `/api/v1`; never use `*`. CORS is not a substitute for Bearer + scopes + IP allowlists.  
- Keep herder self-backups on durable storage separate from the fleet hosts when practical.
