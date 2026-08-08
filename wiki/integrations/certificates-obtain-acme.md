# Obtain a certificate (ACME / Let’s Encrypt)

## What this is

A **novice-friendly cookbook** for getting a real TLS certificate (fullchain + private key) using **ACME** (usually [Let’s Encrypt](https://letsencrypt.org/getting-started/)), then bringing it into PiHerder’s vault.

PiHerder is **fleet management**: it **stores, maps, and deploys** PEMs. It does **not** (yet) issue ACME certificates inside the app. You still get enormous value by obtaining PEMs with standard tools, then letting PiHerder keep every host in sync.

!!! tip "Not a guru? Start here"
    You do **not** need to invent a CA. Follow one path below, use **staging** until it works, then upload into **Catalog → Certificates**.

---

## Pick a path

| Your situation | Do this |
|----------------|---------|
| You already run **Nginx Proxy Manager** | Prefer [NPM → Pull into PiHerder](npm.md) — NPM already speaks ACME for many DNS providers |
| You already have `fullchain.pem` + `privkey.pem` | [Upload PEM](certificates.md#sources) → [service maps](certificates.md) → deploy |
| No NPM, need a new public cert | Continue on this page (ACME + Certbot) |

**After any path:** [Managed certificates](certificates.md) — vault → maps → deploy.

---

## HTTP-01 vs DNS-01 (plain language)

ACME must prove you control the domain. Two common challenges:

| Challenge | How it works | When it fits |
|-----------|--------------|--------------|
| **HTTP-01** | Let’s Encrypt fetches `http://your-domain/.well-known/acme-challenge/…` on **port 80** | Domain points at a host you control; **80 is reachable** from the internet |
| **DNS-01** | You create a **TXT** record `_acme-challenge.your-domain` | **Wildcard** certs (`*.example.com`); port 80 closed; DNS is easier than opening HTTP |

For many home labs, **DNS-01** is simpler long-term (especially with Cloudflare / similar). **HTTP-01** is fine if the edge already answers on 80.

---

## Recommended: optional helper script (Docker + Certbot)

From a machine with **Docker** (often the herder host or your laptop):

```bash
# Staging first (fake certs — safe for learning, no rate-limit pressure)
./scripts/obtain-acme-cert.sh \
  --email you@example.com \
  --domain example.com \
  --domain www.example.com \
  --staging

# When staging works, real cert (production Let’s Encrypt)
./scripts/obtain-acme-cert.sh \
  --email you@example.com \
  --domain example.com \
  --domain www.example.com
```

**What you get** (default under `./acme-out/<primary-domain>/`):

| File | Use in PiHerder |
|------|-----------------|
| `fullchain.pem` | Upload as full chain |
| `privkey.pem` | Upload as private key |

Then: **Catalog → Certificates → Upload PEM** → add [service maps](certificates.md) → **Deploy**.

### DNS-01 with the helper

```bash
# After you create the TXT record(s) Certbot prints, wait for DNS, continue in the container
./scripts/obtain-acme-cert.sh \
  --email you@example.com \
  --domain example.com \
  --domain '*.example.com' \
  --dns-manual \
  --staging
```

Certbot will pause and show the **exact TXT name and value**. Create that record at your DNS provider (Cloudflare, registrar, Pi-hole is **not** enough for public LE validation unless it is the public authoritative DNS).

### Webroot HTTP-01 with the helper

If something already serves a directory on port 80 for the domain:

```bash
./scripts/obtain-acme-cert.sh \
  --email you@example.com \
  --domain app.example.com \
  --webroot /path/on/host/to/webroot \
  --staging
```

---

## Manual Certbot (without our script)

Use official installers when you prefer bare metal:

- **Instructions by OS / web server:** [certbot.eff.org/instructions](https://certbot.eff.org/instructions)  
- **User guide (including DNS plugins):** [EFF Certbot documentation](https://eff-certbot.readthedocs.io/en/stable/using.html)  

Example **manual DNS** (interactive):

```bash
certbot certonly --manual --preferred-challenges dns \
  -d example.com -d '*.example.com' \
  --email you@example.com --agree-tos
```

Copy live PEMs from Certbot’s output path (often `/etc/letsencrypt/live/<name>/`) into PiHerder **Upload PEM**. Protect `privkey.pem` (`chmod 600`); never paste private keys into chat, tickets, or git.

---

## DNS-01 checklist (human-assisted)

1. Start Certbot (helper or manual) with DNS challenge.  
2. Note the **full** TXT hostname (usually `_acme-challenge.example.com`) and the **value**.  
3. In your **public** DNS panel, create the TXT record.  
4. Wait for propagation (seconds to minutes; check with `dig TXT _acme-challenge.example.com` or an online DNS checker).  
5. Continue Certbot.  
6. Upload PEMs into PiHerder; map and deploy.

!!! warning "Rate limits"
    Let’s Encrypt **production** limits failed/new orders. Always prove the flow with **`--staging`** (or the helper default staging mode) first.  
    Details: [Let’s Encrypt rate limits](https://letsencrypt.org/docs/rate-limits/).

---

## Provider automation (advanced)

If you use Cloudflare, Route53, etc., Certbot has **DNS plugins** that set TXT records for you. That is powerful and closer to how [Nginx Proxy Manager](npm.md) works under the hood (Certbot + plugins) — but each provider has its own credentials and docs.

- Start from the [Certbot DNS plugins section](https://eff-certbot.readthedocs.io/en/stable/using.html#dns-plugins)  
- Prefer **official** `certbot-dns-*` docs for your provider  

PiHerder may offer **ACME-in-app** later (see project roadmap). Until then, automated DNS via Certbot plugins or NPM remains the hands-off path.

---

## Into PiHerder (the value part)

Once you have PEMs:

1. **Catalog → Certificates → Upload PEM** (or NPM pull).  
2. **First-cert setup** or cert detail → **Add service map** per consumer.  
3. Prefer **stage + sudo install** for root-owned paths; use the suggested sudoers.  
4. **Deploy** → confirm files and app reload.  
5. On renew: replace PEMs in the vault (re-upload or NPM renew), then redeploy maps (or auto-renew for NPM-sourced certs).

Full map/deploy detail: [Managed certificates](certificates.md).

---

## Security & hygiene

| Do | Don’t |
|----|--------|
| Use staging until the flow works | Hammer production LE while debugging |
| Keep `privkey.pem` mode `600` | Commit keys or paste them into Issues |
| Prefer vault + maps over scp to five hosts by hand | Leave expired PEMs on only some hosts |
| Document which DNS account owns the zone | Share Cloudflare global keys in compose files in git |

---

## Upstream references (authoritative)

| Topic | Link |
|-------|------|
| Let’s Encrypt getting started | [letsencrypt.org/getting-started](https://letsencrypt.org/getting-started/) |
| Certbot install by environment | [certbot.eff.org/instructions](https://certbot.eff.org/instructions) |
| Certbot user guide | [eff-certbot.readthedocs.io — User Guide](https://eff-certbot.readthedocs.io/en/stable/using.html) |
| Rate limits | [letsencrypt.org/docs/rate-limits](https://letsencrypt.org/docs/rate-limits/) |
| PiHerder vault & deploy | [Managed certificates](certificates.md) |
| NPM pull path | [Nginx Proxy Manager](npm.md) |

---

## Related

- [Managed certificates](certificates.md)  
- [Nginx Proxy Manager](npm.md)  
- [Trusted HTTPS on the herder itself](../getting-started/https-tls.md)  
- Helper script: `scripts/obtain-acme-cert.sh` in the [git repository](https://github.com/bjorngluck/piherder)  
