# Nginx Proxy Manager integration

Connect an existing **Nginx Proxy Manager** instance for proxy host inventory (read-only) and TLS certificate pull into PiHerder.

## Connect

1. Catalog → **Integrations** → **+ NPM**
2. Base URL (e.g. `https://nginx.example.com`)
3. Admin **identity** (email) + password
4. Save & connect (PiHerder obtains a short-lived API token per request)

## Proxy hosts (read-only)

- Inventory from `GET /api/nginx/proxy-hosts`
- **Bind** a host to a PiHerder server (optional Docker project/container)
- Create/edit/delete of proxy hosts stays in the NPM UI for this release
- Proxy host **binding** UI is card-based (mobile-friendly selects; host service or Docker cascade)

## Certificates

From the NPM integration detail **Certificates** section:

1. **Pull into PiHerder** — downloads the NPM zip, stores fullchain + private key **encrypted**
2. Manage deploy targets under **Catalog → Certificates** (`/certificates`)

You can also **upload PEM** fullchain + key without NPM: **Catalog → Certificates → Upload PEM**.

### Renew

- NPM-sourced certs with **auto-renew** are checked every 6 hours
- When ≤ **21 days** (configurable) remain: re-pull → if still stale, `POST …/renew` → poll → redistribute to targets
- Manual **Renew (NPM)** on the certificate detail page

### Deploy layouts

| Layout | Files |
|--------|--------|
| pair | `fullchain.pem` + `privkey.pem` |
| combined | single file (privkey then fullchain) |
| pair_and_combined | both |
| pair_and_pfx | pair + OpenSSL PKCS#12 on the host |
| pair_combined_pfx | all three |

Optional owner/group, mode (`600` default), and post-deploy shell command (e.g. service restart).

## Related

- Deploy NPM via [Templates](../service-templates/overview.md)
- Feature plan: repo `docs/FEATURE_PLAN_PIHOLE_NPM_CERTS.md`
