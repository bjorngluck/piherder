# Roles (RBAC)

Three roles, lowest → highest privilege:

| Role | Read fleet UI | Run backups / patch / Docker | Manage users |
|------|---------------|------------------------------|--------------|
| **viewer** | Yes | No (mutating HTTP blocked except self-service) | No |
| **operator** | Yes | Yes | No |
| **admin** | Yes | Yes | Yes (`/auth/users`) |

## Viewer self-service (allowed writes)

- Log out  
- Edit account (profile, password, avatar)  
- Manage own 2FA  
- First-login password change / force-2FA onboarding  
- Dismiss notifications  
- Own Web Push subscription + prefs  

Viewers cannot start jobs, change servers, open Users, or change security policy.

## Enforcement

- Logged-in roles can **GET** most pages (read browsing).  
- Mutating methods checked in auth middleware.  
- User admin routes always require **admin**.  
- Missing legacy role → treated as **admin**.

## Sole admin protection

You cannot demote or delete the **last active admin**. Promote another user first.
