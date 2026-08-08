# Feature Plan: SSO / OIDC (Stream S — v1.2)

**Document:** `docs/FEATURE_PLAN_SSO_OIDC.md`  
**Status:** **Design** (2026-08-08) — implement on `v1.2.0-dev`  
**Owner:** Bjorn  
**Train:** [PLAN_v1.2.0.md](PLAN_v1.2.0.md) Stream **S**  
**Related:** [FEATURE_PLAN_IAM_2FA_UPDATES_NOTIFICATIONS.md](FEATURE_PLAN_IAM_2FA_UPDATES_NOTIFICATIONS.md) · [SECURITY.md](../SECURITY.md) · [ADMIN.md](ADMIN.md) · WebAuthn Stream **I** (passkeys as 2FA)

---

## Goal

Let operators sign in to PiHerder with a **BYO OpenID Connect (OIDC) IdP** (Authentik, Keycloak, Authelia, Google Workspace, Entra ID, …), while keeping **local password (and 2FA) as break-glass**.

Support real-world account lifecycle:

1. **Login via SSO** (new or existing mapped user)  
2. **Link both ways:** SSO login can attach to an existing local account (email auto-link); Account while logged in can attach SSO (explicit)  
3. **Optional remove password** after a trusted SSO link exists  
4. **Unlink** SSO — and **require setting a password** when that would leave the user with no password login path  
5. **2FA always when required** — password and SSO share the same PiHerder TOTP/passkey / Force 2FA gates; sensitive link/unlink/password-remove re-validate 2FA when enrolled

**Non-goals (v1.2):** SAML · multi-tenant org isolation · passwordless-only IdP with no local recovery · social “Sign in with GitHub” as a product surface (generic OIDC covers it if the IdP does).

---

## Why

- Homelabs and small teams already run Authentik / Authelia / Keycloak.  
- Air-gapped and DR still need **local admin** without IdP uptime.  
- Operators often create a local admin first, then want **the same account** behind SSO (not a second JIT user).  
- SSO-only accounts without a password are fine **if** unlink is blocked until a password is set again.

---

## Decisions (locked unless reversed)

| # | Decision |
|---|----------|
| 1 | **Protocol:** OIDC **authorization code + PKCE** (public-ish SPA safety not required; still use PKCE for all clients). Confidential client + client secret (Fernet in DB). |
| 2 | **Library spike first:** prefer `authlib` (or equivalent mature OIDC client); avoid hand-rolled JWT validation. |
| 3 | **One IdP config** per PiHerder instance (v1.2). Multi-IdP later if needed. |
| 4 | **Identity key:** `(issuer, subject)` unique; email is display / soft match only, not the sole link key. |
| 5 | **JIT provision** when SSO login finds no link and email matches **no** existing user → create user with role from claims (default least privilege). |
| 6 | **Link is bidirectional (first-class):** (a) **SSO login → local account** via verified-email auto-link (or existing `OidcIdentity`); (b) **logged-in Account → SSO** via explicit Link. Same `OidcIdentity` row either way. |
| 7 | **Email match on first SSO login:** if a **single** active local user has the same verified email and **no** SSO link yet → **auto-link** (audited), then continue login (incl. 2FA). Ambiguous / disabled / already linked → fail closed. |
| 8 | **Explicit link** from Account while logged in: start OIDC with `prompt=login` (or IdP equivalent); on callback **bind** `(iss, sub)` to **current** user if unlinked. |
| 9 | **Roles:** map IdP groups/claims → `admin` \| `operator` \| `viewer`. Unmapped → **viewer** (or configured default). Local break-glass admin is never auto-demoted below operator without an explicit setting (see orphan / role sync policy). |
| 10 | **Local password login always available** for break-glass unless user **opts into password removal** after SSO is linked. Global “force SSO for non-break-glass” hides password form for others; at least one break-glass path remains (local admin flag or always-on password for designated accounts). |
| 11 | **Password optional** only when `User` has ≥1 active `OidcIdentity` link. Clearing password sets `hashed_password` to a non-usable sentinel (or null if migration allows) and `password_login_enabled=false`. |
| 12 | **Unlink** requires either (a) a usable local password **or** (b) set a new password in the same flow before unlink completes. Never leave a user with no login method. |
| 13 | **2FA is path-agnostic.** Whenever PiHerder would require 2FA on password login, it **must** require the same on SSO login and on sensitive identity actions. IdP is factor one only; PiHerder TOTP / passkey still apply. **No exemption** for SSO-only users when 2FA is required. |
| 14 | **When is 2FA “required”?** (same helpers as password path) · User has TOTP and/or passkey enrolled → **step-up before full session** (and before completing link/unlink/remove-password when already in a session). · Instance **Force 2FA** and user has no second factor yet → after identity is proven (password or SSO), send to existing **force-2FA enroll** wall (not a full session until enrolled, same as today). |
| 15 | **Client secret** in `AppSetting` / dedicated settings blob, **Fernet-encrypted** (same pattern as integration credentials). Included in herder self-backup. |
| 16 | **Redirect URI:** `{PIHERDER_PUBLIC_URL}/auth/oidc/callback` (document exact path). HTTPS required except localhost. |
| 17 | **Demo mode:** SSO UI may be shown; real external IdP optional. Shared demo password remains primary entry after CF Access. |

