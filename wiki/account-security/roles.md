# Roles (RBAC)

Three roles, lowest → highest privilege:

| Role | Read fleet UI | Fleet jobs (backup / patch / Docker) | Users | Settings policy / timezone / fleet defaults | Herder self-backup / restore | Status / API tokens |
|------|---------------|--------------------------------------|-------|---------------------------------------------|------------------------------|---------------------|
| **viewer** | Yes | No | No | No | No | No (Settings shows a short notice) |
| **operator** | Yes | Yes | No | No | No | No |
| **admin** | Yes | Yes | Yes | Yes | Yes | Yes |

**Fleet mutate** means starting backups, OS/container patch and checks, Docker compose actions, template deploy, integration binds, cert deploy, bulk servers actions, etc.

**Control plane (admin only):** force 2FA, app timezone, global update-check defaults, PiHerder self-backup run/restore/download/delete/schedule, stack Status, API tokens. Details: [Settings](../operations/settings.md) · [Self-backup](../operations/self-backup.md).

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
- Instance Settings mutations and herder DR require **admin** (route deps + path prefixes).  
- Missing or unknown role → treated as **viewer** (fail-closed).

## Sole admin protection

You cannot demote or delete the **last active admin**. Promote another user first.
