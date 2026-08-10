# PiHerder v1.3.0 — operator policy, scale UX, multi-identity console, alerts, insights

**Status:** **Planning / backlog** — capture while **v1.2.0** finishes on `v1.2.0-dev`  
**Date opened:** 2026-08-10  
**Git branch (when train opens):** `v1.3.0-dev` (not opened yet)  
**Package / image version (at tag):** `1.3.0`  
**Theme:** Operator-configurable security policy · multi-identity console · optional command audit · console knobs · map/alert granularity · fleet-scale list UX · thin-slice reporting / custom dashboards  
**Baseline:** `v1.2.0` (when tagged)  
**Mode:** Planning only — do **not** start implementation streams until 1.2 freezes  
**Related:** [PLAN_v1.2.0.md](PLAN_v1.2.0.md) · [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · [FEATURE_PLAN_HOST_LIFECYCLE.md](FEATURE_PLAN_HOST_LIFECYCLE.md) P5 · [FEATURE_PLAN_IAM_2FA_UPDATES_NOTIFICATIONS.md](FEATURE_PLAN_IAM_2FA_UPDATES_NOTIFICATIONS.md) · [FEATURE_PLAN_SSO_OIDC.md](FEATURE_PLAN_SSO_OIDC.md) · [ADMIN.md](ADMIN.md) · [wiki/operations/alerts-email-webhooks.md](../wiki/operations/alerts-email-webhooks.md) · [SECURITY.md](../SECURITY.md)

> **Not the active train.** v1.2 is identity + webshell + gated demo. This document parks **operator policy, multi-identity shell, scale, and insights** work so 1.2 bug-capture stays focused. Promote streams when the 1.3 train opens.

---

## 0. Intent

After 1.2, operators who harden fleets and grow host/container counts need:

1. **Security policy they own** (password rules, 2FA/step-up) without rebuilding images  
2. **Console policy they own** (timeouts, re-auth, concurrency) without only env vars  
3. **Least-privilege by default on the host** — manage with a constrained herder SSH user, open a **privileged** shell only when chosen (separate key/user)  
4. **Deeper optional shell audit** — who ran what in the webshell (commands ± responses), with redaction for secrets  
5. **Alerts they can tune** (severity + what fires on maps / channels)  
6. **Lists that scale** (page size, filters, free-text / semantic search) when many servers and Docker services exist  
7. **At-a-glance reporting** — discovery + a **thin slice** of reporting / custom dashboarding (not a full BI product)

**Carry-over from earlier plans (still in 1.3 path):** fine-grained roles (**AC-fg**), ACME-in-herder (under consideration), residual HA REST/path2, branding, k8s/bare — see §6 and [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md).

---

## 1. Decision lock (planning defaults — revisit at train open)

| Choice | Value |
|--------|--------|
| Integration branch | **`v1.3.0-dev`** when 1.2 is on `main` |
| Production line until then | **`main` @ 1.2.x** patches; this plan does not block 1.2 |
| Theme streams (seed) | **P** · **T** · **W-cfg** · **W-id** · **W-audit** (discover) · **A** · **L** · **N** insights thin slice (discover → ship) · (+ **AC-fg** / residual as capacity) |
| Policy storage | Prefer **app Settings** (DB) with env as override / bootstrap where it already exists |
| Host SSH identities | At least **two** optional credentials per host: **fleet / least-priv** (default jobs + console) + **privileged** (break-glass console / elevated jobs later); separate Fernet keys |
| Shell audit | **Opt-in**; default off or session-meta only (1.2); full command/response is **discover → promote** |
| Insights | **Discover + thin slice only** — compose existing fleet signals; not a second Grafana |
| Semver | Additive minor; document migrations for defaults that change behaviour |
| Out of focus for seed | Multi-tenant SaaS · SAML · full Elasticsearch · **video / full PTY replay** dual-control console · full custom BI / arbitrary SQL dashboards |

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

### Stream **W-id** — Multi-identity host access (least-priv + privileged)

**Today (1.2):** One SSH identity per server (`ssh_username` + one encrypted private key). Jobs and webshell both use that identity. Operators who want least-privilege fleet automation must either over-privilege the herder user or rekey manually outside the product.  
**Wanted:** Model **multiple named identities** per host (start with two), pick which to use for **console** (and later optionally for specific job classes). **Discover** enrollment UX and sudo/capability notes during 1.3 design.

| ID | Item | Notes |
|----|------|--------|
| W-id1 | Data model | e.g. `ServerSshIdentity` (or JSON list): `id`, `label`, `role` (`fleet` / `privileged` / custom), `username`, encrypted private key, optional public key fingerprint, `is_default_for_jobs`, `is_default_for_console`, enabled |
| W-id2 | Migrate 1.x single key | Existing `ssh_username` + key → one **fleet** identity; UI remains simple for single-identity hosts |
| W-id3 | Host edit / onboard UI | Add / rotate / remove identities; never show PEM in clear without step-up; separate upload per identity |
| W-id4 | Console “Connect as…” | Ticket + UI picker: least-priv (default) vs privileged (extra confirm + stronger step-up / audit reason optional) |
| W-id5 | Jobs vs console | Default: **all automated jobs** stay on fleet/least-priv identity only; privileged key **console-only** unless admin later opts a job type in (out of default Must) |
| W-id6 | Test connection | Per-identity “test SSH”; show username + fingerprint in UI/audit, not key material |
| W-id7 | Discovery notes | Document recommended host setup: `piherder` (or deploy user) with docker/rsync group, no password sudo; separate `piherder-admin` / root-capable key for break-glass; deploy public keys via existing SSH deploy path per identity |
| W-id8 | RBAC | Who may open privileged console (admin only vs operator+); pairs with **AC-fg** later (“console elevated” feature gate) |
| W-id9 | Demo | Seed only least-priv synthetic identity; privileged console still disabled under `DEMO_MODE` |

**Product shape (sketch):**

```text
Host: lab-core
  ├─ Identity "fleet"     user=piherder        key=…  ← jobs + default console
  └─ Identity "elevated"  user=piherder-admin key=…  ← console only (opt-in)
Console open → Connect as: [ fleet (default) ▾ | elevated ]
```

**Non-goals (W-id):** Password-based SSH; agent-based multi-user; automatic discovery of all local OS users on the host (optional later spike only); shared break-glass dual-control (two-person rule).

**Depends on:** 1.2 webshell ticket path uses a single identity today — extend ticket payload with `identity_id`.

---

### Stream **W-audit** — Command-level webshell audit (discover → optional ship)

**Today (1.2):** Audit rows for console open / close / grant / deny (+ client IP, duration, actor). Interactive **command capture** was intentionally **best-effort / not promised** ([FEATURE_PLAN_HOST_LIFECYCLE.md](FEATURE_PLAN_HOST_LIFECYCLE.md) P5).  
**Wanted:** **Discover** a lower level of auditing: record **commands issued** via webshell and **responses** (or summaries), with **optional redaction** when passwords/secrets appear. Opt-in per instance or per session.

| ID | Item | Notes |
|----|------|--------|
| W-audit0 | **Discovery spike** | Capture options: (A) line-buffered PTY transcript server-side · (B) shell wrapper / `script` · (C) client-sent command events only (weak). Prefer **A** with size caps |
| W-audit1 | Opt-in policy | Settings: off (default) · commands only · commands + truncated output · full session transcript (harder) |
| W-audit2 | Storage | Append-only blob or chunked rows tied to `console_session_id`; retention + max bytes per session; herder self-backup implications |
| W-audit3 | Redaction | Heuristics: password prompts (`password:`, `Password for`, sudo); patterns for tokens; optional “pause audit while typing password” control sequence; never claim perfect secrecy |
| W-audit4 | UI | Session detail: timeline of commands; download/export for admins; viewer role cannot read transcripts |
| W-audit5 | Integrity | Same encryption-at-rest bar as other secrets where feasible; audit **that** a transcript exists even if body purged |
| W-audit6 | Legal / ops docs | Retention, who can view, “this may capture secrets typed at the prompt”, disable in demo |
| W-audit7 | Non-goals clarity | **Not** video session recording; **not** dual-control approval; **not** perfect keystroke timing for forensics lab grade |

**Discovery exit criteria:** Spike proves (or rejects) reliable-enough command boundary detection on interactive bash + redaction of common password prompts without multi-second lag; estimate storage for 1h active session; decide Must vs Cap for 1.3 freeze.

**Security notes:**

- Transcripts are high-sensitivity (may contain secrets despite redaction). Default **off**.  
- Privileged-identity sessions (**W-id**) should force at least “commands only” or warn when audit is off.  
- Demo mode: never persist real transcripts.

**Depends on:** Stable 1.2 console WS path; **W-cfg** for retention knobs may share Settings surface.

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

### Stream **N** — Insights: discovery + thin-slice reporting / custom dashboards

**Today:** Dashboard / ops-hero pulses, Jobs, Audit, Alerts, per-host Overview, maps, and integration detail pages. No operator-owned **report layout** or savable **custom dashboard** of mixed widgets. Roadmap item **N** was “discovery + first thin slice post v1.0” ([ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md)).  
**Wanted:** Run a short **discovery** (what operators actually want on one screen), then ship a **thin slice** — not Grafana-in-herder.

| ID | Item | Notes |
|----|------|--------|
| N0 | **Discovery** | Interview / ops notes: top 5 “I open PiHerder to see…”. Inventory existing data sources (hosts online, job success rate, open alerts by severity, cert expiry, backup last-ok, docker unhealthy, nmap new devices, map down edges). Decide home vs dedicated **Reports** route |
| N1 | Metric registry (thin) | Named, versioned metrics/cards: `id`, label, query or service call, refresh hint, RBAC (viewer-safe). Reuse existing services — no parallel warehouse |
| N2 | Built-in “Fleet health” board | One default dashboard: 4–8 fixed widgets from the registry (counts + links into existing pages). Good enough for most single-operator labs |
| N3 | Custom dashboard v1 | User (or admin) can **add / remove / reorder** widgets from the registry on **one** personal or instance board; persist layout JSON; no arbitrary SQL |
| N4 | Time windows (optional Cap) | “Last 24h / 7d” on job/audit derived cards only where cheap; no long-term TSDB |
| N5 | Export Cap | Optional CSV/PDF of a single summary card or board later — not Must |
| N6 | Grafana coexistence | Keep deep metrics/graphs in Grafana; herder boards are **ops summary + navigation**, not timeseries product. Document “when to use which” |
| N7 | Demo seed | Seed a pretty default board so the public demo shows the surface |

**Product shape (sketch):**

```text
Reports / Dashboard (custom)
  ├─ Widget: Hosts up/down          → /servers?status=…
  ├─ Widget: Open alerts by severity → /notifications
  ├─ Widget: Backups stale          → /servers?… or jobs
  ├─ Widget: Certs expiring ≤30d    → certificates
  └─ [ + Add widget ] from registry
```

**Discovery exit criteria:** Written one-pager of N0 findings; pick **N2 only** vs **N2+N3** for freeze; reject scope creep (custom PromQL, multi-page BI, embedding iframes of random apps as “widgets” without security review).

**Non-goals (N):** Full Grafana replacement; arbitrary SQL / PromQL builder; multi-tenant shared gallery marketplace; real-time streaming charts; storing high-cardinality metrics history in Postgres forever.

**Depends on:** Stable 1.x data already in DB; **L** helps if boards link into long lists; **A** severity taxonomy improves alert widgets.

---

## 3. Ship bar (draft — finalise at train open)

| Priority | Streams | Bar |
|----------|---------|-----|
| **Must** | **L** (at least Servers + Docker + discovery) · **P** or **T** (at least one policy stream fully usable) · **W-id** core (fleet + privileged identity + console picker) | Scale lists + at least one security policy + least-priv/privileged connect-as |
| **Should** | **P** + **T** · **W-cfg** · **A** · **W-audit** if spike green · **N2** built-in fleet board (after **N0**) | Full policy set + console knobs + alerts + opt-in command audit + thin reporting surface |
| **Discover / Cap** | **N0** discovery · **N3** custom layout · **W-audit** spike · **AC-fg** · ACME · branding | Promote only if Must green |

Success criteria (draft):

1. Admin can configure password policy; all password entry paths enforce it and show the same rules.  
2. Admin can configure force-2FA / step-up windows for the catalogued sensitive actions (incl. console).  
3. Console idle/max/concurrency/step-up knobs adjustable in Settings without editing compose for common cases.  
4. Host can store **fleet** + **privileged** SSH identities (separate keys/users); console offers **Connect as…**; jobs stay on fleet by default.  
5. *(If W-audit promoted)* Opt-in command (± response) audit with redaction heuristics and retention; default off; wiki warns about residual secret capture.  
6. Map/stack/cert-style alerts have documented severities and per-category tuning; channels respect filters.  
7. Servers + Docker service lists support page size + free-text filter without loading unbounded HTML.  
8. *(If N promoted)* Operators have at least a **built-in fleet health board** of existing signals; optional **one** customisable layout from a fixed widget registry (no arbitrary queries).

---

## 4. Quality bar (draft)

| Gate | Target |
|------|--------|
| Unit | Hold ≥ 55% (raise only if easy) |
| Tests | Policy validate matrix · settings round-trip · list query unit tests · console limit apply · multi-identity ticket + redaction unit tests |
| E2E | Settings policy save · one large list page-size · connect-as privileged confirm · console settings smoke if flag on |
| Docs | ADMIN + wiki Security / Alerts / Console (identities + audit) / list UX / Reports; `mkdocs build --strict` at freeze |
| Security | Policy changes audited; privileged console extra step-up; transcripts access-controlled; demo never stores real shell transcripts; dashboard widgets respect RBAC |

---

## 5. Dependencies on v1.2

| 1.2 deliverable | Why 1.3 needs it |
|-----------------|------------------|
| WebAuthn + step-up helpers | **T** / **W-cfg** factor policy builds on the same paths |
| SSO 2FA parity | **T** must not re-fork SSO vs password |
| Webshell tickets + env knobs | **W-cfg** moves knobs into settings; **W-id** extends ticket with identity; **W-audit** taps the same WS stream |
| Single-key server model | Migration baseline for **W-id** |
| Demo mode IP scrub / OpenAPI gate | Keep demo safe when settings surfaces expand; no shell transcripts on demo |
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
- **Video / full interactive session replay** and **dual-control** (two-person) console — still out; **W-audit** is opt-in command/transcript style only  
- Guaranteeing redaction catches every secret typed at a shell  
- Auto-enumerating all OS users on a host as “identities”  
- Weakening public **demo** into a multi-user admin sandbox  

---

## 8. Capture log

| Date | Note |
|------|------|
| 2026-08-10 | Opened while finishing **v1.2** demo/ops. Seed streams: **P** password policy · **T** 2FA/step-up policy · **W-cfg** console timeouts/limits/step-up · **A** map alert severity + granular alerts · **L** pagination + free-text/smart search. |
| 2026-08-10 | Added **W-id** multi-identity host SSH (least-priv fleet user + privileged user, separate keys, Connect as…) and **W-audit** discover lower-level webshell audit (commands + responses, optional password redaction). |

Add deferred 1.2 items here as one-line bullets when freeze decides “→ 1.3”.

---

## 9. Immediate next steps (when ready)

| # | Step |
|---|------|
| 1 | Finish **v1.2.0** freeze / tag / Hub |
| 2 | Open **`v1.3.0-dev`** + lock Must/Should from this seed |
| 3 | Spike **L1** shared list component + **P1** settings schema (cheap wins first) |
| 4 | Spike **W-id** model + console ticket identity field (no UI polish) |
| 5 | Spike **W-audit0** PTY capture + redaction on a throwaway host; promote or Cap |
| 6 | Write short feature notes (or extend host-lifecycle / IAM plans) for **T** / **W-cfg** / **W-id** / **W-audit** before coding |

---

*End of planning capture — not a commitment to ship every stream in one minor.*
