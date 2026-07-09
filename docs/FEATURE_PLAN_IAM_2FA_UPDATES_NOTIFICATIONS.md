# Feature Plan: IAM, 2FA, Update Schedules & Notifications

> **Status:** Implemented (2026-07)  
> **Date:** 2026-07-08 (design); implemented same week  
> **Related:** [SPEC.md](../SPEC.md) Phase 2–3

## Context

PiHerder is a self-hosted fleet manager (FastAPI + SQLModel + Jinja/HTMX + APScheduler + Celery). Phase 1 is largely complete: backups with cron, OS/container patching (manual), audit log, JWT auth. This plan’s five areas are **shipped** (profile/IAM, optional 2FA, OS/container check schedules, notifications, plus fleet dashboard polish).

This plan covers five related product areas already hinted in `SPEC.md` (Phase 2–3):

1. **IAM / user profile** — name, email, avatar, password change/reset
2. **Optional app-based 2FA** — TOTP + backup codes + careful “trusted device”
3. **Scheduled OS update checks** (check-only, not auto-apply)
4. **Scheduled container update checks** (check-only, not auto-apply)
5. **In-app notification system** — bell, dismiss, deep links; evolves over time

**Why together:** Update checks only become useful when something *surfaces* them (notifications). Profile/2FA harden the console that can reboot and patch hosts. Shared patterns: new DB tables, account UI, scheduler registration, audit actions.

**Out of scope for this plan (keep Phase 3+):** full RBAC roles, multi-tenant org model, email/Slack channel matrix UI, auto-apply patching on schedule (dangerous by default).

---

## Goals & non-goals

| Goal | Non-goal |
|------|----------|
| Users manage own profile on `/auth/account` | Full multi-user admin console (list/delete users) in v1 of this work |
| Optional TOTP 2FA with backup codes | SMS/hardware keys / WebAuthn (later) |
| Cron **check** for OS & container updates | Silent auto-upgrade of OS/containers on schedule |
| Actionable in-app notification inbox | Replacing AuditLog (audit stays historical; notifications are actionable) |
| Persist plan in repo for review | Full product implementation in the docs commit |

---

## Recommended approach

### Design principles

1. **Check ≠ apply.** Scheduled jobs only *detect* and raise notifications. Apply remains explicit UI actions (`run/os_patch`, container patch, reboot).
2. **Notifications ≠ audit.** `AuditLog` = immutable history of what ran. `Notification` = dismissible, linkable inbox items that can auto-resolve when state clears.
3. **Reuse scheduler pattern** from backups (`Server.backup_schedule` + APScheduler job ids + `services/scheduler.py`), but **fix-register** jobs on config save (fix the current backup-schedule gap that needs web restart).
4. **Hybrid storage stays:** profile/2FA/notifications in PostgreSQL; secrets still Fernet / env.
5. **Self-hosted password recovery** without mandatory SMTP: change-password when logged in; 2FA backup codes; optional later “SMTP reset” if mail is configured.

### Phased delivery (PR plan)

```
PR1: Profile IAM (name, email, password, avatar)
  └─ PR2: 2FA TOTP + backup codes (+ optional trusted device)
PR3: OS update check (service + schedule + Server status fields)
  └─ PR4: Container update check (fleet check + schedule + status)
PR5: Notification system (model, raise/dismiss, bell UI, deep links)
  └─ wires PR3/PR4 (and backup failures / reboot) into inbox
```

PR5 can land as a thin skeleton *before* PR3/PR4 if preferred (raise API + empty UI), then PR3/PR4 call `raise_notification()`. Recommended: **PR5 after or in parallel** once check jobs exist so the first real notifications work end-to-end.

Suggested order for first shippable slice: **PR1 → PR5 skeleton → PR3 → PR4 → PR2** (security after core value), or **PR1 → PR2 → PR3 → PR4 → PR5** if hardening login first. **Recommendation: PR1 → PR3 → PR4 → PR5 → PR2** so update visibility ships before 2FA complexity.

---

## 1. IAM / user profile management

### Current state

- `User`: `id`, `email`, `hashed_password`, `is_active`, `created_at` only (`app/models.py`)
- Auth: bcrypt + JWT cookie 7d (`app/security/auth.py`, `app/routers/auth.py`)
- Account page is a stub (`app/templates/account.html`); nav avatar is first letter of email (`base.html`)

### Target UX (`/auth/account`)

