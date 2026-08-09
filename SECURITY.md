# Security policy

## Supported versions

| Version | Support |
|---------|---------|
| **v1.1.x** | **Current production** line ([RELEASE_v1.1.1.md](docs/RELEASE_v1.1.1.md) · [RELEASE_v1.1.0.md](docs/RELEASE_v1.1.0.md) · [PLAN_v1.1.0.md](docs/PLAN_v1.1.0.md)) |
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
| 2FA secrets | Fernet-encrypted TOTP; hashed backup codes |
| API tokens (`ph_…`) | Stored as hashes only; shown once at create/rotate; scopes + optional IP allowlist |
| Sessions | JWT cookie (HS256 via **PyJWT** + cryptography); **HttpOnly**, **SameSite=Lax**, `path=/`, **Secure** when public URL is HTTPS |
| Cross-origin browser POSTs | Same-origin middleware (Origin/Referer host match when present); Bearer `/api/v1` skipped |
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
- Enable 2FA for admin accounts; consider **Force 2FA** in Settings. Treat **trusted devices** as full session risk until revoked.  
- Put PiHerder behind trusted TLS; restrict network access where possible. Set `PIHERDER_PUBLIC_URL=https://…` so session cookies get the **Secure** flag (or force `COOKIE_SECURE=true`).  
- Set `METRICS_TOKEN` if `/metrics` is reachable beyond a private scrape network.  
- Treat API tokens like passwords; revoke compromised tokens immediately.  
- Leave `CORS_ORIGINS` empty unless a browser on another origin must call `/api/v1`; never use `*`. CORS is not a substitute for Bearer + scopes + IP allowlists.  
- Keep herder self-backups on durable storage separate from the fleet hosts when practical.
