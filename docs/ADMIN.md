# PiHerder admin guide

Practical reference for operators and admins: roles, users, security policy, schedules, and the Jobs page.

Related design notes: [FEATURE_PLAN_IAM_2FA_UPDATES_NOTIFICATIONS.md](FEATURE_PLAN_IAM_2FA_UPDATES_NOTIFICATIONS.md) · stabilisation: [DECISION_PLAN_STABILISATION.md](DECISION_PLAN_STABILISATION.md)

---

## 1. Roles (RBAC)

Three roles, lowest → highest privilege:

| Role | Read fleet UI | Run backups / patch / Docker / schedules | Manage users |
|------|---------------|------------------------------------------|--------------|
| **viewer** | Yes | No (POST/PUT/PATCH/DELETE blocked except self-service) | No |
| **operator** | Yes | Yes | No |
| **admin** | Yes | Yes | Yes (`/auth/users`) |

### Viewer self-service (allowed writes)

Viewers may still:

- Log out
- Edit their account (profile, password, avatar)
- Manage their own 2FA
- Complete first-login password change and force-2FA onboarding
- Dismiss / interact with **notifications**

They cannot start jobs, change servers, open the Users page, or change Settings security policy.

### How enforcement works

- All logged-in roles can **GET** most pages (read-only browsing).
- Mutating methods (`POST` / `PUT` / `PATCH` / `DELETE`) are checked in auth middleware.
- User admin routes always require **admin**, including GET.
- Legacy users with a missing role are treated as **admin** (same as `normalize_role`).

### Sole admin protection

You cannot demote or delete the **last active admin**. Promote another user first.

---

## 2. User administration

**Where:** avatar menu → **Users** (admin only), or Account → “Manage users & roles”.  
**URL:** `/auth/users`

### Create a user

1. Enter email and role (viewer / operator / admin).
2. Use **Generate** (or set a strong password manually). Strength meter + policy apply.
3. On success, a one-time panel shows login URL, email, temporary password, and copyable invite text — **shown once**.
4. New users have **`must_change_password`** set: they must set their own password before using the fleet.

### Password policy

Enforced on register, account change, and admin create:

- At least **10** characters
- At least one **uppercase**, one **lowercase**, one **digit**
- Max **72** bytes (bcrypt limit)
- Special characters recommended by the strength meter, not hard-required

### Roles and delete

- Change role from the user list (sole-admin rules apply).
- Delete requires explicit confirm; you cannot delete yourself.

### Open registration

Only the **first** account can self-register. After that, only admins create users.

---

## 3. Security policy (force 2FA)

**Where:** **Settings** (`/herder-backups`) → Security policy.

| Setting | Effect |
|---------|--------|
| **Force 2FA for all** | Every user without TOTP is redirected to `/auth/force-2fa` before the fleet UI. Password change-on-first-login still runs first if required. |

Optional 2FA (when not forced): Account → enable TOTP, backup codes, optional trusted device.

---

## 4. Schedules

Configured per server on the **server detail** page. Cron uses **5 fields**: `minute hour day month day_of_week` (APScheduler). Check schedules use the app timezone from Settings; same for apply schedules.

### Update checks (safe — detect only)

| Schedule | Does | Does not |
|----------|------|----------|
| **OS packages (apt)** | Count ready packages, phased count, reboot-pending | Run upgrade |
| **Container images** | Pull/compare image IDs per compose project | `compose up -d` |

Enable checkbox + cron (default suggestion often midnight). Results feed the dashboard, badges, and notifications.

### Patch apply (opt-in — **runs real upgrades**)

Off by default. Requires the matching **feature flag** on the server (OS patch / Container patch in Edit server).

| Option | Behaviour |
|--------|-----------|
| Enable scheduled apply | Registers APScheduler job |
| Only when last check found updates | Skips if last check count is `0` (unknown/`null` still allows run) |
| OS: full-upgrade | Uses `full-upgrade` instead of `upgrade` (with update + autoremove) |
| Cron | e.g. weekly Sunday `30 3 * * 0` |

Also skipped when:

- Feature or apply toggle is off
- A job of the same type is already **pending/running** on that server

Scheduled apply/audit attribution shows as **system / scheduler** (no user id).

### Backups

Per-server backup enable + cron on the server/backups UI. Enqueues **Celery** workers (web never runs rsync).

### PiHerder self-backup

Settings → self-backup schedule (config-only or full). Separate from per-server rsync backups.

---

## 5. Jobs page

**Where:** nav **Jobs** · `/jobs`  
Also: compact **Jobs** panel on each server detail page.

### What is a job?

A row in the job queue for long-running work:

| Type | Typical trigger |
|------|-----------------|
| `backup` | Manual or backup cron → Celery |
| `os_patch` / `container_patch` | Manual or apply schedule → thread pool / UI background task |
| `os_update_check` / `container_update_check` | Manual or check schedule |
| `retention` | Retention cleanup |
| `herder_backup` | PiHerder self-backup |

Statuses: `pending` → `running` → `success` / `failed`.

### Fleet Jobs UI

- Filters: server, status, type, date range, per-page
- **Active only** — pending + running
- Click a row → detail modal (summary, log tail, scheduled flag)
- Link to **Audit log** for historical action trail

### Live progress

While a job runs, server UI modals (JobHold / progress) poll job status and log lines. Container/OS patch streams progress into the job details for the holding modal.

### Jobs vs Audit vs Notifications

| System | Purpose |
|--------|---------|
| **Jobs** | Queue + progress of work units |
| **Audit** | Immutable history of actions (who/what/when, output snippet) |
| **Notifications** | Dismissible inbox (updates pending, failed backup, etc.) |

---

## 6. Quick admin checklist

1. Create operators/viewers from **Users**; share one-time invite.
2. Optionally enable **Force 2FA** under Settings.
3. Per server: enable feature flags → set **check** schedules → only then consider **apply** schedules.
4. Prefer “only if updates” on apply schedules; start with a quiet weekly window.
5. Use **Jobs** + **Audit** when diagnosing stuck or failed work.

---

## 7. Implementation pointers (for developers)

| Concern | Location |
|---------|----------|
| Roles / middleware | `app/security/auth.py` |
| Password policy | `app/services/password_policy.py` |
| User admin routes | `app/routers/auth.py` (`/auth/users`) |
| Scheduler registration | `app/services/scheduler.py` |
| Job create / progress | `app/services/jobs.py` |
| Fleet Jobs page | `app/routers/jobs_page.py`, `app/templates/jobs.html` |
| Unit tests | `tests/test_rbac.py`, `test_scheduler_apply.py`, `test_jobs_progress.py` |