| Section | Behaviour |
|---------|-----------|
| Display name | Optional; shown in nav instead of email local-part when set |
| Email | Change requires current password; uniqueness check; update JWT `sub` unchanged (id-based) |
| Avatar | Upload JPEG/PNG/WebP; max ~1–2 MB; store under persistent volume (e.g. `HERDER_BACKUP_ROOT/../data/avatars/{user_id}.webp` or new `DATA_ROOT`); serve via `/auth/avatar` or `/static/uploads/...` with auth; delete → revert to letter avatar |
| Password | Change: current + new + confirm; invalidate optional “all other sessions” later |
| Password reset | See recovery model below |

### Recovery model (self-hosted)

| Scenario | Approach |
|----------|----------|
| Logged in | **Change password** (current required) |
| Forgot password, single admin | Document **recovery**: set password via one-shot env/CLI or restore from herder backup; optional future `piherder set-password` management command |
| Forgot password + SMTP (later) | Token emailed if `SMTP_*` configured — **Phase B**, not blocking |
| 2FA lockout | Backup codes (PR2); disable 2FA only with password + backup code |

Do **not** implement unauthenticated “email reset” without SMTP — it either no-ops or is a security hole.

### Schema changes (`User`)

```text
display_name: Optional[str]
avatar_path: Optional[str]   # relative path under data root
updated_at: Optional[datetime]
# is_active already exists
```

### Routes (extend `app/routers/auth.py`)

| Method | Path | Notes |
|--------|------|-------|
| GET | `/auth/account` | Full form (replace stub) |
| POST | `/auth/account/profile` | name + email |
| POST | `/auth/account/password` | change password |
| POST | `/auth/account/avatar` | multipart upload |
| POST | `/auth/account/avatar/delete` | clear avatar |
| GET | `/auth/avatar/{user_id}` or `/auth/me/avatar` | authenticated image response |

### Audit

- `user_profile_updated`, `user_email_changed`, `user_password_changed`, `user_avatar_updated` (user_id set; no secrets in details).

### Files to touch

| File | Change |
|------|--------|
| `app/models.py` | User fields |
| `app/security/auth.py` | helpers if needed |
| `app/routers/auth.py` | routes |
| `app/templates/account.html` | real forms |
| `app/templates/base.html` | show display_name + avatar image |
| `app/main.py` or `database.py` | column ensure / migration note |
| `app/config.py` | optional `DATA_ROOT` / `AVATAR_MAX_BYTES` |

### Reuse

- `get_password_hash` / `verify_password` / `get_current_user`
- Existing account route + template shell
- Letter-avatar fallback already in `base.html`

---

## 2. Optional app-based 2FA

### Current state

- None. SPEC Phase 3 lists “Optional 2FA”.

### Target behaviour

1. User enables 2FA on Account → server generates TOTP secret (Fernet-encrypted at rest, same pattern as SSH keys).
2. Show **QR** (otpauth URI) + raw secret for manual entry.
3. User confirms with a valid code → `totp_enabled=True`.
4. Login flow: after password OK, if 2FA enabled → second step (code or backup code).
5. Generate **one-time backup codes** (e.g. 8–10); store **hashed** (bcrypt/sha256); show once; allow regenerate (invalidates old).
6. Optional **Trusted device** (see security section).

### Schema

**On `User` (or parallel `UserSecurity` table):**

```text
totp_secret_encrypted: Optional[str]
totp_enabled: bool = False
totp_confirmed_at: Optional[datetime]
```

**`TotpBackupCode` table:**

```text
id, user_id, code_hash, used_at Optional, created_at
```

**`TrustedDevice` table (if implemented):**

```text
id, user_id, token_hash, label Optional, created_at, last_used_at, expires_at, user_agent, ip Optional
```

### Login flow change

```text
POST /auth/login (email+password)
  → if !totp_enabled: issue JWT as today
  → if totp_enabled:
       if trusted_device cookie valid: issue JWT
       else: set short-lived pending_2fa cookie/session (e.g. 5–10 min JWT with claim 2fa_pending)
            redirect /auth/2fa
POST /auth/2fa (code | backup_code, trust_device?)
  → verify → issue full JWT (+ optional trusted device cookie)
```

Pending step must **not** grant full API access.

### Libraries

- `pyotp` for TOTP
- `qrcode` (or pure URL + client QR) for setup UI — prefer server-side PNG or otpauth link for offline-friendly install

### Trusted device — security implications

