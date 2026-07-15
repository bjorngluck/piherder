# Security policy

## Supported versions

| Version | Support |
|---------|---------|
| **v0.2.x** (tag `v0.2.0`+) | Current release line |
| `main` | Active development (pre-1.0) |

Security fixes are applied on the default branch (`main`) and cherry-picked or released as patch tags when warranted. Prefer the latest release tag or `main` for fixes.

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
| User passwords | bcrypt + password policy |
| 2FA secrets | Fernet-encrypted TOTP; hashed backup codes |
| API tokens (`ph_…`) | Stored as hashes only; shown once at create/rotate; scopes + optional IP allowlist |
| Sessions | JWT cookie (HS256) |
| Transport | HTTPS via Caddy + operator-supplied PEMs recommended for production |

Further detail: [SPEC.md](SPEC.md) · [docs/ADMIN.md](docs/ADMIN.md).

## Dependencies & supply chain

| Practice | Status / plan |
|----------|----------------|
| Declared deps | `pyproject.toml` with minimum versions (`>=`) |
| Lockfile | `uv.lock` in repo (hashes) — **not yet** consumed by Dockerfile / CI (`pip install -e .` resolves floating) |
| Image / CI pins | **Post-RC:** install from lock (`uv sync --frozen` or exported requirements) so rebuilds are reproducible |
| Vulnerability scan | Run `pip-audit` (or Dependabot) periodically; track in [ROADMAP quality track](docs/ROADMAP_ECOSYSTEM.md#quality--platform-post-rc--post-10-first-production) |
| Known transitive note | `python-jose` → `ecdsa` (PYSEC-2026-1325 / Minerva timing). Sessions use **HS256** only; ecdsa signing not used for cookies. **No upstream fix.** Prefer **PyJWT + cryptography** after RC. |
| Intentional patching | Bump with tests + `pip-audit` clean (or documented accepted risk); avoid silent floating major upgrades in production images |

## Operational recommendations

- Use a unique strong `PIHERDER_MASTER_KEY` and `SECRET_KEY` (see [`.env.example`](.env.example) for the full env catalog).  
- Prefer SSH key auth; clear any stored SSH passwords after deploy.  
- Enable 2FA for admin accounts; consider **Force 2FA** in Settings.  
- Put PiHerder behind trusted TLS; restrict network access where possible.  
- Set `METRICS_TOKEN` if `/metrics` is reachable beyond a private scrape network.  
- Treat API tokens like passwords; revoke compromised tokens immediately.  
- Leave `CORS_ORIGINS` empty unless a browser on another origin must call `/api/v1`; never use `*`. CORS is not a substitute for Bearer + scopes + IP allowlists.  
- Keep herder self-backups on durable storage separate from the fleet hosts when practical.
