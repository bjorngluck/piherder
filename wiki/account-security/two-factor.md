# 2FA & force 2FA

## What this is

Optional **TOTP** two-factor authentication for user accounts, plus an admin **force 2FA** policy that requires everyone to enrol before using the fleet UI. Template secrets use a **separate step-up** TOTP even after login 2FA.

## Why it exists

Password-only access to a fleet control plane is risky on shared or exposed URLs. 2FA raises the bar for stolen passwords; force 2FA is for households/teams that want a policy floor. Step-up for secrets limits how long cleartext passwords stay on screen after a unlocked session.

---

## End-to-end: protect the instance

1. As a user: **Account** → enable TOTP → store **backup codes** offline.  
2. Optional trusted device (30 days) if you accept that trade-off.  
3. As admin: **Settings → Security policy → Force 2FA for all**.  
4. Users without TOTP hit `/auth/force-2fa` after password login (password change-on-first still first if required).  
5. For templates, enable **Require 2FA for template deploy & secrets** if operators should not deploy without TOTP.

---

## Optional per-user 2FA

**Account** (`/auth/account`) — profile, password, avatar, enable TOTP, save **backup codes**, optional **trusted device** (30 days, revocable), and push preferences.

## Force 2FA for all

**Where:** **Settings** → **Security policy**.

| Setting | Effect |
|---------|--------|
| **Force 2FA for all** | Users without TOTP go to `/auth/force-2fa` before the fleet UI. Password change-on-first-login still runs first if required. |

Stored in PostgreSQL (`appsetting`) — travels with DB dumps and self-backup.

## Template step-up

Viewing cleartext template secrets requires a **separate** TOTP unlock (step-up), even if you already completed login 2FA. See [Template secrets](../service-templates/secrets.md).

Optional: **Require 2FA for template deploy & secrets** in Security policy.