---

## Principles

1. **Never lock out the last recovery path** — unlink and password-remove are gated.  
2. **Fail closed on identity ambiguity** — no silent merge of two people.  
3. **Audit everything identity-related** — login, link, unlink, password remove/set, JIT create, role change from claims.  
4. **Least privilege by default** for new JIT users.  
5. **Local break-glass survives IdP outage** — at least one admin with password (ops runbook).  
6. **Reuse** Account page, Settings patterns, JWT session cookies, audit log — do not invent a parallel session model.  
7. **One 2FA policy for all entry paths** — password and SSO share the same step-up / force-2FA gates; never skip 2FA because the IdP already authenticated.  
8. **Link either direction** — login can attach SSO to an existing local user; Account can attach SSO while already signed in.

---

## Scenarios (product acceptance)

### S-A — First-time SSO login (new user)

1. Operator opens Login → **Continue with SSO**.  
2. Redirect to IdP; consent / login; return to `/auth/oidc/callback`.  
3. No existing `OidcIdentity` for `(iss, sub)`; email not used by anyone.  
4. **JIT create** `User` (email from claim, role from map, **no password** / password login disabled, SSO linked).  
5. **2FA gate** (see S-M): new users usually have no 2FA yet → if **Force 2FA**, send to enroll wall before full app access; else full session.  
6. Audit `sso_login` + `sso_user_provisioned`.  
7. Optional prompt: “Set a local password for break-glass” (soft CTA on Account).

### S-B — SSO login links to existing local account (auto-link)

**Direction: SSO → local account**

1. Local user `admin@lab.example` already exists (password ± 2FA).  
2. First SSO login returns same **email** (prefer `email_verified=true` when claim present).  
3. Exactly one active user with that email and no prior link → **auto-link** (do not issue full session yet).  
4. Audit `sso_link` (reason=`email_match`).  
5. Continue as **S-D / S-M**: apply **2FA step-up** if that user has 2FA enrolled; Force 2FA enroll if required and missing.  
6. Only then full session + audit `sso_login`.  
7. If email already linked to another subject, or conflict → error, **no** link, no session.

### S-C — Link OIDC to **current** account (logged-in)

**Direction: local session → SSO**

1. User is already logged in (password or prior SSO session).  
2. Account → **Connected accounts** → **Link SSO**.  
3. If user has 2FA enrolled → **step-up 2FA first** (or immediately before accepting callback bind — same bar as other sensitive Account actions).  
4. OIDC authorize with state bound to `user_id` + CSRF; on success bind `(iss, sub)` to **this** user.  
5. If `(iss, sub)` already linked to **another** user → refuse with message.  
6. If this user already has a link to same issuer → refuse or “already linked”.  
7. Audit `sso_link` (reason=`account_explicit`).  
8. Show IdP label / email / subject snippet on Account.

