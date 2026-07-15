# Users

**Where:** avatar menu → **Users** (admin only) · `/auth/users`

The page uses the shared **ops-hero** (role / 2FA coverage pulse). Each user card shows **last login** (app timezone) and a link to that user’s **Audit trail**.

## Create a user

1. Open **Create user** (header button) — form is a modal.  
2. Enter email and role (viewer / operator / admin).  
3. **Generate** password (or set manually). Strength meter + policy apply.  
4. After submit, a confirmation modal shows login URL, email, temporary password, and invite text — **shown once**. Copy before closing.  
5. New users have **`must_change_password`** until first reset.

### Password policy

- ≥ **10** characters  
- Upper + lower + digit  
- Max **72** bytes  

## Roles and delete

- Change role from the list (sole-admin rules).  
- Delete requires confirm; you cannot delete yourself.

## Open registration

Only the **first** account self-registers. After that, only admins create users.