| Benefit | Risk |
|---------|------|
| Less friction on personal laptop | Stolen laptop = password-only until expiry |
| Common UX expectation | Cookie theft if XSS (mitigate: HttpOnly, Secure, SameSite=Lax/Strict) |

**Recommended defaults if shipped:**

- Opt-in checkbox “Trust this device for 30 days” (not longer than 30–90d)
- Store **only hash** of random token in DB; cookie holds raw token
- Bind lightly to user agent (soft warning, not hard fail — UA changes often)
- Account page: list trusted devices + revoke all / one
- Enabling 2FA does **not** auto-trust current device until user checks the box
- Revoke all trusted devices on password change and on backup-code regenerate
- Document: trusted devices reduce 2FA strength; suitable for home lab, not high-assurance

**Ship decision:** Include trusted devices in the **same PR as 2FA** but behind clear UX copy; if time-boxed, ship TOTP + backup codes first and mark trusted device as stretch in the same design.

### Files

| File | Change |
|------|--------|
| `app/models.py` | security fields + tables |
| `app/security/auth.py` | 2FA helpers, pending token |
| `app/security/encryption.py` | encrypt TOTP secret (reuse Fernet) |
| `app/routers/auth.py` | setup/confirm/disable/2fa challenge |
| `app/templates/login.html`, `account.html`, new `two_factor.html` | UI |
| `pyproject.toml` | pyotp (+ qrcode if needed) |

### Audit

- `user_2fa_enabled`, `user_2fa_disabled`, `user_2fa_backup_regenerated`, `user_trusted_device_revoked`, failed 2FA attempts rate-limit note

### Rate limiting

- Soft rate limit on `/auth/login` and `/auth/2fa` (in-memory or Redis): aligns with SPEC Phase 3 “Rate limiting on auth endpoints”.

---

## 3. Schedule: OS update checks

### Current state (shipped + later OS apply polish)

- Manual apply: `os_patching.run_os_patch` — selectable steps `update` / `upgrade` **XOR** `full-upgrade` / `autoremove` via sudo apt; live log stream + holding modal; post-patch `check_os_updates` before UI reload
- Check-only: `check_os_updates` with **actionable vs Ubuntu phased** counts, reboot flag; schedule + manual check; notifications
- Diagnostics also reads reboot pending
- Feature flag: `Server.os_patch_enabled`; schedule UI gated with `os_check_enabled`
- **Audit (apply):** `os_patch` rows finish with human `details` (`Job #N · summary`), JSON `output_snippet` including `results`, `summary`, optional `post_check`, and `log_tail` (recent apt lines). Stuck “running” rows without output are treated as noise in the audit feed.

### Target behaviour (check job — implemented)

**Check job** (safe, scheduled):

1. SSH to host
2. `sudo apt update` / apt-get equivalent — network required
3. Count packages: list upgradable + sim install for **actionable** vs **phased** (Ubuntu phasing)
4. Read `/var/run/reboot-required` (+ optional pkgs from `/var/run/reboot-required.pkgs`)
5. Persist summary on `Server`
6. Raise/update notification if actionable count > 0 or reboot pending (phased-only is informational, not the same as “must patch”)
7. Audit: `os_update_check` success/failed with snippet

**Apply** remains manual via OS patch UI (and optional future “apply all upgradable” — not this plan’s auto schedule).

### Schema (`Server`)

```text
os_check_schedule: Optional[str]      # cron, like backup_schedule
os_check_enabled: bool = False        # or reuse os_patch_enabled + schedule set
last_os_check_at: Optional[datetime]
os_updates_count: Optional[int]       # 0 = clean, null = never checked
reboot_pending: bool = False          # last known; also refreshed by diagnostics
os_updates_summary: Optional[str]     # short text / JSON package sample
```

**Enablement rule:** Prefer explicit `os_check_enabled` + schedule (mirrors backup), gated by “OS patch feature allowed” if you want one master toggle — recommend: **schedule UI only shown when `os_patch_enabled`**, job runs if schedule set and flag true.

### Scheduler

Mirror backup:

- Job id: `os_check_{server_id}`
- `schedule_os_check_job(server_id)` → enqueue background job (BackgroundTasks or Celery; BackgroundTasks is fine like current OS patch)
- **On config save:** `sync_server_os_check_schedule(server)` (also fix backup re-register in same helper family)
- Startup lifespan: register all enabled schedules

### Service API

New in `app/services/os_patching.py` (or `os_check.py`):

