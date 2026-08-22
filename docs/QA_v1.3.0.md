# PiHerder v1.3.0 — operator QA / sign-off

**Branch:** `v1.3.0-dev` → `main` · tag **`v1.3.0`** (not cut yet)  
**Code freeze:** **feature freeze 2026-08-22** — no new product streams.  
**Hub / `latest`:** still **1.2.0** until this tag. Package on the branch stays `1.2.0` until the bump.

Use this list as a **real operator pass**, not a unit-test dump. Tick **Pass / Fail / N/A**. Anything **Must** that fails is a ship blocker unless you explicitly accept it.

This file is **maintainer-only** (repo `docs/`). It is **not** published on the operator wiki — how-to pages stay in `wiki/`; freeze checklists stay here.

Plan: [PLAN_v1.3.0.md](PLAN_v1.3.0.md). Screenshots: [wiki/assets/screenshots/README.md](../wiki/assets/screenshots/README.md).

1.2 production sign-off stays [QA_v1.2.0.md](QA_v1.2.0.md) (historical). Deep identity/SSO/console-flag rows that did not change still live there — this file **re-runs the regression table** and fully tests **new 1.3 surfaces**.

---

## How to run this

| | |
|--|--|
| **Instance** | Rebuilt **`v1.3.0-dev`** stack on this host (`docker compose build web && docker compose up -d`). About still shows **1.2.0** until the tag bump. |
| **Browsers** | Desktop Chrome or Firefox **and** one phone (Files, console, Settings sheets) |
| **Accounts** | One **admin**, one **operator** (2FA / passkey enrolled), one **viewer** |
| **Hosts** | At least one real SSH host; optional second host; optional HAOS |
| **Flags** | Console and Files default **off**. Turn each **on only for its section**, then restore production intent. |

Record: date · who · URL · image/commit (`docker compose exec web cat /app/app/version_info.py` or About).

Legend: **Must** = ship blocker · **Should** = fix or accept in notes · **Reg** = 1.2 behaviour that must not regress.

Allowed during freeze: bugs that fail this list, docs, screenshots, coverage. **Not** allowed: new features, Cap pull-ins (W-mux, AC-fg, N3, CSP nonces, ACME).

---

## 0. Boot, install, upgrade

| # | Pri | Test | Pass |
|---|-----|------|------|
| 0.1 | Must | Stack healthy: `web`, `db`, `redis`, `celery-worker`, `caddy`. About / footer (signed in) shows the branch version string (**1.2.0** until tag). | **Pass** |
| 0.2 | Must | Alembic applied, including **`040_ssh_identities`** and **`041_console_transcripts`**. No migration error in `web` logs. Existing host still has a **fleet** identity (username unchanged). | **Pass** |
| 0.3 | Must | Hard-refresh: layout/theme look normal (compiled Tailwind). Light **and** dark. | **Pass** |
| 0.4 | Must | After this upgrade: **Settings → PiHerder backup → Full DR** succeeds; archive contains `database.dump`. Copy off-box. | **Pass** |
| 0.5 | Must | Compose does **not** inject defaulted `PIHERDER_SSH_CONSOLE_*` knobs. Only `PIHERDER_SSH_CONSOLE` (and Files master enable) in compose env. Settings apply for idle/max/slots. | **Pass** |
| 0.6 | Must | `SECRET_KEY` long random — web **starts**. Weak default still **refuses** unless `PIHERDER_ALLOW_INSECURE` / demo. | **Pass** |
| 0.7 | Must | `:8000` loopback only. UI via Caddy. | **Pass** |
| 0.8 | Should | `PIHERDER_PUBLIC_URL` matches the address bar. | **Pass** |
| 0.9 | Must | `web` (and db/redis/celery/caddy) restart policy is `unless-stopped`. After a **host reboot**, the UI returns without `docker compose up`. Existing lab: `docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' piherder-web`. | **Pass** |

---

## 1. Password policy + 2FA settings (P + T)