### S-D — Login when already linked

1. Continue with SSO → match `OidcIdentity` → load user.  
2. Optionally **refresh role** from claims if “sync roles on every login” enabled (default **on**).  
3. Inactive user → refuse.  
4. **2FA gate (S-M)** before full session — same as password login for that user.  
5. Audit `sso_login` (detail includes `method=oidc` and whether 2FA was used).

### S-E — Remove local password (SSO-only login)

**Preconditions:** user has ≥1 active OIDC link; not the sole break-glass admin if policy forbids; confirm UI.

1. Account → Security → **Remove password**.  
2. Confirm: “You will only be able to sign in with SSO until you set a password again.”  
3. Re-auth: if 2FA enrolled → **validate 2FA**; also current password if still set, else short OIDC re-login when already SSO-only.  
4. Clear usable password; `password_login_enabled=false`.  
5. Audit `user_password_removed`.  
6. Login page: password form still works for other users; this user gets clear error if they try password (“use SSO or set a password from Account while signed in” — only possible if they still have a session).

### S-F — Set / restore local password (while SSO-linked)

1. Account → **Set password** (no “current password” if password was removed; if password exists, change-password flow unchanged).  
2. If 2FA enrolled → step-up before accepting new password when no current password (high risk).  
3. Strength rules same as today.  
4. `password_login_enabled=true`; audit `user_password_changed` / `user_password_set`.

### S-G — Unlink SSO (password already set)

1. Account → Connected accounts → **Unlink**.  
2. If 2FA enrolled → **validate 2FA** before unlink completes.  
3. Confirm.  
4. Delete/disable `OidcIdentity` for this user (+ issuer).  
5. Audit `sso_unlink`.  
6. User continues with password (+ 2FA as configured).

### S-H — Unlink SSO when **no** password (must set password)

1. User is SSO-only (`password_login_enabled=false` or unusable hash).  
2. **Unlink** opens a **combined flow**: set new password (+ confirm) → then unlink.  
3. If 2FA enrolled → **validate 2FA** in the same flow (before unlink).  
4. If password set fails validation, do **not** unlink.  
5. Ordered steps: re-auth/2FA → password first → unlink.  
6. Audit `user_password_set` then `sso_unlink`.  
7. Never complete unlink that leaves zero login methods.

### S-I — Force SSO (instance setting)

1. Settings → Authentication: **Require SSO** for non–break-glass users.  
2. Login page: primary SSO button; password form hidden or only for break-glass emails / `is_break_glass` flag.  
3. Break-glass local admin always can password-login (ops recovery).  
4. Linked users without password: SSO only (expected).

### S-J — IdP outage / misconfiguration

1. SSO button shows error from discovery / token endpoint failure (no stack traces to users).  
2. Local password path still works for break-glass.  
3. Audit `sso_login_failed` with safe detail.

### S-K — Orphan / disabled at IdP

| Policy (configurable) | Behaviour |
|----------------------|-----------|
| **Link remains** (default soft) | User can still SSO until admin unlinks; role may drop to default if groups missing |
| **Disable user if not in allow-group** | On login, if required group missing → refuse + optional `is_active=false` (aggressive; off by default) |

v1.2 ships soft default; aggressive disable is Should/Discover.

### S-L — Admin creates user who will only use SSO

1. Users admin: create user with email, role, **no invite password** / “SSO only” checkbox.  
2. User signs in via SSO (email match auto-link or admin pre-linked later).  
3. Aligns with existing `must_change_password` patterns — extend carefully.  
4. Force 2FA still applies after first SSO identity proof (enroll wall).

### S-M — 2FA after SSO (login and sensitive actions) — **Must**

Reuse the **same** post-password machinery (`pending` login cookie / 2FA page / passkey JSON / force-2FA routes). Do not fork a second 2FA UX.