```python
def check_os_updates(server: Server) -> dict:
    # returns {updates_count, reboot_pending, packages_sample[], error?}
```

Do **not** run upgrade in this path.

### UI

- Server detail / backups-style card: “OS update check schedule” cron builder (reuse cron input UX from `server_backups.html` / `server_detail.html`)
- Dashboard / server list badges: “N updates” / “Reboot pending” from persisted fields
- Manual “Check now” button

### Edge cases

| Case | Handling |
|------|----------|
| Non-apt OS | `os_type` field exists; v1 apt-only; skip with clear status for others |
| apt lock / network fail | status failed audit; do not clear previous good counts optionally, or mark stale |
| Passwordless sudo missing | fail with actionable message (same as patch) |

### Files

| File | Change |
|------|--------|
| `app/models.py` | Server fields |
| `app/services/os_patching.py` | `check_os_updates` |
| `app/services/scheduler.py` | register/sync OS check |
| `app/services/jobs.py` | job_type `os_update_check` |
| `app/main.py` | lifespan register |
| `app/routers/servers.py` | config + run check |
| `app/templates/server_detail.html` | schedule UI + badges |
| `app/templates/server_list.html` / dashboard | badges |

---

## 4. Schedule: container update checks

### Current state

- Apply: `container_patching.run_project_update` (pull + conditional `up -d`)
- Per-project check: `docker_management.check_compose_updates` — **already pulls** images (mutates local cache; does not `up -d`)
- Feature flag: `container_patch_enabled`
- **No** fleet-wide scheduled check, **no** persisted summary

### Target behaviour

**Check job** (scheduled, per server):

1. Discover projects (reuse `discover_projects` / compose file detection)
2. Honour `excluded_projects`
3. For each project: compare current image IDs vs remote (see strategy below)
4. Aggregate: projects_with_updates[], failed[]
5. Persist on Server; raise notification if any updates
6. Audit `container_update_check`

### Check strategy (important)

| Approach | Pros | Cons |
|----------|------|------|
| **A. Pull-based** (today’s `check_compose_updates`) | Accurate; matches apply path | Uses bandwidth; changes local image store without restarting containers |
| **B. Registry digest inspect** (`docker manifest` / crane) without pull | Lighter side effects | More fragile offline; registry auth |
| **C. `docker compose pull` only on schedule; never `up`** | Simple reuse | Same as A |

**Recommendation:** **A/C for v1** — reuse pull + ID compare; document that scheduled checks **download** new layers when available but **do not recreate containers**. Add later optional “metadata-only” mode.

Fleet check should **not** call `up -d`. Extract shared “inspect before/after pull” helper from `check_compose_updates` + `run_project_update` to avoid drift.

### Schema (`Server`)

```text
container_check_schedule: Optional[str]
container_check_enabled: bool = False
last_container_check_at: Optional[datetime]
container_updates_count: Optional[int]   # number of projects (or images) with updates
container_updates_summary: Optional[str] # JSON list of project names
```

### Scheduler

- Job id: `container_check_{server_id}`
- Same sync-on-save + lifespan pattern as OS check / backup

### UI

- Server Docker page + server detail: schedule + “Check all projects now”
- Badge: “K projects have image updates”
- Deep link to `/servers/{id}/docker` with filter/highlight if possible

### Staggering

Avoid waking entire fleet at once: default cron suggestions offset by `server_id % 15` minutes in UI help text, or scheduler adds small jitter.

### Files

| File | Change |
|------|--------|
| `app/models.py` | Server fields |
| `app/services/docker_management.py` / `container_patching.py` | fleet `check_all_projects_updates` |
| `app/services/scheduler.py`, `jobs.py`, `main.py` | schedule |
| `app/routers/server_docker.py` / `servers.py` | triggers + config |
| Templates: `docker.html`, `server_detail.html`, list/dashboard | UI |

---

## 5. Notification system

### Current state

- Env webhooks only (`WEBHOOK_*` in config; backup/job finish)
- Flash banners via query params
- Rich **Audit** page — historical, not dismissible inbox

### Product model

**Notifications** = actionable, user-facing alerts.

| Property | Behaviour |
|----------|-----------|
| Raise | Idempotent key: e.g. `(type, server_id, fingerprint)` updates existing open notification instead of spamming |
| Dismiss | User hides; does not fix underlying issue |
| Auto-resolve | When check returns clean (0 updates, reboot cleared, backup succeeds), mark resolved |
| Deep link | `href` to relevant screen |
| Severity | info / warning / critical |
| Read state | optional `read_at` for badge count |

