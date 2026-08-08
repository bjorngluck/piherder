# v1.2 — Big identity + webshell + gated demo

**Status:** Planning draft (post–v1.1 QA) · **Approved** 2026-08  
**Mode:** Capacity-rich train — pull former **v1.3** items into **v1.2**  
**Related:** [PLAN_v1.1.0.md](PLAN_v1.1.0.md) §6 · [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · [FEATURE_PLAN_HOST_LIFECYCLE.md](FEATURE_PLAN_HOST_LIFECYCLE.md) P5 · [FEATURE_PLAN_IAM_2FA_UPDATES_NOTIFICATIONS.md](FEATURE_PLAN_IAM_2FA_UPDATES_NOTIFICATIONS.md)

---

## 1. Decision locks (2026-08)

| Choice | Lock |
|--------|------|
| **Release shape** | **Big 1.2** — WebAuthn/passkeys + SSO/OIDC + webshell (web SSH) + public-facing **demo product** |
| **Demo edge** | **Cloudflare Access gated** (invite / email OTP / IdP) |
| **Demo app login** | **Shared demo password** (single known account) after Access — simple for invitees |
| **Demo role cap** | **Fixed admin role only** for the shared demo user — no multi-role sandbox, no promote/demote other real admins |
| **Demo fidelity** | **Fully functional *clickable* UI** with rich seeded fleet; mutations are **safe** (demo mode), not a live tunnel into the home lab |
| **Hosting** | **Dedicated VPS** (compose stack), **not** the production PiHerder instance |
| **Marketing** | WordPress site remains the story/SEO surface; **Request demo access** CTA → Cloudflare Access → `demo.<domain>` |
| **Capacity** | Team has time — prefer **correct security bar** over shipping half-gated surfaces |

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
| **Accounts** | **One shared login** (e.g. `demo` / published password) with **fixed `admin` role**. No operator/viewer demo users required. Password is only useful *after* Cloudflare Access. |
| **Role ceiling** | Demo user **stays admin** for full UI tour, but **privilege-sensitive admin actions are blocked or no-op** where they would escape the sandbox (create live API tokens that work, open registration, change master secrets, etc.). |
| **Secrets** | No real PEM/SMTP/webhook credentials; placeholders; Fernet key unique to demo |
| **Outbound** | Block or no-op: live SSH to external hosts, nmap scan start, cert apply to edge, webhook fire, mail send |
| **Onboard / real resources** | **Hard block:** add-server wizard, SSH key deploy, “test connection” to non-seed hosts, enroll new devices that require network, bind new live integrations that call out. Seeded hosts remain **read/click** with **canned** job results only. |
| **API** | **View yes, use no:** OpenAPI / ReDoc / Settings → API pages **visible** (docs, screenshots of key shape). **Create / use tokens disabled** (or tokens minted are inert / always 403). No `feature:*` job execution via API. |
| **Keys / credentials UI** | SSH key panels, cert vault labels, integration credential forms **visible** with **redacted / placeholder** values; copy/download of PEMs disabled; rotate/deploy no-op with clear toast. |
| **Mutations (seeded fleet)** | Prefer **canned success** for demos of jobs (backup “ok”, docker restart “ok”) with audit rows so UI feels alive; destructive deletes either blocked or restored on next reset |
| **Reset** | Cron/script: wipe DB volume → re-seed nightly (or on-demand); document RPO for demo chatter |
| **Webshell** | **Off in demo by default**; optional later only on a **local sandbox** host, never implying real-device onboard |

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

| Item | Stance |
|------|--------|
| **I1** Passkeys as **second factor** (coexist with TOTP + backup codes) | Must |
| **I2** Register / list / revoke passkeys on Account | Must |
| **I3** Login step-up: password → WebAuthn **or** TOTP | Must |
| **I4** Policy: require 2FA for admin (passkey satisfies) | Should |
| **I5** Passwordless (discoverable credentials only) | Defer post-1.2 unless free |
| RP ID / HTTPS requirements documented (breaks on HTTP LAN — document) | Must |

**Deps:** `webauthn` / py_webauthn or similar; store credential id + public key + sign count; no private key server-side.

### Stream S — SSO / OIDC (**was v1.3 Z**)

| Item | Stance |
|------|--------|
| **S1** OIDC authorization-code + PKCE; BYO IdP (Authentik, Keycloak, Authelia, Google Workspace, Entra) | Must |
| **S2** Map IdP groups/claims → roles (`admin` / `operator` / `viewer`) | Must |
| **S3** JIT user provision + disable orphan policy | Must |
| **S4** Local password login **remain** (air-gap / break-glass); config to force SSO for non-break-glass | Must |
| **S5** Settings UI: issuer, client id/secret (Fernet), scopes, role claim mapping | Must |
| **S6** Audit: `sso_login` / `sso_link` / failures | Must |
| SAML | Defer |
| Multi-tenant org isolation | Out of scope |

### Stream W — Webshell / web SSH (**was HL-P5 / v1.3**)

Architecture already sketched in host lifecycle plan — **adopt and implement**:

```text
Browser (xterm.js) —WSS ticket→ PiHerder —Paramiko/asyncssh PTY→ host
         (no PEM in browser)
```

| Item | Stance |
|------|--------|
| **W1** Feature flag default **off** (`PIHERDER_SSH_CONSOLE`) | Must |
| **W2** operator+ only; viewer 403 | Must |
| **W3** Step-up 2FA (TOTP **or** passkey) before ticket mint | Must |
| **W4** Short-lived single-use ticket; idle timeout; max concurrent | Must |
| **W5** Audit open/close + IP + duration | Must |
| **W6** SECURITY.md + ADMIN + CSP/TLS bar | Must |
| **W7** Demo: disabled **or** sandbox-only host | Must decide before demo GA |
| Session recording / dual-control root | Defer |

### Stream D — Demo platform (**new product work**)

| Item | Stance |
|------|--------|
| **D1** `PIHERDER_DEMO_MODE` behaviours (§3.2) | Must |
| **D1a** Shared demo user + fixed admin; block real onboard | Must |
| **D1b** API: docs/UI visible; token create/use disabled | Must |
| **D1c** Keys/secrets UI redacted; no PEM download / live deploy | Must |
| **D2** Seed pack + reset script + compose overlay `docker-compose.demo.yml` | Must |
| **D3** Runbook: VPS + Cloudflare Access + shared password + WordPress CTA | Must (wiki/ADMIN) |
| **D4** Nightly reset job (host cron or herder job) | Should |
| **D5** Optional sandbox SSH target for webshell demos | Should if W ships in same tag |

### Stream Q — Quality / freeze (same bar as 1.1, raised for new attack surface)

| Gate | Target |
|------|--------|
| Unit | ≥ 55% (raise if easy); **focused** tests for WebAuthn, OIDC, console ticket, demo mode |
| E2E | Login local · SSO mock · passkey (where Playwright allows) · console open against fixture host · demo banner |
| Security review | Explicit checklist for W + S + I before tag |
| Docs | Wiki + ADMIN + SECURITY; mkdocs strict |

### Capacity residual (not release-defining)

Map interactivity residuals, insights thin, templates git, HA polish, mail matrix — **pull only if streams I/S/W/D are green**. Prefer not to starve security streams.

---

## 5. Recommended delivery order (parallelizable)

```text
Phase 0  Finish v1.1 QA → tag/merge as planned
    │
Phase 1  Foundations (parallel)
    ├─ D1/D2 demo mode + seed (unblocks marketing runbook early)
    ├─ I1–I3 WebAuthn 2FA
    └─ S1 design + OIDC library spike
    │
Phase 2  Core ship
    ├─ S2–S6 SSO GA
    ├─ W1–W6 webshell GA (flag off by default)
    └─ D3 CF Access demo deploy (internal team first)
    │
Phase 3  Integrate + harden
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
2. Operator can log in via OIDC; groups map to roles; local break-glass still works.  
3. Operator can open an in-browser SSH session to a managed host without downloading PEM; viewer cannot; flag can disable globally.  
4. SECURITY/ADMIN document all three.

**Demo**

5. Invitee passes Cloudflare Access → logs in with **shared demo password** (fixed admin) → full fleet UI (maps, docker, jobs, audit).  
6. **Cannot** onboard a real host/device; **cannot** obtain a working API token; keys/docs are view-only / redacted.  
7. Live outbound actions cannot touch the home lab.  
8. Nightly (or manual) reset restores known-good seed.  
9. WordPress drives access requests; app origin never public without Access.

---

## 8. Open points (resolve at kickoff, defaults lean)

| # | Question | Default lean |
|---|----------|--------------|
| 1 | Demo webshell on or off? | **Off** until sandbox host exists; UI shows “disabled in demo” |
| 2 | Demo app auth after CF Access? | **Shared password + fixed admin** (locked); SSO/WebAuthn can still be *shown* as UI if seed supports, but not required for entry |
| 3 | Seed hand-authored vs scrubbed export? | **Hand-authored fixtures** first; scrubber as later tool |
| 4 | Passwordless passkeys in 1.2? | **No** — 2FA only |
| 5 | Force SSO when configured? | Optional setting; break-glass local admin always |
| 6 | Tag shape | `v1.2.0` single big tag after freeze (RCs: `1.2.0-rc.N`) |

---

## 9. Doc / artifact plan (when execution starts)

| Artifact | Purpose |
|----------|---------|
| `docs/PLAN_v1.2.0.md` | Train plan (promote this content) |
| `docs/FEATURE_PLAN_SSO_OIDC.md` | S stream detail |
| `docs/FEATURE_PLAN_WEBAUTHN.md` | I stream detail (or IAM plan addendum) |
| Extend `FEATURE_PLAN_HOST_LIFECYCLE.md` P5 | W stream implementation notes |
| `docs/FEATURE_PLAN_DEMO.md` or wiki `operations/demo-site.md` | D stream + CF runbook |
| `scripts/demo_seed/` + `docker-compose.demo.yml` | Reproducible demo |
| WordPress | CTA + feature pages post-RC screenshots |

---

## 10. Out of scope (stay honest)

- Multi-tenant SaaS / per-customer isolation  
- SAML  
- ACME-in-herder (still ≥1.3 consideration)  
- Live anonymized mirror of production  
- Public ungated demo with shared password  
- Session recording / dual-control console  

---

## 11. Immediate next steps (after plan accept)

1. Finish **v1.1** QA / freeze (do not parallelize risky merges into 1.1).  
2. Open **`v1.2.0-dev`** branch + `PLAN_v1.2.0.md` from this document.  
3. Spike week: OIDC library choice · WebAuthn library · xterm + WS ticket skeleton · demo seed cardinality sketch.  
4. Provision VPS + CF Access early (even with stock 1.1 + seed) so marketing/Access flow is proven before feature complete.
