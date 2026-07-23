# From host

## What this is

**From host** pulls an **existing** Docker Compose project off a managed server and turns it into an operator-owned **template** in the catalog (parameterised where possible).

## Why it exists

Most fleets already have stacks that pre-date PiHerder. Re-typing compose by hand loses real ports, mounts, env, and **sidecar configs** (Promtail, agent YAML, etc.). From host is the on-ramp: capture what works, parameterise host-specific names, then redeploy or standardise later.

!!! note "v0.9"
    Relative config mounts and host-name variables (`NODE_NAME`, remote URLs) are part of the **v0.9** train. Operator testing of from-host on real stacks (e.g. `grafana-monitoring`) is in progress.

---

## End-to-end: capture a running stack

1. Host has **Docker** feature and the project appears under Docker inventory.  
2. **Catalog → Templates → From host…**  
3. Pick **server** + **project**.  
4. Optional: move secret-like values into `.env` placeholders during pull.  
5. Wait for the SSH pull (progress / wait modal).  
6. Review in the editor — adjust variable types, checklist, slug, **additional files**.  
7. **Save** → template badge **Yours** (`source=user`).  
8. Optionally [deploy](deploy.md) to another host, or manage this host via a new deployment path after you deliberately cut over.

---

## Steps

1. **Catalog → Templates → From host…**  
2. Pick a Docker-enabled **server** + **project**.  
3. Optional: move secret-like values to `.env` placeholders.  
4. Pull parameterizes **volumes**, **host ports**, **booleans**, env/secrets, and **host-specific labels** into deploy variables; rewrites short mounts/ports to `{{VAR}}`.  
5. **Additional files** referenced as relative binds (e.g. `./promtail-config.yaml:/etc/promtail/config.yml`) are imported and stored with the template.  
6. Review in the editor → **Save**.  
7. Progress overlay / wait modal while SSH pull runs.

## Example: grafana-monitoring + promtail

On a host such as **rpi5-1**, a stack like `grafana-monitoring` often has:

```yaml
# docker-compose.yml (excerpt)
promtail:
  volumes:
    - ./promtail-config.yaml:/etc/promtail/config.yml
```

```yaml
# promtail-config.yaml (excerpt)
scrape_configs:
  - job_name: system-rpi5-1
    static_configs:
      - labels:
          host: rpi5-1
clients:
  - url: http://rpi5-2.hacknow.info:3100/loki/api/v1/push
```

**From host** will:

| Capture | Variable | Deploy meaning |
|---------|----------|----------------|
| `promtail-config.yaml` body | (file kept) | Written next to compose on deploy |
| `rpi5-1` labels / job names | `{{NODE_NAME}}` | Node / host label for this fleet member |
| Loki push URL on another host | `{{LOKI_URL}}` | Remote endpoint — change per environment |
| Ports / data volumes | port & volume vars | Same as other templates |

On deploy to another Pi, set **NODE_NAME** (and **LOKI_URL** if needed) so logs are labelled for that host without hand-editing YAML.

## Tips

- **Missing `.env`:** uses `.env.example` when present, or parameterizes compose only — messages explain what happened.  
- **Override files:** noted in pull messages; primary compose is imported.  
- **Config sidecars:** relative mounts that look like files (`.yml`, `.yaml`, `.json`, `.toml`, `.conf`, …) stay as `./file:target` and are stored under **Additional files** in the editor. Directory binds (`./data`, named volumes) still become volume variables.  
- **Host names:** short hostname from the fleet server (and FQDN when present) are turned into `NODE_NAME` / `HOST_FQDN`; other URLs in configs become dedicated vars (e.g. `LOKI_URL`).  
- **Errors:** invalid project names and missing compose basenames list path + files found.  
- If pull still fails, see [Templates troubleshooting](../troubleshooting/templates-docker.md).  
- After save, you can [deploy](deploy.md) to the same or another host.  

## Related

- [Secrets model](secrets.md) — how secrets are stored after you own the template  
- [Docker overview](../docker/overview.md)  
