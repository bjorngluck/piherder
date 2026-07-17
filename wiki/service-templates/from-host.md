# From host

## What this is

**From host** pulls an **existing** Docker Compose project off a managed server and turns it into an operator-owned **template** in the catalog (parameterised where possible).

## Why it exists

Most fleets already have stacks that pre-date PiHerder. Re-typing compose by hand loses real ports, mounts, and env. From host is the on-ramp: capture what works, then redeploy or standardise later.

---

## End-to-end: capture a running stack

1. Host has **Docker** feature and the project appears under Docker inventory.  
2. **Catalog → Templates → From host…**  
3. Pick **server** + **project**.  
4. Optional: move secret-like values into `.env` placeholders during pull.  
5. Wait for the SSH pull (progress / wait modal).  
6. Review in the editor — adjust variable types, checklist, slug.  
7. **Save** → template `source=user`.  
8. Optionally [deploy](deploy.md) to another host, or manage this host via a new deployment path after you deliberately cut over.

---

## Steps

1. **Catalog → Templates → From host…**  
2. Pick a Docker-enabled **server** + **project**.  
3. Optional: move secret-like values to `.env` placeholders.  
4. Pull parameterizes **volumes**, **host ports**, **booleans**, and env/secrets into deploy variables; rewrites short mounts/ports to `{{VAR}}`.  
5. Review in the editor → **Save**.  
6. Progress overlay / wait modal while SSH pull runs.

## Tips

- **Missing `.env`:** uses `.env.example` when present, or parameterizes compose only — messages explain what happened.  
- **Override files:** noted in pull messages; primary compose is imported.  
- **Errors:** invalid project names and missing compose basenames list path + files found.  
- If pull still fails, see [Templates troubleshooting](../troubleshooting/templates-docker.md).  
- After save, you can [deploy](deploy.md) to the same or another host.  

## Related

- [Secrets model](secrets.md) — how secrets are stored after you own the template  
- [Docker overview](../docker/overview.md)  
