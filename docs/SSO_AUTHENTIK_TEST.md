# Test Authentik against PiHerder OIDC (Stream S)

**Status:** draft for local / lab smoke  
**Related:** [FEATURE_PLAN_SSO_OIDC.md](FEATURE_PLAN_SSO_OIDC.md) · operator wiki [SSO / OpenID Connect](../wiki/account-security/sso-oidc.md) · `docker-compose.authentik.yml`

Short path: bring up a **throwaway Authentik**, create an OIDC app, point PiHerder Settings → SSO at it, exercise login / link / 2FA / unlink.

---

## 0. Prerequisites

| Piece | Notes |
|-------|--------|
| PiHerder on `v1.2.0-dev` | Migration **038** applied; web restarted |
| `PIHERDER_PUBLIC_URL` | Exact origin you open in the browser (no trailing slash). Examples: `http://localhost:8000` or `https://piherder.example.com:8443` |
| Callback (fixed) | `{PIHERDER_PUBLIC_URL}/auth/oidc/callback` |
| Browser | Same hostnames as configured (localhost vs LAN IP — be consistent) |

Copy callback from **Settings → General → SSO / OpenID Connect** (shown in UI).

---

## 1. Start test Authentik

```bash
cd /path/to/piherder
cp .env.authentik.example .env.authentik
# set AUTHENTIK_SECRET_KEY (required), tweak bootstrap password if you want
openssl rand -base64 36   # paste into AUTHENTIK_SECRET_KEY=

docker compose -f docker-compose.authentik.yml --env-file .env.authentik up -d
docker compose -f docker-compose.authentik.yml --env-file .env.authentik ps
```

- Admin UI: **http://localhost:9000**
- First login: bootstrap email/password from `.env.authentik` (defaults in example file)

Tear down when done:

```bash
docker compose -f docker-compose.authentik.yml --env-file .env.authentik down -v
```

---

## 2. Authentik: create OIDC provider + application

In Authentik admin (paths are **Applications** / **Providers** — labels move slightly by version):

### 2.1 Property mapping (groups → claim)

1. **Customization → Property Mappings → Create → Scope mapping** (or use built-in **groups** scope if present).  
2. Ensure a scope that emits **`groups`** (list of group names).  
3. Built-in **OpenID email / profile / openid** scopes are enough for email + name.

### 2.2 Groups (for role map smoke)

Create groups e.g.:

| Authentik group | PiHerder role (later) |
|-----------------|------------------------|
| `piherder-admins` | `admin` |
| `piherder-ops` | `operator` |

Add your test user to one of them.

### 2.3 OAuth2/OIDC Provider

**Providers → Create → OAuth2/OpenID Provider**

| Field | Value |
|-------|--------|
| Name | `PiHerder` |
| Authorization flow | default explicit consent (or default authentication flow) |
| Client type | **Confidential** |
| Redirect URIs | **Strict** · `{PIHERDER_PUBLIC_URL}/auth/oidc/callback` |
| Signing key | default authentik self-signed |
| Scopes | `openid` · `email` · `profile` · + groups mapping |
| Subject mode | Based on User UUID (default) is fine |

Save and **copy Client ID + Client Secret**.

### 2.4 Application

**Applications → Create**

| Field | Value |
|-------|--------|
| Name | `PiHerder` |
| **Slug** | `piherder` (this becomes the issuer path segment) |
| Provider | the provider above |
| Launch URL | optional empty / PiHerder URL |

**Issuer URL** (use this in PiHerder):

```text
http://localhost:9000/application/o/piherder/
```

(Trailing slash as Authentik shows on the provider/OpenID Endpoint config page — copy from Authentik’s **OpenID Configuration URL** parent, which is usually  
`http://localhost:9000/application/o/piherder/.well-known/openid-configuration` → issuer = without `/.well-known/...`.)

---

## 3. PiHerder Settings → SSO

**Settings → General → SSO / OpenID Connect**