### Schema (`Notification`)

```text
id: int PK
user_id: Optional[int]     # null = broadcast to all users (v1 single-user: all active users)
server_id: Optional[int]
type: str                  # os_updates | container_updates | reboot_pending | backup_failed | ...
severity: str              # info | warning | critical
title: str
body: Optional[str]
link_url: Optional[str]    # e.g. /servers/3 , /servers/3/docker , /servers/3/backups
fingerprint: str           # unique open-item key, indexed
status: str                # open | dismissed | resolved
created_at, updated_at
dismissed_at, resolved_at: Optional
payload: Optional[str]     # JSON extras (counts, project names)
```

Unique partial index idea: one **open** row per `fingerprint` (or upsert by fingerprint when status=open).

### Raise sources (evolution)

| Type | Source | Link |
|------|--------|------|
| `os_updates` | OS check job | `/servers/{id}` OS section |
| `container_updates` | Container check job | `/servers/{id}/docker` |
| `reboot_pending` | OS check / diagnostics / after OS patch | `/servers/{id}` reboot |
| `backup_failed` | backup job finish failed | `/servers/{id}/backups` |
| `herder_backup_failed` | self-backup schedule fail | `/herder-backups` |
| Later | disk full, host offline, 2FA events | … |

Keep a single service:

```python
# app/services/notifications.py
def upsert_notification(..., fingerprint, ...)
def resolve_by_fingerprint(fingerprint)
def dismiss(notification_id, user)
def list_open(user, limit=...)
def unread_count(user)
```

Webhook bridge (optional in same PR): on raise severity≥warning, call existing `_send_webhook` / shared helper so env webhooks stay useful.

### UI

1. **Nav bell** in `base.html` (header-actions, before avatar): badge with open count; dropdown last 5 + “View all”.
2. **Notifications page** `/notifications` — list, filters (type, server, status), dismiss, “dismiss all”.
3. **Not** folded into Audit by default — add cross-link “Related audit” if `payload.audit_id` set. Optional future: audit page filter `action=notification_*` is unnecessary noise.

### Polling

- Badge: lightweight `GET /notifications/count` every N minutes or on navigation (HTMX); keep offline-friendly, no websocket required.

### Files

| File | Change |
|------|--------|
| `app/models.py` | `Notification` |
| `app/services/notifications.py` | **new** |
| `app/routers/notifications.py` | **new** |
| `app/templates/base.html` | bell |
| `app/templates/notifications.html` | **new** |
| `app/main.py` | mount router |
| Wire from `jobs.py`, backup finish, check jobs | raise/resolve |

### Relationship to audit trail

```text
AuditLog  ── immutable “what happened”
Notification ── “what needs attention”
```

Do not overload AuditLog with dismiss state.

---

## Cross-cutting concerns

### Migrations

- Prefer Alembic for new tables/columns (SPEC already wants this). If staying consistent with current runtime `ALTER TABLE` in `main.py`, add ensure-columns helpers for each new field — but document debt.
- Herder self-backup must include new tables/fields when dumping users (verify `herder_backup.py` user export).

### Scheduler sync bugfix (do with PR3/PR4)

Today backup cron is registered at startup only. Introduce:

```python
def sync_server_cron_jobs(scheduler, server: Server):
    # backup + os_check + container_check add/remove
```

Call from backup-config and new schedule endpoints.

### Security summary

| Topic | Stance |
|-------|--------|
| Avatar uploads | Validate content-type + size; strip path traversal; no SVG if XSS risk (or serve with strict Content-Type) |
| 2FA secrets | Fernet with `PIHERDER_MASTER_KEY` |
| Backup codes | Hashed; single use |
| Trusted device | Short TTL, revocable, HttpOnly Secure cookie |
| Registration | Consider lock after first user or env `ALLOW_REGISTER` (small add in PR1) — open register is risky on exposed installs |

### Registration hardening (small, with PR1)

- If ≥1 user exists, disable public register **or** require env `ALLOW_OPEN_REGISTRATION=true`.
- Keeps home-lab first-run simple; stops drive-by account creation.

---

## Critical files (summary)

