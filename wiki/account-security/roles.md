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

**Control plane (admin only):** force 2FA, **SSO / OIDC** settings, app timezone, global update-check defaults, PiHerder self-backup run/restore/download/delete/schedule, stack Status, API tokens. Details: [Settings](../operations/settings.md) · [SSO](sso-oidc.md) · [Self-backup](../operations/self-backup.md).

## Viewer self-service (allowed writes)

- Log out  
- Edit account (profile, password, avatar)  
- Manage own 2FA (TOTP / passkeys)  
- Link / unlink own **SSO** identity (when SSO is enabled)  
- First-login password change / force-2FA onboarding  
- Dismiss notifications  
- Own Web Push subscription + prefs  

Viewers cannot start jobs, change servers, open Users, or change security policy.

## End-to-end: add a least-privilege operator

1. As admin, [create a user](users.md) with role **operator**.  
2. Share one-time password carefully; they change password on first login.  
3. Optional: enable [force 2FA](two-factor.md).  
4. Optional: [SSO](sso-oidc.md) so they can sign in with the IdP (auto-link by email or Account → Link).  
5. As operator, run a backup or update check — should work.  
6. Confirm operator cannot open herder restore, API token create, or SSO settings.

Journey: [Operator scenarios — Journey G](../getting-started/operator-scenarios.md#journey-g).

## Enforcement

- Logged-in roles can **GET** most pages (read browsing).  
- Mutating methods checked in auth middleware (viewer fleet writes blocked unless on the self-service allowlist).  
- User admin routes always require **admin**.  
- Instance Settings mutations and herder DR require **admin** (route deps + path prefixes).  
- Missing or unknown role → treated as **viewer** (fail-closed).  
- **SSE / long streams** (Docker logs, build output, backup/OS progress) require a valid session — unauthenticated stream URLs return **401**. Docker **build** stream requires **operator+** (not viewer).

### Authorization matrix

| Surface | Viewer | Operator | Admin |
|---------|--------|----------|-------|
| Fleet UI read | Yes | Yes | Yes |
| Fleet mutate (backup, Docker, DNS maps, certs, …) | **403** | Yes | Yes |
| Docker log SSE | Yes (if signed in) | Yes | Yes |
| Docker **build** SSE | **403** | Yes (POST, named project) | Yes |
| Live host SSH (console / diagnostics / compose build) | **403** | Yes | Yes |
| **Web SSH console** (flag on) | **403** (demo: simulated only) | Yes + 2FA step-up | Yes + 2FA step-up |
| Users / API tokens / herder restore | No | No | Yes |
| Account self-service | Yes | Yes | Yes |
| REST `/api/v1` | Bearer token + scopes (not browser roles) | | |

Anonymous visitors hitting `/` are redirected to **login** (no empty public dashboard).

## Sole admin protection

You cannot demote or delete the **last active admin**. Promote another user first.

## Future: fine-grained roles (v1.3)

v1.2 keeps **three global roles** only (plus OIDC group → those three roles). A later path (**AC-fg**, planned for **v1.3**) may add:

- Per-**host** allowlists (operate only selected machines)  
- Per-**feature** gates (e.g. backups yes, webshell no, certs read-only)  
- Optional custom roles that SSO groups map into  

Not multi-tenant SaaS isolation. Roadmap: [ROADMAP_ECOSYSTEM.md](https://github.com/bjorngluck/piherder/blob/main/docs/ROADMAP_ECOSYSTEM.md).