| Condition | Behaviour |
|-----------|-----------|
| User has TOTP and/or passkey | After OIDC callback resolves user (linked or auto-linked), issue **pending** auth only → existing **2FA step-up** (TOTP **or** passkey) → then full JWT session |
| User has no 2FA; Force 2FA **off** | Full session after OIDC (first-factor IdP only) |
| User has no 2FA; Force 2FA **on** | After OIDC, limited session / same force-2FA enroll redirect as password path; no fleet access until enrolled |
| Trusted device cookie | Same rules as password login (skip step-up only if trusted device valid for that user) |
| Explicit **Link** / **Unlink** / **Remove password** while session active | If user has 2FA enrolled → **always re-validate 2FA** before completing the mutation |

```text
Password path:  password ──► [2FA if required] ──► session
SSO path:       IdP     ──► [2FA if required] ──► session   # same gate helpers
Account link:   session ──► [2FA if enrolled] ──► IdP ──► bind
```

**Lock:** SSO never bypasses PiHerder 2FA. IdP MFA (if any) is independent and **not** a substitute for PiHerder TOTP/passkey when those are enrolled or Force 2FA applies.

---

## Stream checklist (maps to PLAN Stream S)

| ID | Item | Stance | Notes |
|----|------|--------|-------|
| **S1** | OIDC auth code + PKCE; BYO IdP | Must | Discovery + callback |
| **S2** | Groups/claims → roles | Must | Configurable claim path + map |
| **S3** | JIT provision + orphan policy | Must | Soft orphan default |
| **S4** | Local password remains; optional force SSO | Must | Break-glass always |
| **S5** | Settings UI: issuer, client id/secret, scopes, role map | Must | Fernet secret |
| **S6** | Audit events | Must | See below |
| **S7** | **Link both ways:** SSO login → local (auto/email) **and** Account → SSO (explicit) | Must | S-B + S-C |
| **S8** | **Remove password** when SSO linked | Must | Scenario S-E |
| **S9** | **Unlink** + force set password if needed | Must | Scenarios S-G, S-H |
| **S10** | Login UX: SSO button + errors | Must | |
| **S11** | ADMIN + SECURITY + wiki | Must | |
| **S12** | Focused unit tests + mock IdP | Must | |
| **S13** | **2FA gate on SSO login + link/unlink/remove-password** (same as password path) | Must | Scenario S-M |
| SAML | — | Defer | |
| Multi-IdP | — | Defer | |

---

## Architecture

```text
Browser
  │  GET /auth/login  →  [Continue with SSO]
  │  GET /auth/oidc/login          (start; PKCE + state cookie/JWT)
  │  ──► IdP authorize
  │  GET /auth/oidc/callback       (code → tokens → claims)
  │         │
  │         ├─ match OidcIdentity (iss, sub)
  │         ├─ else email auto-link (strict rules)     # SSO → local
  │         ├─ else JIT User + OidcIdentity
  │         └─ link mode: bind to session user_id      # local → SSO
  │         │
  │         ▼
  │    Resolve user ──► same post_login_path / 2FA helpers as password
  │         ├─ has 2FA?     → pending cookie → TOTP/passkey step-up
  │         ├─ force 2FA?   → enroll wall
  │         └─ else         → full session JWT
  │
Account (vice versa)
  GET  /auth/account               → Connected accounts card
  POST/GET /auth/oidc/link         → [2FA if enrolled] → authorize state.mode=link
  POST /auth/account/password/remove  → [2FA if enrolled]
  POST /auth/account/password/set
  POST /auth/oidc/unlink           → password + [2FA if enrolled] gates

Settings (admin)
  GET/POST authentication / OIDC config in AppSetting (encrypted secret)
```

### Components

