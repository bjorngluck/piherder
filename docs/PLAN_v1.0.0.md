# PiHerder v1.0.0 — first production release train

**Status:** **Tagged** (`v1.0.0` · 2026-07-28) — first production release  
**Date opened:** 2026-07-26 · **Train start:** 2026-07-28 · **Tagged:** 2026-07-28  
**Git tag:** `v1.0.0`  
**Package / image version:** `1.0.0`  
**Theme:** Production hardening · security · known-issue burn-down · polish for go-live  
**Baseline:** `v0.9.0` (tagged 2026-07-26 — last pre-production)  
**Related:** [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · [RELEASE_v0.9.0.md](RELEASE_v0.9.0.md) · [PLAN_v0.9.0.md](PLAN_v0.9.0.md) · [FEATURE_PLAN_IAM_2FA_UPDATES_NOTIFICATIONS.md](FEATURE_PLAN_IAM_2FA_UPDATES_NOTIFICATIONS.md) · [FEATURE_PLAN_PIHOLE_NPM_CERTS.md](FEATURE_PLAN_PIHOLE_NPM_CERTS.md) · [FEATURE_PLAN_LAN_NMAP.md](FEATURE_PLAN_LAN_NMAP.md) · [ADMIN.md](ADMIN.md) · [API.md](API.md) · [PUBLISH_IMAGE.md](PUBLISH_IMAGE.md) · [SECURITY.md](../SECURITY.md)

> **First production release.** Freeze product surface for self-hosted home/lab operators: secure defaults, clear auth, documented REST, stable templates schema, residual UX bugs closed. Deeper features (SSO, insights, full cert wizard, email) stay **post-1.0** unless explicitly pulled in.

---

## 0. Decision lock (train start)

| Choice | Value |
|--------|--------|
| Git tag | **`v1.0.0`** |
| Image tags | `1.0.0` · `1.0` · `latest` (multi-arch amd64 + arm64) |
| Planning frame | **Ship confidence** over new product surface — no half-built themes |
| Single target | All 1.0 work lands on `main` toward one tag (no intermediate `v0.9.x` required) |
| Optional intermediate tags | Only if a security fix must ship early (`v0.9.1` or `v1.0.0-rc.N`) — prefer stay on main |
| Coverage bar | **Maintain ≥ 55%** unit line (`--cov=app`); CI fail-under **55** |
| E2E policy | Surfaces **touched** in 1.0 get basic Playwright coverage; auth/security chrome required |
| Semver signal | **1.0.0** = production-supported surface; post-1.0 may still add features without breaking core contracts |
| Production path | ~~RC1–RC3~~ → ~~**v0.9.0** last pre-prod~~ **tagged** → **v1.0.0 this train** → **v1.1** residual |

### What “production” means for PiHerder 1.0

| Promise | Detail |
|---------|--------|
| **Installable** | Compose + published multi-arch Hub image; pin `1.0.0` or use `latest` |
| **Secure defaults** | Master key + secret required; password policy; 2FA path; step-up for recovery secrets; no anonymous version leak |
| **Authz confidence** | Mutating routes and streams audited against role matrix; API token scopes exercised; gaps fixed or documented |
| **Input hygiene** | Dangerous sinks validated (paths, actions, cron, sizes) — not a full Form→Pydantic rewrite |
| **Operable** | ADMIN + wiki + API.md accurate; upgrade path from 0.9 documented |
| **Stable core contracts** | Templates desired-state model, REST `/api/v1` token model, encryption/master-key restore story — no silent break |
| **Known residual** | Documented in RELEASE; scheduled for v1.1 / post-1.0, not hidden |

**Not promised at 1.0:** SSO, email, password-reset mail, insights/dashboards, full cert distribute wizard, HA REST/LLAT, web SSH, multi-tenant ACLs, validate-every-parameter schema layer.

---

## 1. Goal

1. **Close residual operator bugs** from the 0.9 QA triage (O, R, T, U, V, W).  
2. **Auth entry + security polish** so first-login and logged-out chrome are production-safe (F, AA).  
3. **DNS clarity** (X) so Network Records are operator-obvious.  
4. **Cert distribute (P)** — discovery + document edges only; full wizard → v1.1 if capacity slips.  
5. **Production authz confidence (**AC**)** — route × role matrix audit; fix gaps; lock with tests.  
6. **Risk-based input validation (**AV**)** — dangerous sinks only (paths, actions, cron, sizes).  
7. **Docs freeze** — RELEASE_v1.0.0, ADMIN prod checklist, wiki/screenshot truth, API.md aligned.  
8. **Quality hold** — unit ≥55%, E2E on touched auth + critical shells, green CI, multi-arch publish.

---

## 2. Ship bar (v1.0.0)

| # | Item | Notes | Status |
|---|------|--------|--------|
| 1 | Security hardening pack (**AA**) | Password policy review; force-2FA polish; cookie flags; CSRF on sensitive POSTs audit; rate limits; trusted-device copy. Already done in 0.9: hide version when anonymous; backup-code regenerate needs password + 2FA | **Done** (2026-07-28) |
| 2 | Auth entry UX (**F**) | Unauthenticated `/` → login (not empty dashboard + Sign in) | **Done** (2026-07-28) |
| 3 | Known-issue burn-down (**O, R, T, V, W**) | Docker link-out back; map second-click unlock; brand-on-buttons; Kuma coverage mobile; monitor mute chrome | **Done** (2026-07-28) |
| 4 | Mobile / dense tables (**U**) | NPM certificates list stackable cards on mobile | **Done** (2026-07-28) |
| 5 | DNS records clarity (**X**) | Host A vs Pi-hole A vs CNAME checklist — copy + optional deep links | **Done** (2026-07-28) |
| 6 | Authz matrix (**AC**) | Route × method × role audit; mutate + streams + admin + API scopes; fix gaps; smoke/matrix tests | **Done** (2026-07-28) |
| 7 | Input validation pack (**AV**) | Risk-based validators at sinks (paths, shell-ish actions, cron, body sizes, enums) — not every Form field | **Done** (2026-07-28) |
| 8 | Docs + wiki freeze | RELEASE_v1.0.0, ADMIN prod checklist, wiki production framing, dual-version wording cleared | **Done** (2026-07-28) |
| 9 | Quality | Unit ≥55%; E2E on touched auth/security + critical shells; REST smoke; CI green | **Done** (unit pack + v10 tests; E2E suite retained) |
| 10 | Admin credential recovery (**G2-lite**) | Admin reset temp password, clear 2FA, reset access, force session logout (`session_version`) — no email | **Done** (2026-07-28) |
| 10b | Host sole-admin recovery | `python -m app.cli.recover_admin` + wiki locked-out guide | **Done** (2026-07-28) |
| 10c | Avatar + trusted-device freeze bugs | Per-user avatar cache URL; trust cookie survives logout; no duplicate trust rows | **Done** (2026-07-28) |
| 11 | Publish | Tag `v1.0.0` + Hub multi-arch `1.0.0` / `1.0` / `latest` | **Done** (2026-07-28) |

**Stretch (capacity):** discovery refinements (**S**) if small.

---

## 3. Phased train (recommended order)

Work in **phases**, not a big-bang freeze. Each phase should leave `main` green.

### Phase A — Security & auth entry (priority 1)

| ID | Item | Effort | Direction |
|----|------|--------|-----------|
| **F** | Logged-out base URL → login | S | **Done** — `GET /` redirects to `/auth/login` when `user is None` |
| **AA** | Security hardening pack | M | **Done** — tighter login/2FA/register rate limits; shared cookie kwargs (`HttpOnly`/`SameSite=Lax`/`path=/`/`Secure`); same-origin POST middleware; weak `SECRET_KEY` startup warn; force-2FA + trusted-device copy polish |
| **AA-test** | Auth E2E / unit | S–M | **Done** — `tests/test_security_v10.py` + smoke for F |

**Exit:** Unauthenticated chrome is minimal; no silent privilege paths; CI green.

### Phase B — Known-issue burn-down (priority 1)

| ID | Issue | Effort | Direction |
|----|--------|--------|-----------|
| **O** | Docker → linked tool; browser **Back** shows stuck SSH “Collecting information…” until refresh | M | **Done** — bfcache `pageshow` hides overlay when stack ready; wait modal also clears on `pageshow` |
| **R** | Hosts / Path map desktop: second click should **release** focus (mobile OK) | S–M | **Done** — mouse mesh/list clicks use `forceToggle`; short mouse debounce |
| **T** | `ph_brand()` inside solid accent/danger buttons (e.g. light mode NPM “pull into PiHerder”) | S | **Done** — plain “PiHerder” text on primary/danger buttons (NPM pull, remove host) |
| **V** | Kuma **coverage** mobile: columns bleed | S–M | **Done** — `@media` card stack for coverage tables |
| **W** | Monitor **Mute** chrome vs **Unmute** | S | **Done** — shared accent chip buttons for Mute/Unmute |
| **U** | NPM **Certificates** mobile scroll-only table | S–M | **Done** — `ph-dense-*` card list on NPM certs tab |

**Exit:** Operator QA letters O/R/T/U/V/W closed or explicitly deferred with RELEASE note.

### Phase C — Network / DNS clarity (priority 2)

| ID | Item | Effort | Direction |
|----|------|--------|-----------|
| **X** | Network DNS Records meaning unclear | M | **Done** — legend + Host A / CNAME / External filters; deep links to host edit, path card, Path map |
| **S** *(stretch)* | Discovery last seen / offline / purge | S–M | Only if tiny; default **v1.1** |

**Exit:** An operator can read the DNS checklist without wiki spelunking.

### Phase D — Cert distribute discovery (priority 3 / soft)

| ID | Item | Effort | Direction |
|----|------|--------|-----------|
| **P** | Cert distribution sudoers + setup flow | S–M | **Done (discovery)** — setup page known-edges card; sudoers snippet expands `~/` + home-path notes. Full wizard → **v1.1** |

**Exit:** Known broken paths documented in RELEASE / wiki; no half-built wizard UI.

### Phase E — Production hardening: authz + input (priority 1 before freeze)

**Decision (2026-07-28):** AC + AV are **in scope for v1.0** as production hardening — not post-1.0 polish. Cap scope so they do not open multi-tenant RBAC or a full Form→Pydantic migration.

#### E1 — **AC** Authorization matrix audit

| # | Work | Notes |
|---|------|--------|
| **AC0** | Inventory | **Done** — multi-line signature scan; only public auth + metrics(token) + **two docker SSE** gaps |
| **AC1** | Mutate bar | **Done** — fleet POSTs require login; viewer 403 via `get_current_user` + prefix rules; matrix tests |
| **AC2** | Streams / HTMX | **Done** — log stream + build-stream required session; build-stream **operator+**; backup/os streams already authed |
| **AC3** | Admin surfaces | **OK baseline** — `/auth/users` admin-only (matrix test); herder-backups admin prefixes unchanged |
| **AC4** | API v1 scopes | **OK baseline** — `get_api_auth` + scopes (existing `test_api_tokens`); no new open endpoints |
| **AC5** | Tests | **Done** — `tests/test_authz_matrix_v10.py` + RBAC allowlist extensions |
| **AC6** | Fix or document | **Done** — critical SSE gaps fixed; metrics open-if-no-token remains documented ADMIN stance |

**In scope AC:** single-tenant role model (viewer / operator / admin) as today; path-prefix viewer write allowlist + explicit deps; API token scopes.

**Out of AC for 1.0:** multi-tenant / per-host ACLs; SSO; resource-level “user owns only server 3”; redesign of `_VIEWER_WRITE_PREFIXES` into a full policy engine.

**Exit AC:** Matrix artifact (table in plan appendix or `docs/` note) + tests green + no known unauthenticated mutate / unauthenticated stream.

#### E2 — **AV** Risk-based input validation pack

| # | Work | Notes |
|---|------|--------|
| **AV0** | Shared helpers | **Done** — `app/services/input_validation.py` |
| **AV1** | Path sinks | **Done** — cert `remote_dir`; server `docker_base_dir` via `safe_path` |
| **AV2** | Action / enum sinks | **Done** — docker container action, prune_type, cert layout/write_mode allowlists |
| **AV3** | Cron / schedule | **Done** — `validate_cron` uses `safe_cron` (+ pycron when present) |
| **AV4** | Size caps | **Done** — helpers `clamp_str` / `clamp_text_blob` (wire more PEM/compose bodies as capacity) |
| **AV5** | SSH identity | **Done** — server update hostname + ssh_username via `safe_hostname` / `safe_ssh_user` |
| **AV6** | Tests | **Done** — `tests/test_input_validation_v10.py` |

**In scope AV:** validators at **dangerous sinks** (paths that hit disk/SSH, shell-ish post-deploy, cron, large blobs, action enums).

**Out of AV for 1.0:** rewriting every `Form(...)` to Pydantic models; strict 422 JSON on all HTMX forms (prefer safe redirect + flash where UI expects it); OpenAPI-first schema for browser UI.

**Exit AV:** Shared validators landed; high-risk sinks call them; tests cover reject cases; no broad UI regression.

### Phase F — Docs freeze & tag (priority 1 at end)

| Item | Notes |
|------|--------|
| `RELEASE_v1.0.0.md` | Highlights, migration, verify checklist, freeze checklist; AC/AV residual if any |
| ADMIN prod checklist | TLS, keys, upgrades, backup/DR, force-2FA, image pin, SECRET_KEY |
| Wiki + screenshots | Recapture only surfaces that changed in 1.0; banner/install pin → `v1.0.0` |
| API.md | Align with shipped `/api/v1` (no OpenAPI rewrite — that is post-1.0 **Y**) |
| Version bump | `pyproject.toml` + `APP_VERSION` → `1.0.0` |
| Quality gate | `pytest` unit ≥55%; E2E green; `mkdocs build --strict` |
| Publish | Tag + Hub multi-arch per [PUBLISH_IMAGE.md](PUBLISH_IMAGE.md) |

---

## 4. Workstreams (summary)

| Stream | Phase | Focus | IDs |
|--------|-------|--------|-----|
| **Sec** | A | Auth entry + hardening | F, AA |
| **KI** | B | Known issues from 0.9 QA | O, R, T, U, V, W |
| **Net** | C | DNS clarity (+ optional S) | X, S |
| **Cert** | D | Discovery / docs only | P |
| **Authz** | E1 | Route × role matrix + fixes + tests | **AC** |
| **Valid** | E2 | Risk-based input validators at sinks | **AV** |
| **Docs** | F | RELEASE, ADMIN, wiki, API, tag | — |
| **Q** | continuous | Coverage hold, E2E on touch, CI | — |

---

## 5. Security & auth detail (v1.0)

| ID | Item | Stance |
|----|------|--------|
| **AA** | Cookie/rate/CSRF/force-2FA polish | **Done** (2026-07-28) |
| **F** | Base URL when logged out → **login** | **Done** — redirect when `user is None` |
| **AC** | Authorization matrix (route × role × API scopes) | **Done** — streams fixed; matrix tests |
| **AV** | Risk-based input validation at sinks | **Done** — shared helpers + high-risk sinks |
| **B** | Version in footer when not signed in | **Fixed in 0.9** — version only when authenticated |
| **E** | Generate backup codes without 2FA | **Fixed in 0.9** — modal + password + TOTP/backup code |

### Auth baseline (do not re-build)

Centralized model already in place — AC **audits and hardens**, it does not replace:

- `get_current_user` — JWT, 2FA-pending block, force-password / force-2FA gates  
- Mutating methods — viewer blocked except `_VIEWER_WRITE_PREFIXES`  
- Admin mutations — `_ADMIN_ONLY_PREFIXES` + `get_admin_user`  
- Operator-sensitive routes — `get_operator_user` where used  
- REST — Bearer tokens, scopes (`read`/`jobs`/`edit`/`feature:*`), optional IP allowlist  

### Validation baseline (extend, don’t boil the ocean)

Already partial: password policy, some cron validation, backup path denylists, PEM handling, model `max_length`. AV **concentrates** shared helpers on remaining dangerous sinks.

**Not v1.0 (backlog):** user self-password-reset (**G1**), email/SMTP (**H**), SSO/OIDC (**Z**), full Form→Pydantic migration, multi-tenant RBAC.

**In 1.0 (G2-lite):** admin OOB credential recovery on Users — temp password + must change, clear 2FA, full reset access, `session_version` force-logout. No SMTP.

---

## 6. Explicitly out of v1.0 (→ backlog / post-1.0)

| ID | Item | Destination |
|----|------|-------------|
| **G1** | User self-service forgot / reset password | **v1.1+** (needs email story) |
| **G2-mail** | Admin reset via email OTP / invite mail | **v1.1+** with **H** |
| **H** | Email integration, notification channels, password-reset mail | Post go-live / **v1.1–v1.2** |
| **I** | Broader “after go-live” parking lot | ROADMAP |
| **J** | Favourites / shortcuts | Post-1.0 |
| **K** | Cross-host same-feature jump | Post-1.0 |
| **L** | Docker quick editor for `.env` / sidecars | **Lean no** for quick editor; full editor only |
| **M** | Templates fleet-wide “which hosts have this template” | Post-1.0 / templates plan |
| **N** | Insights, reporting, custom dashboards | Discovery **post v1.0** |
| **Q** | Onboard service: full git clone/pull; more files | Post-1.0 templates |
| **S** | Discovery: last seen, offline polish, purge/hide | **v1.1** default (stretch if tiny) |
| **Y** | API management: OpenAPI polish, bearer testing | Post-1.0 |
| **Z** | SSO / OIDC | Discovery post-1.0 |
| **AB** | Trusted devices: type, last IP, rename | Post-1.0 |
| **HA4+** | HA REST/LLAT, path 2 component, add-ons | Later HA track (not 1.0 ship bar) |
| **E6 / E9 / E11 full** | Human-readable schedules; hero stats; full templates catalog redesign | Post-1.0 platform |
| **AV-full** | Validate every Form/Query with Pydantic models across all routers | Post-1.0 platform (AV 1.0 is sink-only) |
| **AC-tenant** | Per-user host ACLs / multi-tenant isolation | Not product goal for 1.0 homelab |

---

## 7. Quality bar (continuous)

| Gate | Target |
|------|--------|
| Unit coverage | **≥ 55%** line on `app` (hold from 0.9; ~57% baseline) |
| CI fail-under | **55** |
| E2E | Touched 1.0 chrome + auth entry; baseline shell/wizard/viewer/LAN remain green |
| HTTP smoke | Extend if new routes; **AC** matrix cases (viewer mutate, admin-only) |
| Authz / validation unit | **AC** role matrix helpers; **AV** pure validators + reject paths |
| Docs | `mkdocs build --strict` at freeze |
| No live labs in CI | Still no live SSH / nmap / HA |

**Coverage tactics:** pure unit for auth helpers, validators, map toggle; mocked paths for O (pageshow). No router-% farming.

---

## 8. Freeze checklist (maintainer — at tag)

- [x] Phase A (F + AA) done and tested  
- [x] Phase B (O, R, T, U, V, W) done or explicitly deferred in RELEASE  
- [x] Phase C (X) done  
- [x] Phase D (P): discovery notes landed; no half-built wizard  
- [x] Phase E1 (**AC**): authz matrix + fixes + tests  
- [x] Phase E2 (**AV**): sink validators + tests  
- [x] Phase G2-lite: admin credential recovery  
- [x] Host sole-admin recovery CLI + wiki  
- [x] Avatar / trusted-device freeze bugfixes  
- [x] Dual-version / train / RC operator wording cleared (docs + wiki) — **§8.1**  
- [x] `RELEASE_v1.0.0.md` finalized Status **Tagged**  
- [x] Unit pack green (fail-under 55; v10 suites)  
- [x] `mkdocs build --strict` (at freeze)  
- [x] Screenshot pack complete for 1.0 (operator-confirmed; P0 surfaces + full wiki pack) — **§8.2**  
- [x] ADMIN / SECURITY / install framing final pass  
- [x] Bump `pyproject.toml` + `APP_VERSION` → `1.0.0`  
- [x] Tag `v1.0.0` + Hub multi-arch (`1.0.0` / `1.0` / `latest`)  
- [x] ROADMAP release track row → **Tagged**

### 8.1 Dual-version / train wording — **cleared on operator docs (2026-07-28)**

Operator-facing docs now describe **v1.0.0 first production**. Remaining at **git tag / Hub publish** only:

| Still at tag time | Done in docs sweep |
|-------------------|--------------------|
| Package `APP_VERSION` / `pyproject.toml` → `1.0.0` | Wiki home, banner, install, upgrades, README, SECURITY, CONTRIBUTING, ADMIN pin |
| GitHub release + Hub `1.0.0` / `1.0` / `latest` | `RELEASE_v1.0.0.md` drafted; ROADMAP production path updated |
| PLAN Status → **Tagged** | Dual “train / 0.9 + main” messaging removed from operator surfaces |

Historical RELEASE / PLAN files for 0.x **stay as-is** (archive). Do not rewrite past tags.

### 8.2 Screenshots to recapture for v1.0 (operator)

**Status (2026-07-28):** Operator confirmed **all screenshots in place** for the `v1.0.0` freeze (wiki `assets/screenshots/` pack).  

Full inventory: [wiki/assets/screenshots/README.md](../wiki/assets/screenshots/README.md). For **1.0**, priority surfaces that **changed on this train** (reference list — complete):

| Priority | File(s) | Why (1.0 chrome) |
|----------|---------|------------------|
| **P0** | `dns-hub.png` | DNS records card copy + Host A / CNAME / External modal |
| **P0** | `dns-coverage.png` (+ optional `*-mobile`) | Stacked mobile cards; Mute/Unmute chip parity |
| **P0** | `certificates-setup.png` | Known-edges / cert distribute discovery card |
| **P0** | `integrations-npm.png` (certs tab if visible) | Dense cert rows; “Pull into PiHerder” plain text |
| **P0** | `account-2fa.png` | `#account-2fa`, backup-codes modal, trusted-device copy |
| **P0** | `users-admin.png` (or recapture users) | Recovery actions: Reset password / Clear 2FA / Reset access / Sign out sessions |
| **P1** | `dns-logical.png` / `dns-physical.png` | Second-click unlock still true; connector focus |
| **P1** | `docker-logs-modal.png` / build progress if captured | Auth still required for SSE (no product change visible — recapture only if UI drifted) |
| **P1** | `dashboard.png` (+ optional dark) | Logged-in only path; no anonymous dashboard story |
| **P2** | Rest of pack | Only if prose claims differ from PNG; no full matrix required |

**Pre-capture:** rebuild/restart **web** so image matches freeze commit (compose does not bind-mount `app/`). Light theme · desktop default · redact secrets.

**After PNGs:** update captions if needed · `mkdocs build --strict` · commit binaries with freeze PR.

### 8.3 Production note (operator-facing truth at freeze)

When `RELEASE_v1.0.0.md` ships, operators should see a **single** story:

1. Tag **`v1.0.0`** · image **`bjorngluck/piherder:1.0.0`** (also `1.0`, `latest`).  
2. Upgrade from **0.9.0** = self-backup → pull/tag → `docker compose up -d` (same master key).  
3. Wiki home / banner / install pin all say **1.0.0** — no “pre-production” or “train on main”.  
4. Residual known issues (if any) listed once in RELEASE, not as dual version tables.

---

## 9. Suggested implementation order (day-to-day)

1. ~~**F** — auth redirect~~ **Done**  
2. ~~**T, W** — pure UI chrome~~ **Done**  
3. ~~**R, U, V** — map + dense mobile~~ **Done**  
4. ~~**O** — Docker back / SSH wait~~ **Done**  
5. ~~**AA** — security pack~~ **Done**  
6. ~~**X** — DNS copy/deep links~~ **Done**  
7. ~~**P** — cert discovery notes~~ **Done**  
8. ~~**AC** — authz matrix~~ **Done** (SSE auth + matrix tests)  
9. ~~**AV** — sink validators~~ **Done** (helpers + high-risk sinks)  
10. ~~**Docs / version / tag / publish**~~ **Done** (`v1.0.0`)

---

## 10. Breaking / migration expectations

| Topic | Expectation for 1.0 |
|-------|---------------------|
| Alembic | Prefer no surprise operator gates; document any new migration |
| Master key | Unchanged — same `PIHERDER_MASTER_KEY` for restore/DR |
| REST tokens | Compatible scopes; document any new endpoints only |
| Templates schema | Stable desired-state Vn model; no silent schema break |
| Upgrade from 0.9 | Pull image / checkout tag, keep `.env` + volumes, `docker compose up -d` |

---

## 11. Changelog (planning)

| Date | Note |
|------|------|
| 2026-07-26 | Plan opened from operator triage letters A–AB. Bugs A–E fixed on 0.9 train before 1.0. Known issues and backlog slotted. |
| 2026-07-28 | **Train official.** Baseline `v0.9.0` tagged. Expanded plan: decision lock, production promises, phased order A–E, quality hold, freeze checklist, day-to-day sequence. Status → **Active**. |
| 2026-07-28 | First build slice: **F, O, R, T, U, V, W** landed. Remaining: **AA**, **X**, **P** discovery, docs freeze. |
| 2026-07-28 | Second slice: **AA**, **X**, **P** discovery landed. Remaining: docs freeze / tag / publish. |
| 2026-07-28 | **Decision:** production hardening continues with **AC** (authorization matrix) + **AV** (risk-based input validation) **in 1.0** before freeze. Explicitly out: full Form→Pydantic, multi-tenant ACLs. Phase E added; docs freeze renumbered to Phase F. |
| 2026-07-28 | **AC + AV implemented.** Critical: docker log SSE + build SSE required auth (build = operator+). Shared `input_validation` + wired server update, cert maps, docker actions/prune, cron. Tests: `test_authz_matrix_v10`, `test_input_validation_v10`. Remaining: docs freeze / tag. |
| 2026-07-28 | **§8.1–8.3 freeze notes:** dual-version / train wording to **remove at tag**; screenshot recapture list for 1.0 surfaces; single production story after RELEASE. |
| 2026-07-28 | **G2-lite** in 1.0: admin reset password, clear 2FA, reset access, sign-out sessions (`User.session_version` in JWT). Email self-reset (**G1**) + SMTP (**H**) remain **v1.1+**. Tests: `test_admin_credential_recovery_v10`. |
| 2026-07-28 | **Docs/wiki production sweep:** login sessions-revoked flash; Users **Recover…** menu; `RELEASE_v1.0.0.md`; dual-version / RC / train wording → **v1.0.0 first production** on home, banner, install, upgrades, README, SECURITY, CONTRIBUTING, ADMIN pin. Remaining freeze: screenshots, version bump, tag/Hub. |

---

## 12. Appendix — AC matrix template (fill during E1)

Working table (maintainer). Mark each row: **OK** / **Fix** / **Public** / **N/A**.

| Area | Example routes | Intended auth | Notes |
|------|----------------|---------------|--------|
| Public chrome | `/auth/login`, `/register`, `/health`, static, SW | None / optional | Keep minimal |
| Fleet read | `/`, `/servers`, `/dns`, integrations GET | Logged-in (any role) | Viewer OK |
| Fleet mutate | backup, patch, docker, templates deploy, DNS sync | Operator+ (viewer 403) | Central middleware + deps |
| Admin | users, settings, API tokens, herder restore | Admin | |
| Streams | docker logs/build stream | Same as parent server ops | **Must not skip cookie** |
| Account self | password, 2FA, push, trusted devices | Logged-in self | Viewer write allowlist |
| API v1 | `/api/v1/*` | Bearer + scopes | Parallel to UI matrix |

**Spot-check first (highest risk):** any POST without `Depends(get_*_user)`; SSE/stream GETs; metrics if token unset; wizard and bulk actions.

**End of plan** — frozen at tag into [RELEASE_v1.0.0.md](RELEASE_v1.0.0.md). Active development: [PLAN_v1.1.0.md](PLAN_v1.1.0.md) on branch `v1.1.0-dev`.
