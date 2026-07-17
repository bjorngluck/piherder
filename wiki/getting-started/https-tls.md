# Trusted HTTPS & TLS

## What this is

How to put a **trusted certificate** and a **stable public origin** in front of PiHerder (via bundled Caddy or an outer reverse proxy).

## Why it matters

Android **installable PWA** and **Web Push** need a **secure context** with a **trusted certificate** and a stable origin. Self-signed Caddy (`Caddyfile.dev` / `tls internal`) is fine for desktop poking; it is **not** reliable for push on phones. Correct edge TLS also feeds accurate **client IP** into Audit when Caddy is the edge.

---

## Hostname and public URL

In `.env` (compose loads these for **web** and **caddy**):

```bash
PIHERDER_HOSTNAME=piherder.example.com
# Include :8443 when using compose host mapping 8443→443
PIHERDER_PUBLIC_URL=https://piherder.example.com:8443
```

- **DNS:** point `PIHERDER_HOSTNAME` at the host (or your outer reverse proxy).  
- **Ports (default compose):** HTTP `8888→80`, HTTPS `8443→443`.

## Volume-mounted certificates (recommended)

1. Place PEMs in `certs/` (gitignored):

   | File | Role |
   |------|------|
   | `certs/fullchain.pem` | Certificate + chain |
   | `certs/privkey.pem` | Private key |

2. SANs on the cert **must include** `PIHERDER_HOSTNAME`.  
3. Permissions (recommended on the host):

   ```bash
   chmod 600 certs/privkey.pem
   chmod 644 certs/fullchain.pem
   ```

4. Restart Caddy:

   ```bash
   docker compose up -d caddy
   ```

5. Browser should show a **trusted** lock for `PIHERDER_PUBLIC_URL`.

Also see the repo [`certs/README.md`](https://github.com/bjorngluck/piherder/blob/main/certs/README.md).

## Outer reverse proxy (NPM, etc.)

You may terminate TLS at Nginx Proxy Manager (or similar) and reverse-proxy to `web:8000` or to Caddy. Keep:

- A stable public origin in `PIHERDER_PUBLIC_URL`  
- Correct `X-Forwarded-For` / client IP for [API token IP allowlists](../operations/api-tokens.md) **and** Audit trail source IP (PiHerder trusts the first XFF hop / X-Real-IP the same way)  

Bundled Caddy overwrites those headers with the true client — prefer it as the edge for accurate audit IPs.

## Local development without certs

```yaml
# Example: use Caddyfile.dev in compose override
# (see project Caddyfile.dev — self-signed)
```

Push and reliable mobile install need real trusted HTTPS.

## Next

Configure [PWA & Web Push](../account-security/pwa-push.md) after TLS works.