| # | Pri | Test | Pass |
|---|-----|------|------|
| 1.1 | Must | Settings → General → **Security** card → Edit. Change min length (still ≥ 8, ≤ 72) **and** require special. Forms (Account change, admin create, admin reset, first-login, self-service reset) show the **same** live rules. Strength meter **needs** hints match (not hardcoded min 10). | ☐ |
| 1.2 | Must | A password that fails the new rule is **rejected** with the same text. Meter lists what is missing (e.g. min 12, special). Policy change is **audited**. | ☐ |
| 1.3 | Must | Force-2FA scope + grace **0–60** days save. User in grace can postpone; after grace they hit `/auth/force-2fa`. | ☐ |
| 1.4 | Must | Account **SSO unlink** and **passkey revoke** accept **any enrolled 2FA** (passkey **or** TOTP) — KI-account-stepup-factors. | ☐ |
| 1.5 | Must | Console / privileged Files step-up: **Passkey first** when enrolled; TOTP fallback unless Settings requires passkey. Backup codes **rejected** for these grants (default). | ☐ |
| 1.6 | Should | IdP-MFA skip stays **off** unless you opted in; default fail-closed. | ☐ |
| 1.7 | Reg | Passkey add / sign-in / TOTP / backup codes (not in URL) still work as in 1.2. | ☐ |
| 1.8 | Must | Account hub: equal cards + **Edit** sheets (same as Settings). SSO **Unlink…** opens a confirmation sheet (issuer/email, 2FA or set-password, Cancel / Unlink SSO). Not a silent POST and not a browser `confirm()` only. | ☐ |
| 1.9 | Must | Failed unlink / password-remove step-up **stays on that sheet** with a real hint (Confirm passkey or 6-digit / backup). Must **not** dump onto the authenticator-app sheet with “Authenticator or backup code required.” | ☐ |

---

## 2. Console knobs + identities + command audit (W-cfg / W-id / W-audit)

Enable `PIHERDER_SSH_CONSOLE=true` for this section.

| # | Pri | Test | Pass |
|---|-----|------|------|
| 2.1 | Must | Settings → **Console**: idle / max session / concurrency / ticket / hold / bind / scrollback save without compose edits. | **Pass** |
| 2.2 | Must | **Who may elevate** (admin default, or operator) is honoured: operator is refused privileged **Connect as…** when set to admin-only. | **Pass** |
| 2.3 | Must | Host **SSH access**: fleet identity present; optional **privileged** user/key saves separately. Jobs still use **fleet**. | **Pass** |
| 2.4 | Must | Console **Connect as…** fleet works. Privileged requires **fresh 2FA** (grant cookie). Viewer **403**. Flag off: Console hidden. | **Pass** |
| 2.5 | Must | Command audit **off** (default): no transcript rows. Privileged can still open (warn if audit off, still allow unless require-all is on). | **Pass** |
| 2.6 | Must | Command audit **on**: a short session records commands (heuristic redaction). Operator+ can read; **viewer 403**. Demo never persists. | **Pass** |
| 2.7 | Should | **Require audit on every session**: if recording cannot start, live shell is **refused**. | **Pass** |
| 2.8 | Should | JSON config-only herder backup **skips** transcript bodies. | **Pass** |
| 2.9 | Reg | Popup / workspace Maximize, mobile soft keys, park/resume, single-use ticket — 1.2 console behaviour still holds. | **Pass** |

Turn the console flag back to your production intent when done.

---

## 3. Scale lists (L)

| # | Pri | Test | Pass |
|---|-----|------|------|
| 3.1 | Must | **Servers** list: page size 10/20/50/100 (cookie `ph_per_page`), pager, `q` matches name / hostname / IP / DNS / SSH user (aliases e.g. `ha`). | **Pass** |
| 3.2 | Must | **Pins first** sort. Reorder only when All + empty search. | **Pass** |
| 3.3 | Must | **Docker** stacks list and LAN Discovery **list** use the same pager + `q`. Maps stay unpaged. | **Pass** |
| 3.4 | Must | `GET /api/v1/servers` honours `q` / `limit` / `offset` (cap 100) and returns `total`. | **Pass** |
| 3.5 | Reg | A small fleet still loads without the pager looking broken. | **Pass** |

---

## 4. Alerts policy (A)

| # | Pri | Test | Pass |
|---|-----|------|------|
| 4.1 | Must | Settings → Alerts → **Alert policy** Edit: mute / severity / debounce per category save. | **Pass** |
| 4.2 | Must | Channel (webhook / SMTP) respects category allowlist / mute. | **Pass** |
| 4.3 | Should | Map / discovery attention uses the documented severities (not a silent default). | **Pass** |
| 4.4 | Reg | SMTP test send + webhook test still work. | **Pass** |

---

## 5. Reports (N2)

| # | Pri | Test | Pass |
|---|-----|------|------|
| 5.1 | Must | Header **Reports** (`/reports`) is visible to **viewer+**. No writes. | **Pass** |
| 5.2 | Must | Windows **7 / 30 / 90**. Backups / OS / LAN live / Docker / Console tabs render (empty-state OK if no jobs). | **Pass** |
| 5.3 | Must | After a real backup job: dest size / success appear (not Grafana). Console tab uses Audit open/close, not live PTY. | **Pass** |
| 5.4 | Should | Phone: Reports usable (tables scroll, hero not broken). | **Pass** |

---

## 6. Host Files (F)

Default **`PIHERDER_HOST_FILES=false`**. Turn **on** for this section, rebuild/restart web.

