# Security policy

## Supported versions

| Version | Support |
|---------|---------|
| **v1.1.x** | **Current production** line ([RELEASE_v1.1.0.md](docs/RELEASE_v1.1.0.md) · [PLAN_v1.1.0.md](docs/PLAN_v1.1.0.md)) |
| **v1.0.x** | Prior production; prefer upgrade to **v1.1.x** ([RELEASE_v1.0.0.md](docs/RELEASE_v1.0.0.md)) |
| **`main`** | Development tip; security fixes land here first |
| **v0.9.x and older** | Best-effort; prefer upgrade to latest production |

Security fixes are applied on the default branch (`main`) and released as **v1.1.x** (or later) patch tags when warranted. Prefer the latest **1.1.x** tag for production.

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
| **Content-Security-Policy (v1.2+)** | Default **on** (`PIHERDER_CSP=true`): `default-src 'self'`, `object-src 'none'`, `frame-ancestors 'none'`, form-action self, connect-src self + ws/wss (console). Inline script/style + `unsafe-eval` still allowed for legacy templates + Tailwind Play — **no third-party script CDNs**. Report-Only: `PIHERDER_CSP_REPORT_ONLY=true`. Also: `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy`, `Permissions-Policy`. |
| Auth rate limits | Login / 2FA / register limited per IP (disabled only via `PIHERDER_DISABLE_AUTH_RATE_LIMIT` for E2E) |
| Streams (SSE) | Docker logs/build, backup/OS progress require session; build stream is **operator+** |
| Input hygiene | Risk-based validators on paths, hostnames, SSH users, cron, action allowlists (`app/services/input_validation.py`) |
| Transport | HTTPS via Caddy + operator-supplied PEMs recommended for production |

Further detail: [SPEC.md](SPEC.md) · [docs/ADMIN.md](docs/ADMIN.md) · [wiki roles](wiki/account-security/roles.md) · [PLAN_v1.0.0.md](docs/PLAN_v1.0.0.md).

## Dependencies & supply chain

| Practice | Status |
|----------|--------|
| Declared deps | `pyproject.toml` — minimum versions / ranges (`>=`) for package metadata |
| Resolver lock | **`uv.lock`** — full resolved graph + hashes (source of truth) |
| Pip export | **`requirements.lock.txt`** (runtime + `[dev]`) and **`requirements.runtime.lock.txt`** (runtime only) — generated with hashes via `scripts/refresh-lockfiles.sh` |
| Docker image | `pip install --require-hashes -r requirements.lock.txt` then `pip install --no-deps -e .` ([Dockerfile](Dockerfile)) |
| CI | Same locked install ([`.github/workflows/test.yml`](.github/workflows/test.yml)) |
| JWT library | **PyJWT[crypto]** (HS256). Former `python-jose` / transitive `ecdsa` removed |
| Vulnerability scan | Run `pip-audit` periodically (and/or Dependabot); deepen in [ROADMAP quality track](docs/ROADMAP_ECOSYSTEM.md#quality--platform-post-rc--post-10-first-production) |
| Intentional patching | Change `pyproject.toml` if needed → `./scripts/refresh-lockfiles.sh` → tests + `pip-audit` → commit **all three** lock artifacts → rebuild images |

**Do not** `pip install -e ".[dev]"` for release/RC images without the lock — that re-resolves floating versions from PyPI.

## Operational recommendations

- Use a unique strong `PIHERDER_MASTER_KEY` and `SECRET_KEY` (see [`.env.example`](.env.example) for the full env catalog). Web logs a **warning** if `SECRET_KEY` looks like a stock/dev default.  
- Prefer SSH key auth; clear any stored SSH passwords after deploy.  
- Enable 2FA for admin accounts (TOTP and/or **passkeys**); consider **Force 2FA** in Settings. Treat **trusted devices** as full session risk until revoked. Passkeys need HTTPS + matching `PIHERDER_HOSTNAME` / `PIHERDER_PUBLIC_URL` (except localhost).  
- If using **SSO / OIDC** (v1.2+): keep at least one **break-glass local admin password**; map IdP groups carefully (default role is **viewer**); treat PiHerder 2FA as defense-in-depth after the IdP (SSO does not skip enrolled 2FA). See [wiki SSO](wiki/account-security/sso-oidc.md) · [FEATURE_PLAN_SSO_OIDC.md](docs/FEATURE_PLAN_SSO_OIDC.md).  
- **CSP (v1.2+):** leave `PIHERDER_CSP=true` in production. Use `PIHERDER_CSP_REPORT_ONLY=true` only while validating a tighter policy. Console assets (xterm) are vendored under `/static/vendor/xterm/` so they need no CDN allowlist.  
- **Web SSH console** (v1.2+): default **off** (`PIHERDER_SSH_CONSOLE=false`). Designed as **in-app only** (not a public remote API):
  - **operator+ only** (viewer 403); session cookie required (no Bearer `/api/v1` console)
  - Ticket mint requires **same-site browser** Origin/Referer; rejects `Sec-Fetch-Site: cross-site` (CSRF / foreign form)
  - WebSocket requires **Origin == Host**; first message carries ticket — **not** query string (avoids proxy logs / Referer leak)
  - **2FA step-up**: TOTP, backup code, or **passkey**; optional `PIHERDER_SSH_CONSOLE_REQUIRE_2FA_EVERY_SHELL=true` for every New shell
  - Default grant (~10 min, per host + `session_version`) allows extra shells without re-prompt; **Lock step-up** / logout clears grant
  - Single-use tickets bound to session version; concurrent + idle + max session limits
  - Host private key **never** in browser (Paramiko PTY in herder only); CSP `frame-ancestors 'none'` blocks embedding
  - Rate-limited ticket mint; audit `ssh_console_open` / `close` / `denied`
  - Residual risk: XSS on the herder origin is still shell-equivalent — prefer HTTPS, keep flag off when unused  


- Put PiHerder behind trusted TLS; restrict network access where possible. Set `PIHERDER_PUBLIC_URL=https://…` so session cookies get the **Secure** flag (or force `COOKIE_SECURE=true`) and OIDC redirect URIs match.  
- Set `METRICS_TOKEN` if `/metrics` is reachable beyond a private scrape network.  
- Treat API tokens like passwords; revoke compromised tokens immediately.  
- Leave `CORS_ORIGINS` empty unless a browser on another origin must call `/api/v1`; never use `*`. CORS is not a substitute for Bearer + scopes + IP allowlists.  
- Keep herder self-backups on durable storage separate from the fleet hosts when practical.
