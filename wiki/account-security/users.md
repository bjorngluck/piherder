# Users

**Where:** avatar menu → **Users** (admin only) · `/auth/users`

Each card shows **last login** (app timezone) and a link to that user’s **Audit trail**.

## Create a user

1. Enter email and role (viewer / operator / admin).  
2. **Generate** password (or set manually). Strength meter + policy apply.  
3. One-time panel: login URL, email, temporary password, invite text — **shown once**.  
4. New users have **`must_change_password`** until first reset.

### Password policy

- ≥ **10** characters  
- Upper + lower + digit  
- Max **72** bytes  

## Roles and delete

- Change role from the list (sole-admin rules).  
- Delete requires confirm; you cannot delete yourself.

## Open registration

Only the **first** account self-registers. After that, only admins create users.
