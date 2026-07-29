# 2FA & force 2FA

## What this is

Optional **TOTP** two-factor authentication for user accounts, plus an admin **force 2FA** policy that requires everyone to enrol before using the fleet UI. Template secrets use a **separate step-up** TOTP even after login 2FA.

## Why it exists

Password-only access to a fleet control plane is risky on shared or exposed URLs. 2FA raises the bar for stolen passwords; force 2FA is for households/teams that want a policy floor. Step-up for secrets limits how long cleartext passwords stay on screen after a unlocked session.

---

## End-to-end: protect the instance

1. As a user: **Account** → enable TOTP → store **backup codes** offline.  
2. Optional **trusted device** (default **30 days**, from `TRUSTED_DEVICE_DAYS`) if you accept that trade-off — only on machines you control.  
3. As admin: **Settings → Security policy → Force 2FA for all**.  
4. Users without TOTP hit `/auth/force-2fa` after password login (password change-on-first still first if required), then **Set up 2FA on Account** (jumps to the Account 2FA section).  
5. For templates, enable **Require 2FA for template deploy & secrets** if operators should not deploy without TOTP.

---

## Optional per-user 2FA

**Account** (`/auth/account#account-2fa`) — profile, password, avatar, enable TOTP, save **backup codes**, **trusted devices** (revocable), and push preferences.

**Regenerate backup codes** (Account → Two-factor):

1. Click **Generate new codes…**  
2. Confirm modal: **current password** + **authenticator code** (or one unused backup code).  
3. On success, new codes are shown once; old unused codes are invalidated; trusted devices are revoked.

Password alone is **not** enough — this is a deliberate step-up so a stolen session password cannot mint recovery codes.

### Trusted devices

On the 2FA login screen you may **trust this device** for N days (Settings / env default **30**). While trusted, that browser skips the TOTP prompt after password login.

- **Sign out does not clear trust** — you still need the password, but not the authenticator, until the cookie expires or you revoke it.  
- Cookie is **per account** (`trusted_device_{user_id}`) so two logins on one browser do not overwrite each other.  
- Checking “Trust this device” again on an already-trusted browser **refreshes** the same entry instead of adding a duplicate.

**Account → Trusted devices** shows each row with:

| Field | Source |
|-------|--------|
| **Display / friendly name** | Optional rename (e.g. “Work laptop”) — save per row |
| **Device type** | Summarised from the browser user-agent |
| **Last IP** | Last seen client IP for that trust cookie |
| **Last used / expires** | App timezone timestamps |
| **Revoke** | One device or **Revoke all** |

| Risk | Mitigation |
|------|------------|
| Stolen laptop with a trusted cookie | Revoke under **Account → Trusted devices**; password change / **Revoke all** clears trust |
| Shared kiosk | Never enable trust |

Cookies are **HttpOnly**, **SameSite=Lax**, `path=/`, and **Secure** when `PIHERDER_PUBLIC_URL` is `https://…` (or `COOKIE_SECURE=true`).

### Login rate limits

Rough production defaults (in-process; disabled when `PIHERDER_DISABLE_AUTH_RATE_LIMIT` is on for E2E):

| Surface | Limit (approx.) |
|---------|-----------------|
| Password login | 10 attempts / 5 minutes / IP |
| 2FA code | 12 attempts / 5 minutes / IP |
| Registration | 8 attempts / 10 minutes / IP |

## Force 2FA for all

**Where:** **Settings** → **Security policy**.

| Setting | Effect |
|---------|--------|
| **Force 2FA for all** | Users without TOTP go to `/auth/force-2fa` before the fleet UI. Password change-on-first-login still runs first if required. |

Stored in PostgreSQL (`appsetting`) — travels with DB dumps and self-backup.

## Template step-up

Viewing cleartext template secrets requires a **separate** TOTP unlock (step-up), even if you already completed login 2FA. See [Template secrets](../service-templates/secrets.md).

Optional: **Require 2FA for template deploy & secrets** in Security policy.
