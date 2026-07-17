# Roles (RBAC)

## What this is

PiHerder has three **roles** that control what a signed-in user can **change** (and a few admin-only control-plane pages). Most pages remain readable for logged-in users; mutating actions and sensitive settings are role-gated.

## Why it exists

A household or small team often has people who should **look** (viewer), people who should **operate the fleet** (operator), and one or two people who own **users, secrets policy, and DR** (admin). RBAC keeps an accidental click from wiping herder backups or inviting strangers.

---

## Role matrix

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

## End-to-end: add a least-privilege operator

1. As admin, [create a user](users.md) with role **operator**.  
2. Share one-time password carefully; they change password on first login.  
3. Optional: enable [force 2FA](two-factor.md).  
4. As operator, run a backup or update check — should work.  
5. Confirm operator cannot open herder restore or API token create.

Journey: [Operator scenarios — Journey G](../getting-started/operator-scenarios.md#journey-g).

## Enforcement

- Logged-in roles can **GET** most pages (read browsing).  
- Mutating methods checked in auth middleware.  
- User admin routes always require **admin**.  
- Instance Settings mutations and herder DR require **admin** (route deps + path prefixes).  
- Missing or unknown role → treated as **viewer** (fail-closed).

## Sole admin protection

You cannot demote or delete the **last active admin**. Promote another user first.
