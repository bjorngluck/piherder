# PiHerder v1.0.0 — first production release

**Status:** **Planned** (opened 2026-07-26 from post-0.9 operator triage)  
**Date:** 2026-07-26  
**Git tag target:** `v1.0.0`  
**Theme:** Production hardening · security · known-issue burn-down · polish for go-live  
**Baseline:** `v0.9.0` (last pre-production — UX consistency · unit ≥55% · HAOS path 1 · LAN polish)  
**Related:** [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · [PLAN_v0.9.0.md](PLAN_v0.9.0.md) · [FEATURE_PLAN_IAM_2FA_UPDATES_NOTIFICATIONS.md](FEATURE_PLAN_IAM_2FA_UPDATES_NOTIFICATIONS.md) · [FEATURE_PLAN_PIHOLE_NPM_CERTS.md](FEATURE_PLAN_PIHOLE_NPM_CERTS.md) · [FEATURE_PLAN_LAN_NMAP.md](FEATURE_PLAN_LAN_NMAP.md) · [API.md](API.md)

> **First production release.** Freeze product surface for self-hosted home/lab operators: secure defaults, clear auth, documented REST, stable templates schema, and residual UX bugs closed. Deeper features (SSO, insights, full cert wizard, email) stay **post-1.0** unless explicitly pulled in.

---

## 0. Scope principles

1. **Ship confidence** over new product surface.  
2. **Security bar for first prod:** no silent privilege paths; unauthenticated chrome minimal; step-up where recovery secrets are minted.  
3. **Known issues** from 0.9 operator QA either **fix in 1.0** or **document + schedule** (v1.1 / post-1.0).  
4. **Discovery only** for large themes (SSO, custom dashboards, email) — no half-built surfaces in 1.0.

---

## 1. Ship bar (v1.0.0)

| # | Item | Notes |
|---|------|--------|
| 1 | Security hardening pack (AA) | Force-2FA path polished; unauthenticated info leakage closed; backup-code regenerate requires 2FA (**done in 0.9 train**); session/cookie review; password policy defaults; no version string for anonymous footer (**done**) |
| 2 | Auth entry UX (F) | Unauthenticated `/` → login (not empty dashboard + Sign in) |
| 3 | Known-issue burn-down (O, R, T, V, W) | Docker link-out back button; map second-click unlock; brand-on-buttons; Kuma coverage mobile; monitor mute chrome |
| 4 | Mobile / dense tables (U) | NPM certificates list stackable cards on mobile (not wide scroll-only table) |
| 5 | DNS records clarity (X) | Host A vs Pi-hole A vs CNAME checklist — copy + optional deep links; refine Network DNS UX |
| 6 | Docs + wiki freeze | RELEASE_v1.0.0, ADMIN prod checklist, screenshot pack current, API.md aligned |
| 7 | Quality | Maintain unit ≥55%; E2E on touched auth/security and critical shells; REST smoke |

**Stretch (capacity):** discovery refinements (S) if small; cert distribution **discovery notes** only (P).

---

## 2. Known issues → fix in v1.0

Captured from 0.9 operator QA (IDs match triage letters).

| ID | Issue | Direction |
|----|--------|-----------|
| **O** | From Docker, open another tool (e.g. template for a stack); browser **Back** shows “Collecting information from host via SSH” modal and hangs until refresh | Fix nav-loading / bfcache / history: do not re-show blocking SSH wait on `pageshow`/back; cancel or short-circuit when returning to cached page |
| **R** | Hosts map + Path map (desktop): click item to focus; **click again should release** (mobile already does) | Finish toggle unlock on mouse click; avoid re-lock after pointer capture / ghost events |
| **T** | Branded `ph_brand()` (Pi black / Herder red) **inside coloured buttons** (e.g. light mode NPM “pull into PiHerder”) — no space before Pi; Herder invisible on red | **Do not use brand mark inside solid accent/danger buttons**; plain “PiHerder” text or white/ink-safe mark; audit all `btn-*` + `ph_brand()` combinations |
| **V** | Uptime Kuma **coverage** on mobile: service table / status / host columns bleed; should be stacked cards (same pattern as 0.9 path gaps) | Stackable card rows; no dual-width bleed |
| **W** | Monitor columns: **Mute** should read **Mute** with control chrome like **Unmute** (green text / button parity) on infra monitors | Align mute/unmute control styling and labels |
| **U** | NPM **Certificates** on mobile is only horizontally scrollable | Dense stacked cards / `ph-dense-*` pattern |

---

## 3. Security & auth (v1.0)

| ID | Item | Stance |
|----|------|--------|
| **AA** | Further security hardening for first prod | Password policy review; force-2FA; cookie flags; CSRF on sensitive POSTs audit; rate limits; hide version when anonymous (**done**); regenerate backup codes requires password + 2FA (**done**); trusted-device copy |
| **F** | Base URL when logged out → **login**, not empty dashboard | Redirect `/` (and optionally other shells) when `user is None` |
| **B** | Version in footer when not signed in | **Fixed in 0.9 train** — version only when authenticated |
| **E** | Generate backup codes without 2FA | **Fixed in 0.9 train** — modal + password + TOTP/backup code |

**Not v1.0 (backlog):** user self-password-reset (G1), admin OTP reset (G2), email/SMTP (H), SSO/OIDC (Z).

---

## 4. Cert distribution (discovery + partial)

| ID | Item | Stance |
|----|------|--------|
| **P** | Certificate distribution sudoers script incorrect; setup needs wizard-driven flow | **v1.0:** discovery, document broken edges, start UX outline. **Full wizard + sudoers fix may land in v1.1** if capacity slips. See [FEATURE_PLAN_PIHOLE_NPM_CERTS.md](FEATURE_PLAN_PIHOLE_NPM_CERTS.md) |

---

## 5. DNS / Network polish

| ID | Item | Stance |
|----|------|--------|
| **X** | Network DNS Records: host A vs Pi-hole A vs CNAME checklist meaning unclear; want link-through to update DNS | Refine labels, help text, optional deep links to Pi-hole / host DNS modals |

---

## 6. Explicitly out of v1.0 (→ backlog / post-1.0)

| ID | Item | Destination |
|----|------|-------------|
| **G** | Forgot / reset password | Backlog: (1) user self-reset flow (2) admin-triggered OTP reset, clear 2FA, etc. Needs email or out-of-band story |
| **H** | Email integration, notifications channels, password-reset mail | Backlog under consideration (post go-live) |
| **I** | Broader “after go-live” parking lot | ROADMAP |
| **J** | Favourites / shortcuts (e.g. Backups on a host) | Post-1.0 |
| **K** | Cross-host same-feature jump (Docker on host A → same page on host B) | Post-1.0 |
| **L** | Docker quick editor for `.env` / sidecars | **Decision lean no** for quick editor; full editor only; UI note if missing |
| **M** | Templates fleet-wide “which hosts have this template” overview | Post-1.0 / templates plan |
| **N** | Insights, reporting, custom dashboards | **Discovery** first thin slice **post v1.0** |
| **Q** | Onboard service: full git clone/pull; more files than compose + Dockerfile | Post-1.0 templates |
| **S** | Discovery: last seen, offline indicator polish, purge/hide old devices | Consider for 1.0 if small; else **v1.1** |
| **Y** | API management refinement: OpenAPI polish, bearer testing, align with non-OIDC model | Post-1.0 |
| **Z** | SSO / OIDC (social, Authentik, Okta, Auth0); optional IDP groups → roles | Discovery post-1.0 |
| **AB** | Trusted devices: device type, last IP, rename | Post-1.0 |
| **E6 / E9 / E11 full** | Human-readable schedules; hero stats; full templates catalog redesign | Post-1.0 platform (unchanged) |

---

## 7. Workstreams (summary)

| Stream | Focus |
|--------|--------|
| **Sec** | AA, F, residual auth chrome |
| **KI** | O, R, T, U, V, W |
| **Net** | X (+ optional S) |
| **Cert** | P discovery / start |
| **Docs** | RELEASE, ADMIN, wiki freeze, API |

---

## 8. Changelog (planning)

| Date | Note |
|------|------|
| 2026-07-26 | Plan opened from operator triage letters A–AB. Bugs A–E fixed on 0.9 train before 1.0. Known issues and backlog slotted. |

**End of plan** — living document until freeze into `RELEASE_v1.0.0.md`.
