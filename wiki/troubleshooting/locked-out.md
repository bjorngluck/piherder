# Locked out / sole admin recovery

## What this is

How to regain access when **no admin can sign in** — forgotten password, lost authenticator, or both — using **host Docker access**. If another admin can still log in, use the UI instead: [Users → Recover…](../account-security/users.md#credential-recovery-admin).

## Why it exists

PiHerder has **no default password**. Email **Forgot password** exists when SMTP is configured; otherwise UI recovery needs a working admin session. Host-side recovery is the out-of-band path for a clean install or sole-admin lockout. **Require SSO** still allows **admin** password login (break-glass); non-admins are blocked.

---

## Choose a path

| Situation | What to use |
|-----------|-------------|
| Another admin can still log in | **Users → Recover…** (no Docker needed) |
| Sole admin forgot password; 2FA still works | Host: **reset-password** |
| Lost password **and** phone / 2FA | Host: **reset-access** (recommended) |
| Force 2FA on and lost all factors | Same — **reset-access** or **clear-2fa**, then enrol again |
| Password known; authenticator lost | Host: **clear-2fa** (or UI if another admin) |
| SSO-only user (password removed); IdP down | Host: **reset-password** (re-enables local password) or **reset-access**; then sign in locally |
| **Require SSO** hid password form; IdP down | **Admin password still works** (break-glass). Non-admins cannot use the password form. Or host recovery + disable **Require SSO**. |
| Kick all browsers only | Host: **sign-out** or UI **Sign out sessions** |
| Want a brand-new first admin (keep fleet data) | Host: **delete-user** on the last account → [Register](../getting-started/first-login.md) |
| Full wipe | Self-backup restore or drop DB volume — [Self-backup](../operations/self-backup.md) |

You need **shell access on the host** that runs `docker compose` (or equivalent) and a running **`web`** container.

!!! tip "SSO tip"
    Prefer keeping at least one **admin with a local password** even when SSO is enabled. See [SSO / OpenID Connect](../account-security/sso-oidc.md#recovery-if-idp-is-down).

---

## Host CLI (preferred)

The image includes a small module that uses the same password hashing and recovery helpers as the Users UI.

### From the compose project root

```bash
# List accounts
./scripts/recover-admin.sh list

# Full lockout recovery (temp password + clear 2FA + revoke sessions)
./scripts/recover-admin.sh reset-access --email you@example.com --generate --yes

# Password only (keep 2FA)
./scripts/recover-admin.sh reset-password --email you@example.com --generate --yes

# Clear 2FA only
./scripts/recover-admin.sh clear-2fa --email you@example.com --yes

# Force logout everywhere
./scripts/recover-admin.sh sign-out --email you@example.com --yes
```

If the helper script is missing (older image) or you prefer an explicit exec:

```bash
docker compose exec -T web python -m app.cli.recover_admin list
docker compose exec -T web python -m app.cli.recover_admin reset-access \
  --email you@example.com --generate --yes
```

### Commands

| Command | Effect |
|---------|--------|
| `list` | Show id, role, active, 2FA, must_change_password, email |
| `reset-password` | Temporary password + `must_change_password` + revoke sessions; **keeps 2FA** |
| `clear-2fa` | Wipe TOTP + backup codes + trusted devices + revoke sessions; password unchanged |
| `reset-access` | Full recovery: temp password **+** clear 2FA **+** revoke sessions |
| `sign-out` | Bump `session_version` + revoke trusted devices only |
| `delete-user` | Remove the user row (if last user, first-admin **Register** re-opens) |

### Flags

| Flag | Meaning |
|------|---------|
| `--email` | Target account (case-insensitive) |
| `--generate` | Create a strong temporary password and print it once |
| `--password …` | Set a specific temporary password (must meet policy) |
| `--yes` | Skip confirmation (**required** for non-interactive `docker compose exec -T`) |

Password policy matches the app: ≥10 characters, upper + lower + digit (see [First login](../getting-started/first-login.md)).

!!! warning "Copy the temporary password immediately"
    It is printed once and stored only as a bcrypt hash. After login you are forced through password change (and force-2FA setup if that policy is enabled).

!!! danger "Host access = full control"
    Anyone who can `docker compose exec` into `web` can reset any account. Protect the host and compose project like production secrets (`SECRET_KEY`, `PIHERDER_MASTER_KEY`).

### After recovery

1. Open the app login URL.  
2. Sign in with email + temporary password.  
3. Set a new personal password when prompted.  
4. Re-enrol 2FA if you cleared it or force-2FA is on.  
5. Optional: create a **second admin** under Users so the next lockout can be fixed in the UI.

Audit trail rows (when available): `host_password_reset`, `host_2fa_cleared`, `host_access_reset`, `host_sessions_revoked`, `host_user_deleted`.

---

## Nuclear: delete last user and re-register

Fleet servers and settings stay; only the account is removed.

```bash
./scripts/recover-admin.sh delete-user --email you@example.com --yes
```

Then open the app → **Create account** / Register as the new first admin ([First login](../getting-started/first-login.md)).

---

## Manual fallback (no module)

If you are on an image built before this CLI shipped, the same helpers work as a one-liner:

```bash
docker compose exec -T web python - <<'PY'
from sqlmodel import Session, select
from app.database import engine
from app.models import User
from app.services.user_admin import admin_reset_password
from app.services.password_policy import generate_password

EMAIL = "you@example.com"
password = generate_password(16)
with Session(engine) as s:
    u = s.exec(select(User).where(User.email == EMAIL)).first()
    if not u:
        raise SystemExit("user not found")
    admin_reset_password(s, u, password, clear_2fa=True)
    s.commit()
print("temp password:", password)
PY
```

Raw SQL is possible but error-prone (bcrypt must match the app). Prefer the CLI or the snippet above.

---

## What you cannot do

- Read back the old password (one-way bcrypt)  
- Recover without host/Docker, a working admin session, or **Forgot password** (SMTP + `PIHERDER_PUBLIC_URL`)  
- Use this CLI without a running stack and `DATABASE_URL` pointing at the real database  

Email **Forgot password** is available when SMTP is configured under Settings → Alerts. Links are built from `PIHERDER_PUBLIC_URL` only.
