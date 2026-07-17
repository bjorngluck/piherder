# Managed certificates

## What this is

PiHerder stores TLS **fullchain + private key** encrypted (Fernet / `PIHERDER_MASTER_KEY`) and **deploys** them to fleet hosts over SSH via **service maps** (path, layout, permissions, optional restart command).

## Why it exists

One Let’s Encrypt cert often feeds NPM, UniFi, reverse proxies, and app containers. Copying PEMs by hand is error-prone and expires on different schedules. The vault is a single encrypted store; maps describe each consumer; renew/redeploy keeps them aligned.

---

## End-to-end: vault → host files

1. **Get material in** — NPM pull or **Upload PEM**.  
2. Open the cert detail → **Add service map** (host, directory, layout, mode, post-deploy).  
3. **Deploy** that map (or all maps).  
4. Confirm files on the host and that the app reloaded.  
5. Enable auto-renew for NPM-sourced certs if desired.  

---

## Mental model

| Piece | What it is |
|-------|------------|
| **Vault** | One certificate identity in PiHerder (domains, expiry, encrypted PEMs) |
| **Service map** | One consumer of that cert: host + directory + layout/filenames + mode/owner + optional post-deploy command |
| **Deploy** | SSH write files → chmod/chown → run restart command |

Typical flow:

1. **Get material in** — NPM → Certificates → Pull, or **Upload PEM**
2. **Map consumers** — on the cert detail page, add a **service map** per app (NPM volume, UniFi PFX path, Docker bind-mount, …)
3. **Deploy** — per map or “Deploy all maps”; renew/auto-renew re-deploys maps after a successful NPM renew

PiHerder does **not** reconfigure the app’s TLS settings. Point the service at the files you wrote (or the volume that mounts them).

## Where to find it

**Catalog → Certificates** (`/certificates`) — same Catalog tabs as Integrations / Templates / Network.

List shows expiry chips, source (npm / upload), and **service map** count. Certs with **no maps** get an **Add map** shortcut.

## Sources

1. **NPM pull** — Catalog → Integrations → NPM → Certificates → Pull  
2. **PEM upload** — Catalog → Certificates → **Upload PEM** (cleartext paste; encrypted immediately; never shown again)

## Service maps (deploy targets)

Each map answers: *for this service, where and how should the cert land?*

| Field | Purpose |
|-------|---------|
| **Label** | Human name (“NPM custom SSL”, “UniFi”) |
| **Host** | PiHerder server (SSH) |
| **Directory** | Remote path (`~/certs` or absolute, e.g. `/opt/stacks/npm/certs`) |
| **Layout** | Which files to write (see below) |
| **Filenames** | Exact names the app expects |
| **Mode / owner** | `chmod` + optional `chown` |
| **Post-deploy** | Shell command after write (e.g. `docker compose … restart`) |

### Layouts

| Layout | Files written |
|--------|----------------|
| **pair** | `fullchain.pem` + `privkey.pem` (defaults; rename as needed) |
| **combined** | One PEM: private key then fullchain |
| **pair_and_combined** | Both |
| **pair_and_pfx** | Pair + PKCS#12 via host `openssl pkcs12` |
| **pair_combined_pfx** | All three |

Presets in the UI fill common patterns; always adjust path and restart for your stack.

### Example: NPM custom cert on a Docker host

- Label: `NPM custom SSL`
- Directory: `/opt/stacks/npm/certs` (bind-mounted into the container if needed)
- Layout: **pair** → `fullchain.pem`, `privkey.pem`
- Mode: `600`, owner `root:root`
- Post-deploy: `docker compose -f /opt/stacks/npm/docker-compose.yml restart`

Then configure NPM (or the proxy) to use those files.

## Auto-renew

Every **6 hours**: NPM-sourced certs with auto-renew and expiry within the window → renew orchestration → deploy all enabled maps. Failures raise notifications (`cert_expiring`, `cert_renew_failed`).

## Herder self-backup

Certificate rows and maps are included. Restore requires the **same** master key.

## Security notes

- Prefer `600` mode and a dedicated remote directory  
- Post-deploy commands are operator-supplied and audited — treat like any remote shell privilege  
- PFX export uses host `openssl pkcs12` (empty or stored export password)
- Removing a map does **not** delete files already on the host