| Piece | Responsibility |
|-------|----------------|
| `app/services/oidc_svc.py` | Discovery, PKCE, token exchange, claims parse, role map, errors |
| `OidcIdentity` model | Persist link rows |
| `app/routers/auth_oidc.py` (or extend `auth.py`) | Login/callback/link/unlink routes |
| `app_settings` keys | Non-secret OIDC config; secret Fernet field |
| Login + Account templates | Buttons, cards, confirm modals |
| Tests | Claim fixtures; link/unlink/password matrix |

### Suggested library

Spike order:

1. **authlib** (Starlette/FastAPI friendly)  
2. Fallback: `oic` / manual + `PyJWT` only if authlib fights stack  

Record spike result in this doc’s changelog.

---

## Data model

### `OidcIdentity` (new table)

```text
id                  PK
user_id             FK → user.id  (index)
issuer              str   # normalized iss URL
subject             str   # sub claim
email_at_link       Optional[str]  # snapshot at link time
display_name_at_link Optional[str]
claims_json         Optional[str]  # last login claims subset (no tokens)
linked_at           datetime
last_login_at       Optional[datetime]
unique (issuer, subject)
# Optional: unique (user_id, issuer) — one link per issuer per user
```

### `User` extensions

```text
# Existing hashed_password is currently required (str).
# Migration options (pick in implementation):
#   A) hashed_password Optional[str] = None  when SSO-only
#   B) keep dummy unusable hash + password_login_enabled: bool = True
# Prefer B if fewer nullability ripples through codebase; document sentinel.

password_login_enabled: bool = True   # False after remove-password
# Optional later:
# is_break_glass: bool = False        # always allow password when force SSO
```

**Recommendation:** `password_login_enabled` + **unusable** password hash (bcrypt of random or fixed invalid marker) so existing `verify_password` paths fail closed without null checks everywhere. Login checks `password_login_enabled` first.

### Settings keys (`AppSetting.data_json` + encrypted secret)

| Key | Purpose |
|-----|---------|
| `oidc_enabled` | bool |
| `oidc_issuer` | URL (discovery base) |
| `oidc_client_id` | str |
| `oidc_client_secret_encrypted` | Fernet (or separate credentials blob) |
| `oidc_scopes` | default `openid email profile` (+ groups if needed) |
| `oidc_role_claim` | e.g. `groups` or `realm_access.roles` |
| `oidc_role_map` | JSON `{"piherder-admins":"admin", ...}` |
| `oidc_default_role` | default `viewer` |
| `oidc_sync_roles_on_login` | bool default true |
| `oidc_require_sso` | bool — hide password for non–break-glass |
| `oidc_auto_link_by_email` | bool default true |
| `oidc_require_email_verified` | bool default true when claim present |
| `oidc_display_name` | button label e.g. “Company SSO” |
| `oidc_allowed_email_domains` | optional allow-list |

Discovery document URL: `{issuer}/.well-known/openid-configuration` (issuer trailing-slash rules per OIDC).

---

## Routes

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| GET | `/auth/oidc/login` | Public | Start login; set PKCE+state cookie |
| GET | `/auth/oidc/callback` | Public | Finish login or link |
| GET | `/auth/oidc/link` | Session | Start link for current user |
| POST | `/auth/oidc/unlink` | Session | Body/form: confirm; password fields if needed |
| POST | `/auth/account/password/remove` | Session | SSO link required |
| POST | `/auth/account/password/set` | Session | For SSO-only users (no current password) |
| GET/POST | Settings OIDC section | Admin | Test connection / save |

State JWT/cookie payload (short-lived, signed with app secret):

```text
{ "mode": "login" | "link", "user_id": null | int, "nonce": "...", "cv": "code_verifier", "exp": ... }
```

---

## UI

### Login (`/auth/login`)

- If `oidc_enabled`: primary or secondary **Continue with {display_name}**.  
- Password form remains unless `oidc_require_sso` (then break-glass only).  
- Errors: `?error=sso_denied` · `sso_config` · `sso_link_conflict` · `sso_email_conflict` · `sso_inactive`.

### Account — Connected accounts

