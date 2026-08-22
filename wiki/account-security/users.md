# Users

## What this is

The **Users** admin page creates and manages people who can log into this PiHerder instance: email, role, temporary password, last login, and links into their audit trail.

## Why it exists

After the first self-registered admin, open registration closes so the internet cannot mint operators. Admins invite people intentionally, hand them a one-time password, and force a personal password on first login.

**Where:** avatar menu → **Users** (admin only) · `/auth/users`

The page uses the shared **ops-hero** (role / 2FA coverage pulse). Each user card shows **last login** (app timezone) and a link to that user’s **Audit trail**.

---

## End-to-end: invite an operator

1. Open **Users → Create user**.  
2. Email + role **operator** + generate password.  
3. Copy the one-time credentials modal **before** closing.  
4. User logs in → forced password change.  
5. Optional force-2FA path if policy requires it.  
6. Confirm last login and audit rows appear after they act.

---

## Create a user

1. Open **Create user** (header button) — form is a modal.  
2. Enter email and role (viewer / operator / admin).  
3. **Generate** password (or set manually). Strength meter + **needs …** hints follow the live Settings policy (min length, special character, etc.). Same meter on **Reset password**, **Reset access**, Account change, first-login, and self-service forgot-password.  
4. After submit, a confirmation modal shows login URL, email, temporary password, and invite text — **shown once**. Copy before closing.  
5. New users have **`must_change_password`** until first reset.

### Password policy

Default rules (unless an admin changes them):

- ≥ **10** characters  
- Upper + lower + digit  
- At most **72 Latin letters/digits** (emoji/symbols count as more)  

**Available from v1.3:** an admin can change min/max length and required character classes under **Settings → General → Security policy**. The floor is 8 characters; the ceiling is 72. Register, account, and this Create user form all show the same live rules.

## Roles and delete

- Change role from the list (sole-admin rules — [Roles](roles.md)).  
- Delete requires confirm; you cannot delete yourself or the **last admin**.

### What delete removes

| Removed with the account | Kept (unlinked) |
|--------------------------|-----------------|
| Password, profile, avatar files | **Audit** rows (`user_id` cleared) |
| TOTP secret, backup codes, **passkeys**, trusted devices | **Notifications** (`user_id` cleared) |
| Web Push subscriptions + push preferences | **API tokens** the user created (`created_by` cleared; token still works until revoked) |
| **SSO / OIDC** identity links | **Map edges / port notes** they created (`created_by` cleared) |
| Pins / favourites, unused password-reset tokens | |

Delete fails closed if related rows cannot be detached (should not happen on a healthy DB).

## Credential recovery (admin)

When someone loses a password, authenticator, or you need to kick sessions, open the per-user **Recover…** menu on **Users** (no email/SMTP required):

| Action | Effect |
|--------|--------|
| **Reset password** | Sets a **temporary** password (shown once). User must change it on next login (`must_change_password`). Revokes **all sessions** + trusted devices. **Does not** clear 2FA. |
| **Clear 2FA** | Removes TOTP secret, backup codes; revokes sessions + trusted devices. Password unchanged. If [force 2FA](two-factor.md) is on, they re-enrol after login. |
| **Reset access** | Full lockout recovery: temp password **+** clear 2FA **+** kill sessions. Shown once. **Cannot target yourself** (use Reset password / Clear 2FA, or another admin). |
| **Sign out sessions** | Bumps `session_version` so all browser JWTs stop working; revokes trusted devices. Password and 2FA unchanged. Signing out **yourself** returns you to login with a confirmation banner. |

Audit keys: `admin_password_reset`, `admin_2fa_cleared`, `admin_access_reset`, `admin_sessions_revoked`.

### Sole admin locked out (no UI session)

If **no** admin can sign in, use host Docker recovery instead of this page:

→ **[Locked out / sole admin recovery](../troubleshooting/locked-out.md)** (`./scripts/recover-admin.sh` or `python -m app.cli.recover_admin`)

**Forgot password** (email) is available when SMTP is configured under [Settings → Alerts](../operations/alerts-email-webhooks.md). Reset **links use `PIHERDER_PUBLIC_URL` only** (a spoofed `Host` header is ignored). SMTP invite mail on **Create user** is not available — copy the one-time password from the modal.

## Open registration

Only the **first** account self-registers (becomes **admin**). After that, login points people to
**ask an admin** (Users → Create user).

`ALLOW_OPEN_REGISTRATION=true` re-enables public sign-up if you intentionally want it.
Later self-registered accounts become **operator** (not admin, not viewer). Leave this **off**
in production and create viewers/operators via the Users UI.