| Area | Paths |
|------|--------|
| Models | `app/models.py` |
| Auth | `app/security/auth.py`, `app/security/encryption.py`, `app/routers/auth.py` |
| Account UI | `app/templates/account.html`, `base.html`, `login.html` |
| Scheduler | `app/services/scheduler.py`, `app/main.py` lifespan |
| Jobs | `app/services/jobs.py` |
| OS | `app/services/os_patching.py`, `app/routers/servers.py` |
| Containers | `app/services/container_patching.py`, `docker_management.py`, `routers/server_docker.py` |
| Notifications | **new** `services/notifications.py`, `routers/notifications.py` |
| Webhooks | `app/services/backup.py`, `jobs.py`, `config.py` |
| Spec/docs | `SPEC.md` (update phases), **new** `docs/FEATURE_PLAN_IAM_NOTIFICATIONS.md` (this plan committed) |
| Deps | `pyproject.toml` |

---

## Existing utilities to reuse

| Utility | Path |
|---------|------|
| Password hash/verify, JWT, `get_current_user` | `app/security/auth.py` |
| Fernet encrypt/decrypt | `app/security/encryption.py` |
| APScheduler cron pattern + herder sync | `app/services/scheduler.py` |
| Job create + background finish + webhook | `app/services/jobs.py` |
| Reboot detection | `os_patching.py`, `diagnostics.py` |
| Compose update check | `docker_management.check_compose_updates` |
| Project discovery | `container_patching.discover_projects` |
| Audit write helpers | `server_audit.py`, `AuditLog` model |
| Cron UI / validation | backup schedule forms + `herder_backup.validate_cron_expression` |
| Nav single-source items | `base.html` `nav_items` / `secondary_items` |
| Theme tokens | `themes.css` (badge, banner-warning) |

---

## Verification plan

### PR1 Profile

- Register/login still works
- Change display name → appears in nav
- Change email → login with new email
- Change password → old fails, new works
- Avatar upload → shows in nav; delete reverts
- Audit rows created; no plaintext passwords logged
- Open registration locked when user exists (if implemented)

### PR2 2FA

- Enable → QR → confirm → logout → password alone insufficient
- Valid TOTP logs in; invalid rejected
- Backup code works once then fails
- Disable 2FA with password + code
- Trusted device (if shipped): second login skips TOTP until expiry; revoke forces challenge
- Herder backup still restores user row with encrypted secret

### PR3 OS check

- Manual “Check now” updates counts and reboot flag
- Cron registers and fires (or unit-test scheduler add_job with mock)
- Notification/fingerprint when updates > 0; resolves when 0
- Does not run `apt upgrade`

### PR4 Container check

- Manual fleet check lists projects with updates without `up -d`
- Schedule + badges; excluded projects skipped
- Notification deep link opens Docker page

### PR5 Notifications

- Bell count matches open items
- Dismiss hides from badge; “View all” page filters work
- Click navigates to `link_url`
- Backup failure raises notification (optional wire)
- Webhook still fires if configured (regression)

### Regression

- Existing backup schedule, OS patch apply, container patch apply, audit filters, herder backup

---

## Implementation status

All five product areas from this plan are implemented in tree. Companion docs:

1. This file under `docs/` (design + acceptance criteria retained as reference)
2. `SPEC.md` Phase 2–3 checkboxes marked complete where applicable
3. `README.md` feature list and onboarding notes

**Still out of scope (as planned):** full RBAC, multi-tenant orgs, email/Slack channel matrix UI, scheduled auto-apply of patches.

Optional next: open GitHub issues / Project cards from remaining unchecked SPEC Phase 2–4 items.

---

## Open decisions (defaults chosen)

| Decision | Default in this plan |
|----------|----------------------|
| Password reset without SMTP | Change-password + ops recovery; no email token v1 |
| Trusted devices | Include design; implement with 2FA, 30d max, revocable |
| Auto-apply on schedule | **No** — check-only |
| Container check method | Pull + compare IDs (no `up -d`) |
| Notifications vs audit | Separate table + bell UI |
| Registration | Lock after first user |
| PR order | Profile → OS check → container check → notifications → 2FA |

If any default should flip before implementation, adjust in review comments on the committed plan.

---

## Implementation notes for the executor

1. Keep modules small (match recent split: routers + services).
2. Prefer form POST + redirect + HTMX fragments consistent with existing UI.
3. Every privileged change: AuditLog.
4. Scheduled jobs: never run long SSH work inside APScheduler thread — enqueue `jobs` / BackgroundTasks / Celery like backups.
5. Update herder backup include list for new models.
6. Do not force-push; docs commit on `main` or a `docs/feature-plan-…` branch per user preference (ask if branch strategy unclear).
