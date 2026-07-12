# 2FA & force 2FA

## Optional per-user 2FA

**Account** → enable TOTP, save **backup codes**, optional **trusted device** (30 days, revocable).

## Force 2FA for all

**Where:** **Settings** → **Security policy**.

| Setting | Effect |
|---------|--------|
| **Force 2FA for all** | Users without TOTP go to `/auth/force-2fa` before the fleet UI. Password change-on-first-login still runs first if required. |

Stored in PostgreSQL (`appsetting`) — travels with DB dumps and self-backup.

## Template step-up

Viewing cleartext template secrets requires a **separate** TOTP unlock (step-up), even if you already completed login 2FA. See [Template secrets](../service-templates/secrets.md).

Optional: **Require 2FA for template deploy & secrets** in Security policy.
