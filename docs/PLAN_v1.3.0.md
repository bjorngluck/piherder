# PiHerder v1.3.0 — operator policy, scale UX, alerts depth

**Status:** **Planning / backlog** — capture while **v1.2.0** finishes on `v1.2.0-dev`  
**Date opened:** 2026-08-10  
**Git branch (when train opens):** `v1.3.0-dev` (not opened yet)  
**Package / image version (at tag):** `1.3.0`  
**Theme:** Operator-configurable security policy · console knobs in-app · map/alert granularity · fleet-scale list UX (pagination + search)  
**Baseline:** `v1.2.0` (when tagged)  
**Mode:** Planning only — do **not** start implementation streams until 1.2 freezes  
**Related:** [PLAN_v1.2.0.md](PLAN_v1.2.0.md) · [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · [FEATURE_PLAN_IAM_2FA_UPDATES_NOTIFICATIONS.md](FEATURE_PLAN_IAM_2FA_UPDATES_NOTIFICATIONS.md) · [FEATURE_PLAN_SSO_OIDC.md](FEATURE_PLAN_SSO_OIDC.md) · [ADMIN.md](ADMIN.md) · [wiki/operations/alerts-email-webhooks.md](../wiki/operations/alerts-email-webhooks.md) · [SECURITY.md](../SECURITY.md)

> **Not the active train.** v1.2 is identity + webshell + gated demo. This document parks **operator policy and scale** work so 1.2 bug-capture stays focused. Promote streams when the 1.3 train opens.

---

## 0. Intent

After 1.2, operators who harden fleets and grow host/container counts need:

1. **Security policy they own** (password rules, 2FA/step-up) without rebuilding images  
2. **Console policy they own** (timeouts, re-auth, concurrency) without only env vars  
3. **Alerts they can tune** (severity + what fires on maps / channels)  
4. **Lists that scale** (page size, filters, free-text / semantic search) when many servers and Docker services exist  

**Carry-over from earlier plans (still in 1.3 path):** fine-grained roles (**AC-fg**), ACME-in-herder (under consideration), residual HA REST/path2, branding, k8s/bare — see §6 and [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md).

---

## 1. Decision lock (planning defaults — revisit at train open)

| Choice | Value |
|--------|--------|
| Integration branch | **`v1.3.0-dev`** when 1.2 is on `main` |
| Production line until then | **`main` @ 1.2.x** patches; this plan does not block 1.2 |
| Theme streams (seed) | **P** password policy · **T** 2FA/step-up policy · **W-cfg** console config · **A** alerts/map severity · **L** list pagination + search · (+ **AC-fg** / residual as capacity) |
| Policy storage | Prefer **app Settings** (DB) with env as override / bootstrap where it already exists |
| Semver | Additive minor; document migrations for defaults that change behaviour |
| Out of focus for seed | Multi-tenant SaaS · SAML · full Elasticsearch · session recording |

```text
main @ v1.2.0 (+ v1.2.x)
  └─ v1.3.0-dev → merge → main → tag v1.3.0 → Hub
```

---

## 2. Streams (seed backlog)

### Stream **P** — User-configured password policy

**Today:** Fixed code defaults in `app/services/password_policy.py` — min length 10, upper + lower + digit, specials optional, soft max ~72 bytes (bcrypt). Text is shown on register / change / admin create.  
**Wanted:** Admin (Settings) can set policy for the instance without code changes.

| ID | Item | Notes |
|----|------|--------|
| P1 | Settings UI: min length, require upper/lower/digit/special, optional max length (≤72) | Persist in app settings; `policy_rules_text()` / `validate_password()` read config |
| P2 | Safe defaults + migration | Existing fixed policy becomes the default seed; never weaken silently below a documented floor unless admin opts in |
| P3 | Surface copy everywhere | Register, account password, admin create/reset, recover-admin CLI uses same rules text |
| P4 | Audit on policy change | Who changed rules + summary of new constraints |
| P5 | API / docs | ADMIN + wiki password section; optional read-only `GET` for automation later |

**Non-goals (P):** Per-user policies; breached-password dictionaries (nice later); SSO IdP password rules (out of herder scope).

---

### Stream **T** — User-driven 2FA enforcement and step-up policy

**Today:** Instance **Force 2FA** (enroll wall); login step-up when TOTP/passkey enrolled; secrets step-up cookie; webshell step-up with env knobs (`REQUIRE_2FA_EVERY_SHELL`, passkey prefer/require, backup codes). SSO shares the same 2FA helpers as password login.  
**Wanted:** Operators configure **who must enroll**, **when step-up fires**, and **how long step-up grants last** — not only a binary force-2FA flag + env for console.

| ID | Item | Notes |
|----|------|--------|
| T1 | Policy matrix in Settings → Security | Force 2FA (all users / admins only / operators+ / off); grace period after enable; optional trusted-device skip rules |
| T2 | Step-up surfaces catalog | Document + configure: secrets view, sensitive account actions, console open, (optional) destructive jobs — each with re-auth window minutes |
| T3 | Factor policy | Allow TOTP / passkey / backup codes for **login** vs **step-up** (e.g. console: passkey preferred / required; backup codes never for shell) |
| T4 | Alignment with SSO | Keep “IdP MFA does not replace herder 2FA” unless an explicit admin option; no silent skip |
| T5 | Audit + break-glass | Policy changes audited; sole-admin / recover path documented when force-2FA + lost factors |

**Non-goals (T):** Passwordless-only login day one (unless residual after 1.2); per-host 2FA (belongs with **AC-fg**).

**Depends on:** 1.2 WebAuthn + SSO step-up behaviour stable (reuse helpers).

---

### Stream **W-cfg** — Configurable console timeouts, 2FA step-up, session limits

**Today:** Webshell limits are largely **env-only** (`PIHERDER_SSH_CONSOLE_*`: idle, max session, ticket TTL, max per user / global, grant minutes, revalidate, every-shell 2FA, bind IP/device, scrollback). Flag default off.  
**Wanted:** Safe **in-app Settings** (admin) for the knobs operators actually tune, with env as hard ceiling or bootstrap.

| ID | Item | Notes |
|----|------|--------|
| W1 | Settings → Console (or Security) panel | Idle timeout, max session length, max concurrent shells per user + global, ticket lifetime, step-up grant window |
| W2 | 2FA step-up knobs in UI | Require 2FA every new shell; allow backup codes; prefer/require passkey — same semantics as env, stored in settings |
| W3 | Precedence rules | Document: env kill switch still master; settings fill defaults; optional “env wins if set” for air-gapped deploys |
| W4 | Live limits without restart | Prefer settings reload without full process restart where safe |
| W5 | Wiki + DEMO | Demo keeps console off; document that public demo does not expose these knobs as a multi-tenant shell farm |

**Non-goals (W-cfg):** Session recording; dual-control console; raising global caps beyond a hard server ceiling (DoS).

**Depends on:** 1.2 Stream **W** shipped and operationally trusted.

---

### Stream **A** — Map alert severity and granular alert options

**Today:** Notifications have severity (`info` / `warning` / `critical`); webhook/SMTP min severity; some stack-health / cert verify alerts; map and inventory surfaces raise alerts with limited operator control over *which* map events and *how loud*.  
**Wanted:** Clearer **severity mapping** and **granular enable/filters** so map noise (flapping hosts, optional ports, discovery churn) does not equal cert-fail critical.

| ID | Item | Notes |
|----|------|--------|
| A1 | Alert taxonomy review | Inventory map / stack / discovery / cert / job event types → default severity table (documented) |
| A2 | Per-category severity overrides | Settings: e.g. “inventory down” = warning, “cert verify fail” = critical; optional mute categories |
| A3 | Map-specific options | Which map edges/devices raise alerts; optional debounce / re-alert interval; link back to map focus |
| A4 | Channel filters depth | Beyond min severity: event allowlist/denylist per webhook and mail (extend Wh-lite) |
| A5 | UI | Alerts page filters by severity + category; bulk resolve by category |

**Non-goals (A):** Full SIEM; PagerDuty product; multi-tenant routing trees.

---

### Stream **L** — Pagination, page size, free-text / semantic search filters

**Today:** Jobs and Audit already use **per-page** + filters; many dense surfaces (Servers list, Docker services/stacks, discovery devices, templates, notifications, maps device lists) load large tables or cards with limited paging / inconsistent search.  
**Wanted:** **App-wide list pattern** so fleets with many hosts and containers stay usable.

| ID | Item | Notes |
|----|------|--------|
| L1 | Shared list chrome | `per_page` choices (e.g. 10/20/50/100), page controls, total count, remember preference (user or cookie) |
| L2 | Priority surfaces | Servers list · server Docker (projects/services) · discovery devices · integrations lists · notifications · templates catalog · (extend jobs/audit consistency) |
| L3 | Free-text filter | Case-insensitive match across name, hostname, IP, labels, project, image — same “search box” pattern as Audit |
| L4 | Structured filters | Status, role/kind, host, unhealthy only, favourites first — composable query params |
| L5 | “Semantic” search (pragmatic) | **Not** embedding ML day one: tokenised multi-field search + optional synonym/aliases (e.g. `ha` → homeassistant); document as **smart free-text**, not vector search |
| L6 | Performance | Server-side limit/offset or keyset; avoid loading entire Docker inventory into the browser when possible |
| L7 | API alignment | Optional `limit`/`offset`/`q` on list-ish `/api/v1` endpoints if still missing |

**Non-goals (L):** Full-text Postgres extensions required day one; client-only virtual scroll as the only strategy; Elasticsearch dependency.

---

## 3. Ship bar (draft — finalise at train open)

| Priority | Streams | Bar |
|----------|---------|-----|
| **Must** | **L** (at least Servers + Docker + discovery) · **P** or **T** (at least one policy stream fully usable) | Operator can run a large fleet without drowning and can set at least one security policy in UI |
| **Should** | **P** + **T** · **W-cfg** · **A** | Password + 2FA policy + console knobs + alert severity depth |
| **Discover / Cap** | **AC-fg** · ACME · branding · insights residual | Promote only if Must green |

Success criteria (draft):

1. Admin can configure password policy; all password entry paths enforce it and show the same rules.  
2. Admin can configure force-2FA / step-up windows for the catalogued sensitive actions (incl. console).  
3. Console idle/max/concurrency/step-up knobs adjustable in Settings without editing compose for common cases.  
4. Map/stack/cert-style alerts have documented severities and per-category tuning; channels respect filters.  
5. Servers + Docker service lists support page size + free-text filter without loading unbounded HTML.

---

## 4. Quality bar (draft)

| Gate | Target |
|------|--------|
| Unit | Hold ≥ 55% (raise only if easy) |
| Tests | Policy validate matrix · settings round-trip · list query unit tests · console limit apply |
| E2E | Settings policy save · one large list page-size · console settings smoke if flag on |
| Docs | ADMIN + wiki Security / Alerts / Console / list UX; `mkdocs build --strict` at freeze |
| Security | Policy changes audited; no weakening of demo/prod gates by accident |

---

## 5. Dependencies on v1.2

| 1.2 deliverable | Why 1.3 needs it |
|-----------------|------------------|
| WebAuthn + step-up helpers | **T** / **W-cfg** factor policy builds on the same paths |
| SSO 2FA parity | **T** must not re-fork SSO vs password |
| Webshell tickets + env knobs | **W-cfg** moves knobs into settings |
| Demo mode IP scrub / OpenAPI gate | Keep demo safe when settings surfaces expand |
| Force 2FA + trusted devices | Baseline for **T** grace / skip rules |

1.2 bugs and polish found during capture go on **1.2** (or 1.2.x), not this document, unless explicitly deferred here.

---

## 6. Carry-over / residual (already on 1.3 path)

| Theme | Source | Notes |
|-------|--------|--------|
| **AC-fg** fine-grained roles | ROADMAP · PLAN_v1.1 §6 · PLAN_v1.2 §10 | Per-host / per-feature gates; design at train open |
| **P-acme** ACME-in-herder | PLAN_v1.1 §6.1 | Under consideration — not a Must for this seed |
| HA REST / path 2 | FEATURE_PLAN_HOME_ASSISTANT | Residual integration |
| Full insights · branding · k8s/bare | ROADMAP H3 / quality | Far horizon unless capacity |

---

## 7. Out of scope (stay honest)

- Multi-tenant SaaS / org isolation  
- Replacing NPM with full ACME product as a 1.3 Must  
- Vector/embedding “AI search” as a hard dependency  
- Session recording / dual-control console  
- Weakening public **demo** into a multi-user admin sandbox  

---

## 8. Capture log

| Date | Note |
|------|------|
| 2026-08-10 | Opened while finishing **v1.2** demo/ops. Seed streams from operator asks: **P** password policy · **T** 2FA/step-up policy · **W-cfg** console timeouts/limits/step-up · **A** map alert severity + granular alerts · **L** pagination + free-text/smart search across dense lists. |

Add deferred 1.2 items here as one-line bullets when freeze decides “→ 1.3”.

---

## 9. Immediate next steps (when ready)

| # | Step |
|---|------|
| 1 | Finish **v1.2.0** freeze / tag / Hub |
| 2 | Open **`v1.3.0-dev`** + lock Must/Should from this seed |
| 3 | Spike **L1** shared list component + **P1** settings schema (cheap wins first) |
| 4 | Write short feature notes (or extend IAM plan) for **T** / **W-cfg** before coding |

---

*End of planning capture — not a commitment to ship every stream in one minor.*
