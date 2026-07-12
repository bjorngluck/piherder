# Template secrets model

Home-production stance (locked decision):

| Layer | Behaviour |
|-------|-----------|
| **PiHerder** | Source of truth; secrets Fernet-encrypted; edit / audit / redeploy here |
| **UI reveal** | Cleartext only after **View secrets** with **step-up TOTP** (even if you already 2FA’d at login). Unlock cookie ~10 minutes; **Hide secrets** clears it |
| **Host project** | Locked-down **`.env`** (`chmod 600`) for Compose `${VAR}`; offline restarts work **without** PiHerder |
| **Docker page** | Template-managed stacks show a **Template** badge; full compose editor gated |
| **Not default** | Compose `./secrets/` files, Swarm secrets, vault inject — future / advanced |

!!! danger "Master key"
    Restore of encrypted template / deployment secrets requires the **same** `PIHERDER_MASTER_KEY`.

## Self-backup

Herder self-backup includes template catalog rows and stack deployments (ciphertext only).

## Advanced stores

Swarm / vault / sealed host blobs remain **post-0.5 / Horizon 3** exploration — not the default path.