| Element | Behaviour |
|---------|-----------|
| Status | “Linked to {issuer host} as {email/sub}” or “Not linked” |
| Link | Button → S-C |
| Unlink | Button → S-G / S-H modal |
| Password card | Change / Set / **Remove password** (only if linked) |

**Unlink modal (no password):**

```text
Set a local password to keep access after unlinking SSO.
[ new password ] [ confirm ]
[ Cancel ]  [ Set password & unlink ]
```

**Remove password modal:**

```text
You will sign in with SSO only. You can set a password again anytime.
[ Cancel ]  [ Remove password ]
```

### Settings — Authentication / SSO

- Enable, issuer, client id, secret (write-only after save), scopes, role claim, role map editor (simple JSON or key-value rows).  
- “Test discovery” button (admin).  
- Force SSO toggle + help text about break-glass.  
- Redirect URI copy-paste for IdP admin.

---

## Role mapping

1. Read claim path (`groups` array, or nested JSON path).  
2. First matching map entry wins (document order) **or** highest privilege wins — **lock: highest privilege wins** among matched groups (`admin` > `operator` > `viewer`).  
3. No match → `oidc_default_role` (viewer).  
4. On login with sync on: update `user.role` if changed; audit `user_role_changed` (source=`oidc`).  
5. Local-only users (no link): role only via Users admin.

**Break-glass:** optional seed/docs: first admin created at onboard is break-glass; not demoted by missing IdP groups when `oidc_protect_break_glass` (default true) — if set, skip role sync demotion for that user.

---

## Security

| Risk | Control |
|------|---------|
| Account takeover via open redirect | Fixed redirect_uri; state/nonce; no open redirects |
| Link takeover | Link mode requires existing session; refuse foreign sub; CSRF state |
| Email auto-link abuse | Prefer `email_verified`; unique email; no link if already bound |
| Admin group mis-map | Default viewer; highest-privilege map explicit; audit role changes |
| Secret leak | Fernet; never log tokens; self-backup encrypted blob only |
| Password remove lockout | Require active OIDC link; unlink blocked without password |
| CSRF on callback | One-time state |
| Token storage | **Do not** store refresh tokens in v1.2 unless needed; access token only in memory for claims |
| Force SSO lockout | Document break-glass; never remove last password admin without warning in UI |

---

## Audit actions

| Action | When |
|--------|------|
| `sso_login` | Successful SSO session |
| `sso_login_failed` | Denied / error (safe detail) |
| `sso_user_provisioned` | JIT create |
| `sso_link` | Explicit or email auto-link (`details.reason`) |
| `sso_unlink` | Unlink |
| `user_password_removed` | Remove password |
| `user_password_set` | Set password after SSO-only / unlink gate |
| `user_role_changed` | Role sync from claims (if not already covered) |

Reuse existing `user_login` only for password/2FA paths so metrics stay clear — **or** also emit `user_login` with detail `method=oidc`. Prefer dual: `sso_login` + optional `user_login` for “last login” UX consistency (`last_login_at` always updated).

---

## Interaction with WebAuthn / TOTP / Force 2FA

| Login / action | PiHerder 2FA |
|----------------|--------------|
| Password login | Existing: TOTP and/or passkey step-up; Force 2FA enroll wall |
| **SSO login** | **Same helpers** — if user has 2FA enrolled → step-up; if Force 2FA and none → enroll wall. IdP MFA does **not** replace this. |
| Trusted device | Same skip rules as password path |
| Explicit link / unlink / remove password | Re-validate 2FA when enrolled (sensitive identity change) |
| Webshell **W3** | Unchanged: step-up before ticket (TOTP or passkey) regardless of how the session was opened |

**Implementation note:** After OIDC callback, call the same “user needs 2FA / force enroll / issue session” path used after password verify (pending login token + `/auth/...` 2FA routes). Do not mint a full access cookie until that path would for password login.

Document in SECURITY.md: PiHerder 2FA is **defense in depth** after IdP; operators who want IdP-only MFA can leave PiHerder 2FA unenrolled and Force 2FA off.

