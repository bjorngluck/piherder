# Template secrets model

## What this is

How PiHerder stores and reveals **passwords and other secret template variables**, versus what lands on the host for Compose.

## Why it exists

Templates must be redeployable after disk loss **and** operators must not leave secrets in plaintext UI forever. The model keeps PiHerder as source of truth (encrypted), requires **step-up TOTP** to view cleartext, and still writes a locked host `.env` so containers restart without the herder online.

---

## Mental model

Home-production stance (locked decision):

| Layer | Behaviour | Why |
|-------|-----------|-----|
| **PiHerder** | Source of truth; secrets Fernet-encrypted; edit / audit / redeploy here | One place to recover from |
| **UI reveal** | Cleartext only after **View secrets** with **step-up TOTP** (even if you already 2FA’d at login). Unlock cookie ~10 minutes; **Hide secrets** clears it | Login 2FA ≠ “always show secrets” |
| **Host project** | Locked-down **`.env`** (`chmod 600`) for Compose `${VAR}`; offline restarts work **without** PiHerder | Host must boot alone |
| **Docker page** | Template-managed stacks show a **Template** badge; full compose editor gated | Desired state stays authoritative |
| **Not default** | Compose `./secrets/` files, Swarm secrets, vault inject — future / advanced | Keep the RC path simple |

!!! danger "Master key"
    Restore of encrypted template / deployment secrets requires the **same** `PIHERDER_MASTER_KEY`.

## End-to-end: view a secret safely

1. Open the template or deployment that owns the secret.  
2. Ensure your account has TOTP ([2FA](../account-security/two-factor.md)).  
3. **View secrets** → enter TOTP (step-up).  
4. Copy what you need; **Hide secrets** when finished.  
5. Remember Audit may record privileged views depending on action.

## Self-backup

Herder self-backup includes template catalog rows and stack deployments (ciphertext only). See [Self-backup](../operations/self-backup.md).

## Advanced stores

Swarm / vault / sealed host blobs remain **post-0.5 / Horizon 3** exploration — not the default path.