| # | Pri | Test | Pass |
|---|-----|------|------|
| 6.1 | Must | Flag **off**: Files button **404** / hidden. Viewer **403** even when on. Demo never. | **Pass** |
| 6.2 | Must | Flag **on**, operator: host overview **Files** button (next to Console). Fleet **nav** present (Dashboard / Servers / ☰) — never a fake dest strip. | **Pass** |
| 6.3 | Must | Hero: hostname · Fleet (user) · **Limited access** (not “jailed SFTP”). Privileged: **Elevated access**. | **Pass** |
| 6.4 | Must | Path bar: **no `//`**. Separators **green** (accent). Privileged root is `/home/…` not `/ / home`. | **Pass** |
| 6.5 | Must | List **scrolls in the pane** (page does not grow). **Maximize** hides the hero; Restore brings it back; remembered. Phone: expand is an obvious change. | **Pass** |
| 6.6 | Must | Name tap opens folder / editor / preview. Row click selects. Phone **long-press** selects. | **Pass** |
| 6.7 | Must | Upload files + folder tree; download progress past ~12 MiB; overwrite confirm. | **Pass** |
| 6.8 | Must | Edit UTF-8 ≤ 512 KiB, Ctrl/Cmd+S. Privileged save of a root-owned file uses **`sudo -n tee`** (or a clear editor error — not a raw PermissionError after Close). | **Pass** |
| 6.9 | Must | Zip **on the host**; optional download; “delete after download” removes the **zip**, not originals. Unzip refuses `..`. | **Pass** |
| 6.10 | Must | Search: **Enter** or Search (not each keystroke). **In files** greps UTF-8. `.env` / PEM **list** but open/download/preview/content-search need 2FA (Passkey). | **Pass** |
| 6.11 | Must | Preview images: ‹ › (or arrows). **Loading** overlay until the picture arrives. | **Pass** |
| 6.12 | Must | chmod/chown names; privileged `sudo -n` when not root. Recursive folder delete confirms. | **Pass** |
| 6.13 | Must | Docker mounts: container → bind + named volume. Browse in-jail binds on fleet; named volume `_data` needs privileged. | **Pass** |
| 6.14 | Must | Settings → **Files** cap (0.25–32 GiB) unless env locks. Oversize upload refused. | **Pass** |
| 6.15 | Must | Toolbar **⋯** only for New folder / Docker / Refresh / identity / Maximize — no floating extra buttons. | **Pass** |
| 6.16 | Should | API token scope `files`: fleet list/get/put/mkdir/rename/empty-delete only. Privileged / zip / edit denied. | **Pass** |
| 6.17 | Should | HAOS: fleet jail may be tight; privileged reaches `/mnt/data` if that SSH user can. | **Pass** |

Turn **`PIHERDER_HOST_FILES`** back to **false** unless you are shipping it on for this fleet.

---

## 7. Settings hub (IA)

| # | Pri | Test | Pass |
|---|-----|------|------|
| 7.1 | Must | General is **cards + Edit modals** (Security, Console, Files, SSO, Cleanup). Timezone stays on the page. | **Pass** |
| 7.2 | Must | Phone: Edit is a **full-height sheet**; title + Save stay put; body scrolls. | **Pass** |
| 7.3 | Must | Operator hitting Settings mutate routes is **403**; viewer too. | **Pass** |
| 7.4 | Should | `#settings-console` / Files bookmark opens the right modal. | **Pass** |

---

## 8. Public demo (Reg)

| # | Pri | Test | Pass |
|---|-----|------|------|
| 8.1 | Must | Demo banner; no real Files tree; no console transcripts; no privileged live SSH. | ☐ |
| 8.2 | Must | `/docs` · `/redoc` · `/openapi.json` still **404** on demo. | ☐ |
| 8.3 | Reg | Shared viewer cannot add a server / mint a token. | ☐ |

---

## 9. Core fleet regression (1.2 must still work)

Spot-check. Failures here **block** 1.3 even if new surfaces pass.

| # | Pri | Test | Pass |
|---|-----|------|------|
| 9.1 | Reg | **Viewer:** read dashboard / servers / jobs / docker inventory / Reports; **cannot** start backup, patch, deploy, Users, Settings mutate, Files, Console. | ☐ |
| 9.2 | Reg | **Operator:** fleet jobs work; **cannot** Users / herder restore / SSO / API tokens. | ☐ |
| 9.3 | Reg | **Admin:** Users recovery, Force 2FA, SSO, Full DR, Status. | ☐ |
| 9.4 | Reg | One host: **Test connection** (pin), manual **backup**, **OS / HAOS check**. Job + audit. | ☐ |
| 9.5 | Reg | Docker inventory, logs, compose **Build**, full editor, deploy/start/stop. | ☐ |
| 9.6 | Reg | Add-server default SSH user **`pi`**. Host-key mismatch refused until reset pin. | ☐ |
| 9.7 | Reg | Sign out logs out other tabs. Admin “sign out sessions” kicks that user. | ☐ |
| 9.8 | Reg | SSO (if enabled): login button, JIT/map, Require SSO hides password for non-admin, admin break-glass password still works. | ☐ |
| 9.9 | Reg | Maps / Path map / LAN discovery / certs list open. | ☐ |
| 9.10 | Reg | Template catalog lists; deploy wizard opens (need not apply). | ☐ |
| 9.11 | Reg | Notifications bell + (HTTPS) Web Push toggle. | ☐ |
| 9.12 | Reg | CSP still has **no** `unsafe-eval`. Dashboard/Servers/Docker/Settings/Account render desktop **and** phone. | ☐ |
| 9.13 | Reg | Full DR restore story (lab): same `PIHERDER_MASTER_KEY`; login + hosts present. | ☐ |
| 9.14 | Should | Direct TLS (no NPM) path still selectable on a host FQDN app. | ☐ |
| 9.15 | Should | Empty-DB first register still creates the only admin (throwaway compose). | ☐ |