---

## Testing

| Layer | Cases |
|-------|-------|
| Unit | Role map; email auto-link rules; unlink gate; password_login_enabled; state mode |
| Unit | JIT create; conflict when sub owned by other user |
| Unit | SSO → pending 2FA when TOTP/passkey enrolled; Force 2FA enroll path; trusted device skip parity |
| Unit | Link/unlink/remove-password blocked without 2FA when enrolled |
| Integration | Mock token endpoint + claims → callback → 2FA pending |
| Manual | Authentik or Keycloak docker; link both ways; 2FA matrix; remove password / unlink |
| E2E | Mock IdP or skip if flaky; login SSO button + Account card + 2FA if fixture allows |

Coverage: contribute to Stream Q “focused tests for OIDC”.

---

## Docs deliverables

| Doc | Content |
|-----|---------|
| ADMIN.md | IdP setup, redirect URI, role map examples (Authentik/Keycloak/Entra/Google) |
| SECURITY.md | Threat model, break-glass, password remove, force SSO |
| Wiki | Short “Sign in with SSO” operator page |
| PLAN_v1.2.0.md | S7–S9 + link to this plan |

---

## Implementation order (PRs)

```text
S-PR1  Spike: authlib + discovery against one IdP; note in changelog
S-PR2  Model OidcIdentity + User.password_login_enabled + migration
         + settings keys (no UI polish)
S-PR3  Login + callback + JIT + email auto-link + role map
         + **wire SSO into existing 2FA / force-2FA post-login path** (S1–S3, S13)
S-PR4  Settings UI + Fernet secret + enable flag (S5)
S-PR5  Account link / unlink / remove-password / set-password
         + 2FA step-up on those actions (S7–S9, S13)
S-PR6  Force SSO + break-glass (S4) + docs + tests polish
```

**Suggested first vertical slice after spike:** S-PR2 + S-PR3 (SSO login + 2FA parity), then S-PR5 (bidirectional link lifecycle) before force-SSO.

---

## Success criteria

- [ ] Operator configures OIDC in Settings; discovery succeeds.  
- [ ] New user can JIT login via SSO with least-privilege role.  
- [ ] Existing local user **auto-links on SSO login** (email) **and** can **explicitly link from Account** while logged in.  
- [ ] User with TOTP/passkey **must** complete PiHerder 2FA after SSO before full session (same as password).  
- [ ] Force 2FA applies after SSO when user has no second factor.  
- [ ] Link / unlink / remove-password re-check 2FA when enrolled.  
- [ ] Linked user can **remove password** and still SSO login (still 2FA if enrolled).  
- [ ] SSO-only user **cannot** unlink without **setting a password** in the same flow.  
- [ ] Linked user with password can unlink and password-login.  
- [ ] Local break-glass works with IdP down.  
- [ ] Role map assigns admin/operator/viewer; audit trail complete.  
- [ ] ADMIN + SECURITY updated; unit tests cover link + 2FA gate matrix.

---

## Open questions (resolve in spike / S-PR1)

| # | Question | Lean |
|---|----------|------|
| 1 | Store refresh tokens? | **No** for v1.2 |
| 2 | Multiple links per user (Google + Authentik)? | **One issuer per user** first; schema allows more later |
| 3 | Google Workspace without groups → all viewer? | Yes + docs for admin role map via Workspace groups if available |
| 4 | `must_change_password` + SSO-only | Skip must-change when password disabled |
| 5 | Library | authlib unless blocked |

---

## Changelog

| Date | Note |
|------|------|
| 2026-08-08 | Initial design: S1–S6 + **S7 link current account**, **S8 remove password**, **S9 unlink with password gate**; scenarios S-A–S-L. |
| 2026-08-08 | **Link both ways** (SSO→local auto-link + Account→SSO). **S13 / S-M:** always run PiHerder 2FA when required — SSO login and sensitive identity actions; no SSO exemption. |
