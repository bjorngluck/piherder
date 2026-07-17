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
3. **Generate** password (or set manually). Strength meter + policy apply.  
4. After submit, a confirmation modal shows login URL, email, temporary password, and invite text — **shown once**. Copy before closing.  
5. New users have **`must_change_password`** until first reset.

### Password policy

- ≥ **10** characters  
- Upper + lower + digit  
- At most **72 Latin letters/digits** (emoji/symbols count as more)  

Configurable admin policy (custom min length / classes) is **post-RC** — see roadmap.

## Roles and delete

- Change role from the list (sole-admin rules — [Roles](roles.md)).  
- Delete requires confirm; you cannot delete yourself.

## Open registration

Only the **first** account self-registers (becomes **admin**). After that, login points people to
**ask an admin** (Users → Create user).

`ALLOW_OPEN_REGISTRATION=true` re-enables public sign-up if you intentionally want it.
Later self-registered accounts become **operator** (not admin, not viewer). Leave this **off**
in production and create viewers/operators via the Users UI.
