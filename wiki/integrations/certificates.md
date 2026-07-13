# Managed certificates

PiHerder can store TLS **fullchain + private key** encrypted (Fernet / `PIHERDER_MASTER_KEY`) and deploy them to fleet hosts over SSH.

## Sources

1. **NPM pull** — Integrations → NPM → Certificates → Pull  
2. **PEM upload** — Certificates → Upload PEM (cleartext paste; encrypted immediately; never shown again)

Upload is for operators who issue certs outside NPM (or do not run NPM at all) but still want conversion and distribution.

## Certificate detail

- Domains, issuer, not-before/after, days left, fingerprint  
- Auto-renew (NPM only) and renew-days-before (default 21)  
- Replace PEM material in place  
- Deploy targets: server, remote dir, layout, filenames, mode/owner, post-deploy command  

Private keys are **never** returned to the browser.

## Scheduler

Every **6 hours**: for each NPM-sourced cert with auto-renew and expiry within the window → renew orchestration → deploy all enabled targets. Failures raise in-app notifications (`cert_expiring`, `cert_renew_failed`).

## Herder self-backup

Certificate rows and targets are included. Restore requires the **same** master key.

## Security notes

- Prefer `600` mode and a dedicated remote directory  
- Post-deploy commands are operator-supplied and audited — treat like any remote shell privilege  
- PFX export uses host `openssl pkcs12` (empty or stored export password)
