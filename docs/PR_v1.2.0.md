# PR: v1.2.0-dev → main

**Open:** https://github.com/bjorngluck/piherder/compare/main...v1.2.0-dev?expand=1

**Title:** `v1.2.0: passkeys, SSO, web SSH, gated demo, full-DB backup`

**Base:** `main` · **Head:** `v1.2.0-dev` · **Tag:** `v1.2.0`

---

## Summary

Second minor after production **v1.1.1**. Big identity + optional webshell + public demo, plus full-database self-backup and the Stream R security remediations.

Package version is **1.2.0**. README + wiki present this as current production. Merge, tag, and Hub `1.2.0` / `1.2` / `latest` remain the last ship steps.

| Stream | Highlights |
|--------|------------|
| **I · Passkeys** | WebAuthn as second factor after password or SSO (not passwordless). Register / nickname / revoke. Force 2FA satisfied by passkey or TOTP. |
| **S · SSO / OIDC** | Authorization code + PKCE. JIT, verified-email auto-link, group→role map. Require SSO hides password form; admins stay password break-glass. `email_verified` fail-closed. |
| **W · Web SSH** | Flag **off** by default. operator+ only; passkey step-up preferred; backup codes rejected. Fleet grant, parked resume, multi-host `/console`, mobile soft-key row (v15). |
| **D · Public demo** | `DEMO_MODE` sandbox: shared viewer, write guard, simulated console, audit IPs `redacted`, OpenAPI gated. |
| **B · Backup** | **B-retry** vanished rsync (24 / partial 23). **B-DR** Full = `pg_dump -Fc` of entire Postgres + `DATA_ROOT` (format **v6**). |
| **R · Security** | Loopback bind + trusted XFF · public-URL reset links · POST-only compose build · logout `session_version` · backup-code flash cookie · delete graphs · default SSH user `pi` · archive path confine · SSH host-key TOFU · compiled Tailwind (no `unsafe-eval`). |
| **Maps (QA)** | Direct TLS (no leftover NPM hop): persist **Use this project**, host-identity vs via-proxy, Kuma host-scoped. |

Full notes: [RELEASE_v1.2.0.md](RELEASE_v1.2.0.md) · operator QA: [QA_v1.2.0.md](QA_v1.2.0.md)

## Migrations (apply on deploy)

| Rev | Purpose |
|-----|---------|
| `037` | WebAuthn credentials |
| `038` | OIDC identities / link lifecycle |
| `039` | SSH host-key pin (TOFU) |

## Test plan

### Freeze / CI
- [ ] Unit suite green; coverage still ≥ **55%**
- [ ] Playwright E2E (shell, nav, login — no live SSH/nmap/NPM in CI)
- [ ] `mkdocs build --strict`
- [ ] Fresh deploy: migrate `037`–`039` on empty + upgrade from `v1.1.1`

### Operator QA (sign-off — [QA_v1.2.0.md](QA_v1.2.0.md))
- [x] Boot / weak `SECRET_KEY` refuses / loopback `:8000` / compiled Tailwind
- [x] Passkeys + TOTP + backup codes not in URL
- [x] SSO happy path + require-SSO + admin break-glass (or N/A)
- [x] Console flag off / viewer 403 / operator PTY; mobile Tab + soft keys
- [x] New host SSH user `pi`; host-key pin + reset
- [x] Full DR archive contains `database.dump`
- [x] Demo: viewer banner, no real onboard, simulated console
- [x] Direct TLS map (11.10) + core fleet regression (accepted; not every item fully validated)
- [x] Wiki screenshot refresh

## Out of scope (deferred)

- Per-host / per-feature roles (**v1.3 AC-fg**)
- Host file upload/download thin slice (**v1.3 F**)
- Service migration host→host (**v1.4 M**)
- CSP nonces / drop `unsafe-inline` (**v1.3**)
- Passwordless passkeys · SAML · session recording

## Merge checklist

- [x] Operator **Ready to tag `v1.2.0` = Yes**
- [x] Version bump `app/version_info.py` + `pyproject.toml` → **1.2.0**
- [x] Flip [RELEASE_v1.2.0.md](RELEASE_v1.2.0.md) + wiki Home to **Tagged / current production**
- [ ] Merge `v1.2.0-dev` → `main`
- [ ] Tag **`v1.2.0`** · Hub `1.2.0` / `1.2` / `latest`
- [ ] Keep `1.1` / `1.1.1` pins valid

## After merge

```bash
# Hub multi-arch (maintainer)
export IMAGE=bjorngluck/piherder VERSION=1.2.0
# see docs/PUBLISH_IMAGE.md — tags 1.2.0 / 1.2 / latest
```

GitHub Release from tag `v1.2.0` uses [RELEASE_v1.2.0.md](RELEASE_v1.2.0.md).