Deep 1.2 identity matrix (passkey RP, Require SSO edge cases): [QA_v1.2.0.md](QA_v1.2.0.md) §§1–2 if you still run SSO in production.

---

## 10. Docs / wiki / screenshots / automation

| # | Pri | Test | Pass |
|---|-----|------|------|
| 10.1 | Must | `pytest -q` unit pack green; coverage **≥ 55%**. | ☐ |
| 10.2 | Must | `mkdocs build --strict` green after wiki + screenshot caption edits. | ☐ |
| 10.3 | Must | Screenshot pack for **new 1.3 surfaces** landed or explicitly deferred in [screenshots README](../wiki/assets/screenshots/README.md) (do not ship captions that lie). | ☐ |
| 10.4 | Should | Playwright e2e: Settings policy save · list page-size · connect-as confirm · Files 404 when flag off. | ☐ |

---

## 11. Accept as known (do **not** fail the freeze)

| Item | Stance |
|------|--------|
| Package / About still **1.2.0** on `v1.3.0-dev` | Until tag bump |
| **`PIHERDER_HOST_FILES` default off** | GA opt-in; demo never |
| Command audit default **off** | **Known:** redaction is heuristic and imperfect (`read -s`, editors, pasted secrets). Do not fail freeze for that. Documented on the console wiki. |
| **KI-console-mobile-soft-tab** | Residual IME may remain |
| CSP `unsafe-inline` | Cap (nonces) — not this tag |
| No per-host ACL | **AC-fg** Cap |
| No custom dashboard layout | **N3** Cap |
| No host `tmux`/`screen` | **W-mux** Cap |
| Richer Files API | **v1.4+** |
| Service migration | **v1.4** |
| Screenshots may still be 1.2 chrome on unchanged pages | Recapture only rows in the 1.3 inventory |

---

## Sign-off

| Gate | Name | Date | Result |
|------|------|------|--------|
| Boot / upgrade / Alembic 040–041 | Björn | 2026-08-22 | **Pass** |
| Password / 2FA policy (P + T) | | | ☐ (1.8/1.9 unlink + step-up sheet — retest) |
| Console knobs + identities + audit | Björn | 2026-08-22 | **Pass** |
| Scale lists (L) | Björn | 2026-08-22 | **Pass** |
| Alerts policy (A) | Björn | 2026-08-22 | **Pass** |
| Reports (N2) | Björn | 2026-08-22 | **Pass** |
| Host Files (F) | Björn | 2026-08-22 | **Pass** |
| Settings hub | Björn | 2026-08-22 | **Pass** |
| Demo | | | ☐ |
| 1.2 core regression | | | ☐ |
| Docs / wiki / screenshots / coverage | | | ☐ |
| **Ready to bump `1.3.0` and tag** | | | ☐ |

### After **Yes** (maintainer — not operator browsing)

1. Bump `app/version_info.py` + `pyproject.toml` → `1.3.0`.  
2. Finish [RELEASE_v1.3.0.md](RELEASE_v1.3.0.md) (freeze **draft** already has § Where the plan bent); flip wiki Home + this file to **Tagged**.  
3. Merge `v1.3.0-dev` → `main` · tag `v1.3.0` · Hub `1.3.0` / `1.3` / `latest` (keep `1.2` / `1.2.x` pins valid).  
4. Do **not** start v1.4 service migration on the tag commit.

---

## Related

- [PLAN_v1.3.0.md](PLAN_v1.3.0.md)  
- [QA_v1.2.0.md](QA_v1.2.0.md) (production Hub until this tag)  
- [SECURITY.md](../SECURITY.md)  
- [wiki Host Files](../wiki/day-to-day/host-files.md) · [Reports](../wiki/day-to-day/reports.md) · [Settings](../wiki/operations/settings.md) · [upgrades](../wiki/operations/upgrades.md)  
