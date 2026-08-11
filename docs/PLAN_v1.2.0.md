# PiHerder v1.2.0 — big identity + webshell + gated demo

**Status:** **Active** — branch `v1.2.0-dev`  
**Date opened:** 2026-08-08  
**Git branch:** `v1.2.0-dev` (integration) · merge → `main` at freeze → tag `v1.2.0`  
**Package / image version (at tag):** `1.2.0`  
**Theme:** Big identity + webshell + gated demo — WebAuthn · SSO/OIDC · web SSH · `DEMO_MODE` public demo  
**Baseline:** `v1.1.0` (current production — 2026-08-08)  
**Mode:** Capacity-rich train — pull former **v1.3** items into **v1.2**  
**Related:** [RELEASE_v1.1.0.md](RELEASE_v1.1.0.md) · [PLAN_v1.1.0.md](PLAN_v1.1.0.md) §6 · [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · [FEATURE_PLAN_HOST_LIFECYCLE.md](FEATURE_PLAN_HOST_LIFECYCLE.md) P5 · [FEATURE_PLAN_IAM_2FA_UPDATES_NOTIFICATIONS.md](FEATURE_PLAN_IAM_2FA_UPDATES_NOTIFICATIONS.md) · [FEATURE_PLAN_SSO_OIDC.md](FEATURE_PLAN_SSO_OIDC.md) · [ADMIN.md](ADMIN.md) · [API.md](API.md) · [SECURITY.md](../SECURITY.md)

> **Big minor after 1.1.** Ship WebAuthn/passkeys, SSO/OIDC, webshell (flag-off by default), and a Cloudflare Access–gated demo product. Prefer **correct security bar** over half-gated surfaces. Keep `main` patchable for **v1.1.x** while this train runs on `v1.2.0-dev`.

---

## 0. Decision lock (train open)

| Choice | Value |
|--------|--------|
| Integration branch | **`v1.2.0-dev`** |
| Production line | **`main` @ `v1.1.0`** — hotfixes → **`v1.1.x`**, port into `v1.2.0-dev` |
| Git tag (freeze) | **`v1.2.0`** (RCs: `1.2.0-rc.N` if needed) |
| Image tags (freeze) | `1.2.0` · `1.2` · `latest` (multi-arch); keep `1.1` / `1.1.x` pins valid |
| In-scope streams | **I** WebAuthn · **S** SSO/OIDC · **W** webshell · **D** demo platform · **B-retry** backup vanished-file retry · **B-DR** herder self-backup full DB (`pg_dump`) · **Q** quality/freeze |
| Out-of-focus | Multi-tenant SaaS · SAML · ACME-in-herder · session recording · passwordless-only passkeys · residual Cap unless I/S/W/D green |
| Mode | Security-first · parallel foundations · no half-built auth surfaces |
| Coverage | **≥ 55%** unit; focused tests for WebAuthn, OIDC, console ticket, demo mode |
| E2E | Login local · SSO mock · passkey where Playwright allows · console against fixture · demo banner |
| Semver | Additive minor; no silent contract breaks |
| Version bump | `1.2.0` **at freeze only** |

```text
main @ v1.1.0 (+ v1.1.x patches)
  └─ v1.2.0-dev → merge → main → tag v1.2.0 → Hub
```

| Rule | Practice |
|------|----------|
| Must → Should → Discover | Do not start Discover while Must is open |
| Demo never holds prod keys | No decryptable path to production hosts |
| Webshell flag default **off** | Ship complete or do not open the flag |
| Prod critical bugs | **main** as **1.1.x** first |
| Residual Cap | Pull only if streams I/S/W/D (and preferably B-retry) are green |

---

## 1. Product decision locks (2026-08)

| Choice | Lock |
|--------|------|
| **Release shape** | **Big 1.2** — WebAuthn/passkeys + SSO/OIDC + webshell (web SSH) + public-facing **demo product** |
| **Demo edge** | Cloudflare Access **optional** (spam gate); app shared password always public via wiki |
| **Demo app login** | **Shared demo password** (single known account) — simple for invitees |
| **Demo role cap** | **Fixed `viewer`** for the shared demo user (not admin) — full read tour + canned job runs; write guard blocks sabotage |
| **Demo fidelity** | **Fully functional *clickable* UI** with rich seeded fleet; mutations are **safe** (demo mode), not a live tunnel into the home lab |
| **Hosting** | **Dedicated VPS** (compose stack), **not** the production PiHerder instance |
| **Marketing** | WordPress story/SEO; wiki holds live password; **screenshots refresh at QA complete** (not a freeze blocker mid-train) |
| **Capacity** | Prefer **correct security bar** over shipping half-gated surfaces |
| **Demo console** | **Re-opened for 1.2** as optional **sandbox-only** surface (see **D5** / **W7**) — never real fleet hosts or home lab |

**Explicit non-goals for demo**

- Do **not** expose the real homelab PiHerder (even “behind CF”). Topology, keys, backup paths, and audit history are production assets.
- Do **not** live-sync / continuous anonymize-from-prod (leak risk + coupling).
- Do **not** multi-tenant SaaS in 1.2 (still single-tenant herder; demo is one shared instance).
- Do **not** allow **usable API** access (tokens that call live APIs, Try-a-token against real scopes).
- Do **not** allow **onboarding a real device/host** (wizard / add server / key deploy / live SSH target).

---

## 2. Why this demo architecture

| Approach | Verdict |
|----------|---------|
| **A. Production instance + redaction** | **Reject.** Residual secrets (Fernet blobs, SSH keys, tokens), real hostnames/CIDRs, accidental write (backup/rsync/docker), and ops coupling. Anonymization is never 100%. |
| **B. VPS empty install** | UI works but **empty** — maps/jobs/docker look dead; bad conversion. |
| **C. VPS + seed + `DEMO_MODE` (chosen)** | Full clickable product; no path to your lab; resettable; CF Access controls who sees it. |
| **D. VPS + real sandbox VMs** | Maximum “real” jobs; higher cost/ops. **Optional phase 2** if fakes feel thin for webshell demos. |

**Chosen path: C**, with optional **D** only for webshell proof hosts if needed.

```text
WordPress (marketing)
    │  CTA: Request demo / Sign in to demo
    ▼
Cloudflare Access (gate)
    ▼
demo.example.com  →  VPS: Caddy + PiHerder (DEMO_MODE=1)
                          │
                          ├─ Postgres: seeded fleet (scrubbed *structure*, synthetic names)
                          ├─ Redis/Celery: jobs run but side effects faked / no-op
                          └─ No outbound SSH to home lab; optional loopback “toy” hosts later
```

---

## 3. Demo product design

### 3.1 Environment & edge

| Piece | Recommendation |
|-------|----------------|
| **DNS** | `demo.<yourdomain>` → VPS; CF proxy orange-cloud |
| **TLS** | Cloudflare Full (strict) + origin cert or Let’s Encrypt on Caddy |
| **Access** | Cloudflare Zero Trust **Access** app on `demo.*` — email OTP or Google/GitHub IdP; small allowlist + “request access” form on WordPress |
| **WAF** | CF WAF + bot fight; rate limit login/API |
| **Hardening** | Separate VPS, no shared network with home; no VPN path into lab; firewall: 443 only (and 22 from your admin IPs) |
| **Compose** | Same image as Hub tag you want to market (e.g. `1.2.0` / pre-release `1.2.0-rc`) |

### 3.2 `DEMO_MODE` (product flag)

New env (name bikeshed: `PIHERDER_DEMO_MODE=1`):

| Behaviour | Detail |
|-----------|--------|
| **Banner** | Persistent “Demo — shared account · data resets · some actions simulated” |
| **Accounts** | **One shared login** (`demo@…` / published password) with **fixed `viewer` role**. Password published on live wiki (may rotate). |
| **Role ceiling** | Demo user **stays viewer**; **write guard** + demo hard blocks prevent sandbox vandalism (API tokens, real onboard, settings sabotage, account lockout, etc.). Canned job runs allowed. |
| **Secrets** | No real PEM/SMTP/webhook credentials; placeholders; Fernet key unique to demo |
| **Outbound** | Block or no-op: live SSH to external hosts (except optional **sandbox console** target), nmap scan start, cert apply to edge, webhook fire, mail send |
| **Onboard / real resources** | **Hard block:** add-server wizard, SSH key deploy, “test connection” to non-seed hosts, enroll new devices that require network, bind new live integrations that call out. Seeded hosts remain **read/click** with **canned** job results only. |
| **API** | Token create/use **403**. Interactive OpenAPI **gated** (`/docs`, `/redoc`, `/openapi.json` → 404). Human docs remain in repo/wiki. |
| **Keys / credentials UI** | SSH key panels, cert vault labels, integration credential forms **visible** with **redacted / placeholder** values; copy/download of PEMs disabled; rotate/deploy no-op with clear toast. |
| **Mutations (seeded fleet)** | Prefer **canned success** for demos of jobs (backup “ok”, docker restart “ok”) with audit rows so UI feels alive; destructive deletes either blocked or restored on next reset |
| **Reset** | **Live:** host cron via `scripts/demo-maintain.sh` + `scripts/cron.d/piherder-demo.example` (6h data-reset + daily redeploy) — **D4 done** on public demo VPS |
| **Audit privacy** | Visitor **client IPs scrubbed** (`redacted`) on write + display so shared account does not leak others’ addresses |
| **Webshell** | **Off today** (`demo_mode` forces console disabled). **D5 under consideration:** sandbox-only console for product tour (see §4 Stream D) |

### 3.3 Seed data strategy (“feels like my fleet, isn’t my fleet”)

**One-shot pipeline** (run by maintainers, not continuous):

1. Optional: export schema-shaped snapshots from **staging** or a **copy** of prod DB offline.  
2. **Scrubber** (script in repo, e.g. `scripts/demo_seed/`):  
   - Hostnames → `lab-core.demo`, `lab-edge.demo`, …  
   - IPs → RFC5737 / RFC1918 demo ranges  
   - Emails → `demo@example.invalid`  
   - **Drop** all encrypted private keys, tokens, SMTP passwords, API tokens, VAPID, backup path roots pointing at real disks  
   - Rewrite audit/job payloads that embed host paths  
   - Keep **structure**: N servers, docker project names (generic), nmap device kinds, fabric edges, cert *names* (not real PEMs), integration *types* without live URLs  
3. Ship **checked-in seed** (SQL/JSON fixtures) that CI can load — reproducible, reviewable, no prod dump in git.  
4. Prefer **hand-authored rich seed** if scrubbed export is messy; use prod only as *inspiration* for cardinality.

**Click surfaces that must look full:** fleet overview, Hosts/Path maps, discovery devices, docker inventory (static snapshot rows), backups history (past success/fail), jobs + audit feed, integrations list, settings (non-secret), SSO/WebAuthn UI when shipped (enroll against demo RP).

### 3.4 WordPress role

| Do | Don’t |
|----|-------|
| Feature pages, SEO, screenshots, short video | Embed unauthenticated live UI in iframe (CF Access breaks embeds) |
| “Request demo access” → your email / Typeform → add to CF Access | Promise “instant open demo” if Access is gated |
| Link to wiki/docs Hub | Host PiHerder inside WordPress |

---

## 4. v1.2 product streams (big release)

Pull former **v1.3** items into **v1.2**. Keep residual 1.1 polish out of this plan unless still open at freeze.

### Stream I — WebAuthn / passkeys (**was v1.2**)

| Item | Stance | Status |
|------|--------|--------|
| **I1** Passkeys as **second factor** (coexist with TOTP + backup codes) | Must | **Landed** (2026-08-08) |
| **I2** Register / list / revoke passkeys on Account | Must | **Landed** |
| **I3** Login step-up: password → WebAuthn **or** TOTP | Must | **Landed** |
| **I4** Policy: force 2FA satisfied by passkey (or TOTP) | Should | **Landed** |
| **I5** Passwordless (discoverable credentials only) | Defer post-1.2 unless free | Deferred |
| RP ID / HTTPS requirements documented (breaks on HTTP LAN — document) | Must | **Landed** (wiki + Account copy) |

**Deps:** `webauthn` (py_webauthn) · `WebAuthnCredential` model · challenge JWT cookies · no private key server-side.

### Stream S — SSO / OIDC (**was v1.3 Z**)

**Detail:** [FEATURE_PLAN_SSO_OIDC.md](FEATURE_PLAN_SSO_OIDC.md)

| Item | Stance | Status |
|------|--------|--------|
| **S1** OIDC authorization-code + PKCE; BYO IdP (Authentik, Keycloak, Authelia, Google Workspace, Entra) | Must | **Code complete** — operator live IdP QA pending |
| **S2** Map IdP groups/claims → roles (`admin` / `operator` / `viewer`) | Must | **Code complete** (UI map modal) |
| **S3** JIT user provision + disable orphan policy | Must | **Code complete** (soft orphan) |
| **S4** Local password login **remain** (air-gap / break-glass); config to force SSO for non-break-glass | Must | **Code complete** |
| **S5** Settings UI: issuer, client id/secret (Fernet), scopes, role claim mapping | Must | **Code complete** |
| **S6** Audit: `sso_login` / `sso_link` / `sso_unlink` / failures | Must | **Code complete** |
| **S7** **Link both ways:** SSO login → local (email auto-link) **and** Account → SSO (explicit) | Must | **Code complete** |
| **S8** **Remove password** when ≥1 SSO link (SSO-only login) | Must | **Code complete** |
| **S9** **Unlink** SSO; if no password, **set password in same flow** before unlink completes | Must | **Code complete** |
| **S13** **2FA path-agnostic:** when 2FA is required (enrolled or Force 2FA), validate on SSO login and on link/unlink/remove-password — same gates as password | Must | **Code complete** |
| SAML | Defer | — |
| Multi-tenant org isolation | Out of scope | — |

### Stream W — Webshell / web SSH (**was HL-P5 / v1.3**)

Architecture already sketched in host lifecycle plan — **adopt and implement**:

```text
Browser (xterm.js) —WSS ticket→ PiHerder —Paramiko/asyncssh PTY→ host
         (no PEM in browser)
```

| Item | Stance | Status |
|------|--------|--------|
| **W1** Feature flag default **off** (`PIHERDER_SSH_CONSOLE`) | Must | **Landed** |
| **W2** operator+ only; viewer 403 | Must | **Landed** |
| **W3** Step-up 2FA before ticket; **fleet-wide** grant (all hosts) | Must | **Landed** (passkey preferred; backup codes off; grant re-prompt on expiry) |
| **W4** Single-use open ticket; idle/max; concurrent caps | Must | **Landed** |
| **W4a** Soft resume after WS drop (app switch) | Must | **Landed** (park PTY + resume token; `HOLD_SEC`) |
| **W4b** Multi-shell UI + multi-host `/console` + popup | Should | **Landed** (popup; host tabs keep WS; compact chrome; sticky Ctrl) |
| **W5** Audit open/close + IP + duration | Must | **Landed** |
| **W6** SECURITY.md + ADMIN + **CSP** / TLS bar | Must | **Landed** (same-origin iframe CSP; wiki env catalog) |
| **W7** Demo console policy | Must | **Landed (D5)** — simulated shell only (no live SSH); viewer allowed in demo |
| **W8** Mobile soft-Tab completion polish | Cap | **Parked** — **KI-console-mobile-soft-tab** (desktop Tab OK; mobile IME can re-append fragment after completion). Workaround in [wiki web-ssh-console](../wiki/day-to-day/web-ssh-console.md#known-issues). Not freeze-blocking. |
| Session recording / dual-control root | Defer | — |

### Stream D — Demo platform (**new product work**)

| Item | Stance | Status |
|------|--------|--------|
| **D1** `PIHERDER_DEMO_MODE` behaviours (§3.2, as evolved) | Must | **Landed** (banner, write guard, canned jobs, outbound blocks, IP scrub, OpenAPI gate) |
| **D1a** Shared demo user + fixed **viewer**; block real onboard | Must | **Landed** (role + hard blocks; not admin) |
| **D1b** API: token create/use disabled; OpenAPI gated | Must | **Landed** |
| **D1c** Keys/secrets: no real PEMs / live deploy | Must | **Landed** (seed placeholders + demo blocks) |
| **D2** Seed pack + reset script + compose overlay `docker-compose.demo.yml` | Must | **Landed** |
| **D3** Runbook: VPS + password wiki + optional CF Access | Must | **Landed** ([DEMO_SITE.md](DEMO_SITE.md) · [wiki demo-site](../wiki/operations/demo-site.md)); WordPress CTA optional polish |
| **D4** Scheduled reset (host cron) | Should | **Live** on public demo VPS (`demo-maintain` cron) — 2026-08 |
| **D5** **Demo console** (simulated webshell) | Should | **Landed** — in-process shell only (`demo_console.py`); no network; skip 2FA; viewer OK |
| Screenshots / marketing stills | Freeze polish | **Deferred until QA completes** (not mid-train) |

#### D5 — Demo console (shipped: simulated)

**Choice:** Option **B** — canned in-process PTY (`app/services/demo_console.py`). **No Paramiko, no TCP, no live host.**

| Rule | Implementation |
|------|----------------|
| Enable | `console_enabled()` is **True** when `DEMO_MODE` (independent of `PIHERDER_SSH_CONSOLE`) |
| Channel | `open_session_channel()` → `open_demo_shell()` under demo; production still Paramiko |
| RBAC | `get_console_user` / WS cookie allow **viewer** only when `is_demo_console()` |
| 2FA | Skipped in demo (shared account must not enroll) |
| Creds | No encrypted key required on seed hosts |
| Abuse | Same concurrent / idle / max session caps; write guard allows console POSTs |
| Banner | UI + terminal: “simulated · no live SSH” |

Sidecar SSH toy (option A) remains a possible later upgrade if a “real path” demo is needed; not required for 1.2.

### Stream Q — Quality / freeze (same bar as 1.1, raised for new attack surface)

| Gate | Target |
|------|--------|
| Unit | ≥ 55% (raise if easy); **focused** tests for WebAuthn, OIDC, console ticket, demo mode |
| E2E | Login local · SSO mock · passkey (where Playwright allows) · console open against fixture host · demo banner |
| Security review | Explicit checklist for W + S + I before tag |
| Docs | Wiki + ADMIN + SECURITY; mkdocs strict |

### Stream B — Backup reliability (from v1.1 known issues)

| Item | Stance | Status |
|------|--------|--------|
| **B-retry** | Retry (and optional soft-success policy) when rsync reports **vanished files** / partial transfer on busy sources (e.g. Frigate NVR recordings moved/deleted mid-run). Carried from **v1.1 KI-rsync-vanished** — [RELEASE_v1.1.0.md](RELEASE_v1.1.0.md). | Should | **Landed** — retry + soft-OK (env knobs) |
| **B-policy** | Soft-OK on code **24** / vanished **23**; path excludes remain operator choice | Should | **Landed** with B-retry defaults |
| **B-DR** | **Herder self-backup Full = real DB DR.** Pre-**v1.2.0** “full” was JSON snapshots with **no jobs**, **capped audit**, no `pg_dump` — not sole DR (**KI-self-backup-not-full-db**). **v1.2.0** Full mode: **`pg_dump -Fc` entire Postgres** + `DATA_ROOT` files; restore via `pg_restore`. Config-only stays light JSON. Document limitations for **&lt; 1.2** in release notes + wiki. | Must | **Landed** on `v1.2.0-dev` (format **v6**) |

### Capacity residual (not release-defining)

Map interactivity residuals, insights thin, templates git, HA polish, mail matrix — **pull only if streams I/S/W/D (and preferably B-retry) are green**. Prefer not to starve security streams.

---

## 5. Recommended delivery order (parallelizable)

```text
Phase 0  Finish v1.1 QA → tag/merge  ✅ done (v1.1.0)
    │
Phase 1  Foundations (parallel)  ← current
    ├─ D1/D2 demo mode + seed (unblocks marketing runbook early)
    ├─ I1–I3 WebAuthn 2FA
    └─ S1 design + OIDC library spike
    │
Phase 2  Core ship
    ├─ S2–S6 SSO GA
    ├─ W1–W6 webshell GA (flag off by default)
    └─ D3 CF Access demo deploy (internal team first)
    │
Phase 3  Integrate + tighten
    ├─ W3 step-up accepts passkeys
    ├─ Demo enables SSO login (demo IdP or CF Access already gate)
    ├─ Optional D5 sandbox host for terminal demo
    └─ WordPress CTA + screenshots of 1.2 surfaces
    │
Phase 4  Freeze
    └─ Security review · E2E · docs · Hub multi-arch · tag v1.2.0
```

**Why this order:** Demo seed is independent value; WebAuthn hardens step-up for webshell; SSO is large but isolated; webshell is highest risk — last among features to open the flag.

---

## 6. Threat model summary (release gates)

| Surface | Primary risks | Controls |
|---------|---------------|----------|
| **Demo** | Abuse of shared instance; data pollution; scrape; API abuse; fake “I added my Pi” | CF Access **and** shared password; fixed admin + hard blocks (API use, real onboard, PEM export); demo mode; reset; no real secrets; rate limits |
| **WebAuthn** | Weak RP ID; phishing | HTTPS; standard ceremonies; backup codes remain |
| **SSO** | Account takeover via mis-mapped admin group | Explicit claim map; default least privilege; break-glass local admin |
| **Webshell** | XSS→shell; herder as jump box; session theft | Flag off; step-up; tickets; CSP; no PEM to browser; idle kill; audit |

**Demo must never** hold a decryptable key to a production host.

---

## 7. Success criteria

**Product 1.2**

1. Operator can enroll a passkey and use it for 2FA step-up (including before console).  
2. Operator can log in via OIDC (2FA when required); link both ways; groups map to roles; local break-glass still works.  
3. Operator can open an in-browser SSH session to a managed host without downloading PEM; viewer cannot; flag can disable globally.  
4. SECURITY/ADMIN document all three.

**Demo**

5. Visitor logs in with **shared demo password** (fixed **viewer**) → full fleet UI (maps, docker, jobs, audit). Optional CF Access outer gate.  
6. **Cannot** onboard a real host/device; **cannot** obtain a working API token; OpenAPI gated; keys view-only / redacted.  
7. Live outbound actions cannot touch the home lab.  
8. Scheduled (and manual) reset restores known-good seed — **live on demo VPS**.  
9. Wiki publishes credentials; marketing/WordPress optional; **screenshots updated when QA completes**.  
10. *(If D5 ships)* Sandbox-only console openable from demo; non-sandbox hosts still denied.

---

## 8. Kickoff leans (locked 2026-08-08)

| # | Question | Decision |
|---|----------|----------|
| 1 | Demo webshell on or off? | **Today off.** **Revised 2026-08-10:** pursue **sandbox-only** demo console (**D5**, lean option A) if security bar holds; else stay off |
| 2 | Demo app auth? | **Shared password + fixed viewer** (locked); CF Access optional outer gate |
| 3 | Seed hand-authored vs scrubbed export? | **Hand-authored fixtures** first; scrubber as later tool |
| 4 | Passwordless passkeys in 1.2? | **No** — 2FA only |
| 5 | Force SSO when configured? | Optional setting; break-glass local admin always |
| 6 | Tag shape | `v1.2.0` single big tag after freeze (RCs: `1.2.0-rc.N`) |
| 7 | Screenshots | **After QA completes** (not mid-train) |
| 8 | D4 demo cron | **Live** on public demo VPS (2026-08) |

---

## 9. Doc / artifact plan (when execution starts)

| Artifact | Purpose |
|----------|---------|
| `docs/PLAN_v1.2.0.md` | Train plan (promote this content) |
| `docs/FEATURE_PLAN_SSO_OIDC.md` | S stream detail (**written** 2026-08-08 — link/unlink/password lifecycle) |
| `docs/FEATURE_PLAN_WEBAUTHN.md` | I stream detail (or IAM plan addendum) |
| Extend `FEATURE_PLAN_HOST_LIFECYCLE.md` P5 | W stream implementation notes |
| `docs/DEMO_SITE.md` (+ slim wiki blurb) | D stream + CF/VPS runbook (maintainer) |
| `scripts/demo_seed/` + `docker-compose.demo.yml` | Reproducible demo |
| WordPress / marketing stills | CTA + screenshots **after QA completes** |

---

## 10. Out of scope (stay honest)

- Multi-tenant SaaS / per-customer isolation  
- **Fine-grained / per-host / per-feature roles (AC-fg)** — global viewer·operator·admin only in 1.2; **→ v1.3** ([PLAN_v1.3.0.md](PLAN_v1.3.0.md) · [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md))  
- **Configurable password / 2FA-step-up / console timeout policy in Settings** — fixed code policy + env console knobs in 1.2; **→ v1.3** streams **P / T / W-cfg**  
- **Multi-identity host SSH + command-level shell audit** — one key/user per host and session meta audit only in 1.2; **→ v1.3** streams **W-id / W-audit**  
- **Map alert severity depth + app-wide pagination/search** — **→ v1.3** streams **A / L**  
- **Insights / custom dashboards** — **→ v1.3** stream **N** (discover + thin slice only)  
- SAML  
- ACME-in-herder (still ≥1.3 consideration)  
- Live anonymized mirror of production  
- Public ungated demo with shared password  
- Video session recording / dual-control console (command transcripts are 1.3 **discover**, not 1.2)  

**Planning capture (while 1.2 finishes):** [PLAN_v1.3.0.md](PLAN_v1.3.0.md) — do not implement on `v1.2.0-dev` unless pulled as an explicit exception.

---

## 11. Immediate next steps

| # | Step | Status |
|---|------|--------|
| 1 | Finish **v1.1** QA / freeze | **Done** — `v1.1.0` tagged · Hub multi-arch published |
| 2 | Open **`v1.2.0-dev`** + promote this plan | **Done** 2026-08-08 |
| 3 | Streams **I / S / W / B / D1–D4** product land | **Done** (S live multi-IdP = operator QA) |
| 4 | Demo VPS + maintain cron | **Done** — **D4 live** |
| 5 | **D5** simulated demo console | **Done** — no live SSH |
| 6 | Operator QA → screenshot refresh → freeze docs / RELEASE / tag / Hub | After QA |

**Feature-complete bar for freeze:** I + S + W + B + D1–D5 green.