| Field | Example |
|-------|---------|
| Enable SSO | ✓ |
| Display name | `Authentik` |
| Issuer URL | `http://localhost:9000/application/o/piherder/` |
| Client ID | *(from provider)* |
| Client secret | *(from provider)* |
| Scopes | `openid email profile` (add groups scope name if custom) |
| Role claim path | `groups` |
| Group → role map | **Add mapping…** → group `piherder-admins` → role **admin** → Add; same for `piherder-ops` → **operator** |
| Default role | `viewer` |
| Sync roles on login | ✓ |
| Auto-link by email | ✓ |
| Require email verified | ✓ (Authentik usually sets this) |
| Require SSO | leave **off** for first tests |

Save.

---

## 4. Test matrix (checklist)

Use two browsers or private windows when needed.

| # | Scenario | Expect |
|---|----------|--------|
| 1 | Login page shows **Continue with Authentik** | Button visible when SSO enabled |
| 2 | New user only in Authentik (email not in PiHerder) | JIT user + SSO link; role from map / default |
| 3 | Existing local user, **same email** as Authentik | First SSO login **auto-links**; then 2FA if enrolled |
| 4 | Local user with **TOTP/passkey** | After IdP → PiHerder **2FA step-up** before full session |
| 5 | Account → **Link SSO** (while logged in) | Completes after password/2FA confirm |
| 6 | Account → **Remove password** (linked) | Password login fails; SSO still works (+ 2FA) |
| 7 | Account → **Unlink** without password | Must **set password** in unlink form first |
| 8 | Account → **Unlink** with password | Link gone; password login works |
| 9 | Wrong redirect URI | IdP error; PiHerder `sso_denied` / config error |
| 10 | IdP stopped | SSO fails; local password break-glass still works |

---

## 5. Common pitfalls

| Symptom | Fix |
|---------|-----|
| `redirect_uri` mismatch | Exact match including `http` vs `https` and port (`:8000` / `:8443`) |
| Discovery fails | Issuer must match Authentik OpenID issuer; from PiHerder container, `localhost:9000` is **the PiHerder host’s** localhost only if Authentik is published on the host — usually OK when both are host-published ports |
| Email auto-link skipped | Emails differ, or `email_verified` false and policy strict |
| Groups empty → always viewer | Missing groups scope / mapping; check ID token claims in Authentik |
| Cookie / Secure issues | Align `PIHERDER_PUBLIC_URL` scheme with how you browse |
| Second link to same sub | One `(issuer, sub)` globally; unlink other account first |

**Docker networking note:** browser talks to `localhost:9000` and `localhost:8000`. PiHerder **server** also fetches discovery/token from the **issuer URL** you configured. If PiHerder runs **in Docker** and issuer is `http://localhost:9000`, that is the **web container’s** localhost (wrong). Options:

1. Prefer running Authentik ports on the **host** and set issuer to a host-reachable URL from the PiHerder container, e.g. `http://172.17.0.1:9000/application/o/piherder/` or your LAN IP; **or**  
2. Put Authentik on the same compose network and use `http://server:9000/application/o/piherder/` as issuer **only if** the browser can also reach that hostname (usually needs a reverse proxy / hosts entry).  

**Simplest lab pattern:** browser + PiHerder web on host network ports; issuer = `http://HOST_LAN_IP:9000/application/o/piherder/` so both browser and container resolve the same IdP.

---

## 6. Minimal env reminder (PiHerder)

```bash
# .env (PiHerder)
PIHERDER_PUBLIC_URL=http://localhost:8000   # or https://…:8443
PIHERDER_HOSTNAME=localhost
```

Restart web after changing public URL.

---

## 7. Optional: same-network sketch

If you later merge Authentik into the main compose, attach `server` to PiHerder’s default network and set:

- Browser: reverse-proxy `https://auth.lab.example`  
- Issuer: that public URL (not internal service name)  
- PiHerder callback: public PiHerder URL  

Keep **public issuer + public redirect** — never put Docker-internal hostnames in redirect URIs.

---

## Changelog

| Date | Note |
|------|------|
| 2026-08-08 | Initial draft: compose + Authentik app steps + PiHerder field map + test matrix. |
