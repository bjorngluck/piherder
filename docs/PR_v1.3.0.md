# PR: v1.3.0-dev → main

**Title:** `v1.3.0: operator policy, scale lists, Connect as…, Reports, Host Files`

**Base:** `main` · **Head:** `v1.3.0-dev` · **Tag:** `v1.3.0` (after merge)

---

## Summary

Third minor after production **v1.2.0**. Operator-owned password / 2FA policy, console knobs, fleet + privileged SSH, opt-in command audit, scale lists, alert policy, history **Reports**, and a confined **Host Files** manager (flag **off**).

Package version is **1.3.0**. README + wiki Home present this as current production. Merge, tag, and Hub `1.3.0` / `1.3` / `latest` remain the last ship steps. Keep `1.2.0` / `1.2` pins valid.

| Stream | Highlights |
|--------|------------|
| **P + T** | Settings → Security: password rules, force-2FA + grace 0–60 days, step-up windows. Account hub cards. SSO unlink confirmation sheet. Link SSO hops same-origin so CSP `form-action 'self'` can reach Authentik. |
| **W-cfg** | Settings → Console: idle / max / slots / ticket / park / bind / scrollback. Kill switch stays `PIHERDER_SSH_CONSOLE`. |
| **W-id** | Fleet + privileged SSH identities; console **Connect as…**; Settings who may elevate. Alembic `040_ssh_identities`. |
| **W-audit** | Opt-in Fernet command audit (`041`). Default off. Heuristic redaction. Demo never stores. |
| **L** | Pager + search on Servers, Docker stacks, discovery list. |
| **A** | Alert policy (severity / mute / debounce) + map/discovery types. |
| **N2** | `/reports` history (backups dest, OS patches, LAN live, Docker deploys, console sessions). Not Grafana widgets. |
| **F** | Confined Host Files manager (browse, transfer, edit, zip, perms, search, preview, thin `docker cp`). Flag `PIHERDER_HOST_FILES` default **off**. Demo never. |
| **Settings hub** | General cards + Edit sheets. |
| **Ops** | `web` restart `unless-stopped` so the UI returns after a host reboot. |

Honesty (scope bent, architecture held): [RELEASE_v1.3.0.md](RELEASE_v1.3.0.md) § Where the plan bent · [PLAN §0a](PLAN_v1.3.0.md#0a-where-the-plan-bent-not-broken).

Full notes: [RELEASE_v1.3.0.md](RELEASE_v1.3.0.md) · maintainer QA: [QA_v1.3.0.md](QA_v1.3.0.md)

## Migrations (apply on deploy)

| Rev | Purpose |
|-----|---------|
| `040` | Fleet + optional privileged SSH identities |
| `041` | Opt-in encrypted console transcripts |

## Test plan

### Freeze / CI
- [x] Unit suite green; coverage still ≥ **55%**
- [ ] Playwright E2E (Should — not re-run this freeze)
- [x] `mkdocs build --strict`
- [x] Upgrade path: Alembic `040`–`041` on existing 1.2 hosts

### Operator QA (sign-off — [QA_v1.3.0.md](QA_v1.3.0.md))
- [x] Boot / Alembic 040–041 / web `unless-stopped`
- [x] Password / 2FA policy (P + T) including Account hub + unlink sheet
- [x] Console knobs + identities + command audit
- [x] Scale lists (L)
- [x] Alerts policy (A)
- [x] Reports (N2)
- [x] Host Files (F)
- [x] Settings hub
- [x] Public demo (viewer tour)
- [x] 1.2 core regression
- [x] Wiki screenshot pack

## Out of scope (deferred)

- Host `tmux`/`screen` (**W-mux**)
- Fine-grained roles (**AC-fg**)
- Custom dashboard layout (**N3**)
- CSP nonces / drop `unsafe-inline`
- ACME-in-herder · extra branding
- Richer Files token API · demo simulated Files (**D-F**, v1.4)
- Service migration host→host (**v1.4 M**)

## Merge checklist

- [x] Operator **Ready to bump `1.3.0` and tag = Yes**
- [x] Version bump `app/version_info.py` + `pyproject.toml` → **1.3.0**
- [x] Flip [RELEASE_v1.3.0.md](RELEASE_v1.3.0.md) + wiki Home to current production (**Ready to tag** until merge)
- [ ] Merge `v1.3.0-dev` → `main`
- [ ] Tag **`v1.3.0`** · Hub `1.3.0` / `1.3` / `latest`
- [ ] Keep `1.2` / `1.2.0` pins valid

## After merge

```bash
# Hub multi-arch (maintainer)
export IMAGE=bjorngluck/piherder VERSION=1.3.0
# see docs/PUBLISH_IMAGE.md — tags 1.3.0 / 1.3 / latest
```

GitHub Release from tag `v1.3.0` uses [RELEASE_v1.3.0.md](RELEASE_v1.3.0.md).
