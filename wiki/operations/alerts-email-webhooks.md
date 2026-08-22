# Alerts: policy, inbox, webhooks & email

**Where:** Settings → **Alerts** (`/herder-backups?tab=alerts`) — **admin only**.  
Inbox: **Alerts** in the nav (`/notifications`) — any logged-in role.

PiHerder surfaces alerts **in-app** (Notifications) and optional **Web Push**.  
This page covers **policy** (what fires, how loud) and **outbound** delivery.

| Channel | Cap ID | Role |
|---------|--------|------|
| Webhook | **Wh-lite** | POST JSON to n8n / Signal / Discord / custom |
| SMTP email | **H-lite** | Alert mail + optional password recovery (**G1-lite**) |

Env `WEBHOOK_*` still works as a **fallback** when Settings has no URL (compose operators).

## Alert policy (v1.3)

Settings → Alerts → **Alert policy** is the taxonomy + volume control so map/discovery noise is not as loud as a cert-fail.

<figure class="ph-figure" markdown>
  ![Settings Alerts](../assets/screenshots/settings-alerts.png)
  <figcaption>Settings → Alerts — alert policy card plus webhook / SMTP.</figcaption>
</figure>

| Category | What fires | Default |
|----------|------------|---------|
| **Hosts** | `host_down` — Kuma **SSH** monitor down | critical · debounce 15m · re-alert 24h |
| **Inventory** | `stack_container_down` — Kuma-bound container stopped in Docker inventory | critical · debounce 15m |
| **Kuma services** | `integration_monitor_down` — Kuma **service** monitor down | critical |
| **Map infra** | `map_infra_down` — Network gateway / WAN Kuma chip down | warning |
| **LAN discovery** | `nmap_new_device` (new host) · `nmap_device_offline` (stale / not seen ~14d) | warning / info |
| **PiHerder stack** | `stack_health` — disk / DB / Redis / Celery | fail = critical, warn = warning |
| **Certificates** | expiring · renew/deploy/verify failed | warning / critical |
| **Backups** | host backup failed · herder self-backup failed | critical |
| **Updates** | OS updates · reboot pending · container image updates | warning |
| **Templates** | config drift | warning |

Per category you can **mute**, override **severity** (`default` keeps the table above), set **debounce** (minutes before a dismissed flap re-opens), and **re-alert** (hours; `0` = no channel nudge while still open).

**Severity `default`** means each type keeps its catalog / emitter hint (so stack-health fail vs warn, and cert verify partial vs fail, stay distinct).

Policy is stored in App Settings (herder backup). Demo cannot save it. Changes are audited (`alert_policy_changed`).

The Network map checkbox **Alert when a Kuma-bound container is stopped** writes the same **Inventory** enabled flag (do not fork a second switch).

### Host-down

Herder does **not** ping or SSH-probe hosts for health. Bind an **Uptime Kuma SSH / reachability monitor** to the host (`ssh_reachability`). When that monitor goes down, Alerts opens `host_down` with a Hosts-map focus link.

### LAN discovery

- **New devices:** one inbox row per device (map focus). Webhook / email / push get **one digest per scan** (“N new devices on LAN”) so a first CIDR scan does not POST 40 times.
- **Offline:** nmap last-seen older than the stale window (~14 days). Resolve when the device is seen again.
- **Mute LAN discovery** in Alert policy before the first large scan if you do not want the inbox filled.
- Port add/remove is **not** an alert.

### Debounce and re-alert

Fingerprint upsert still refreshes an **open** row without re-POSTing. After **dismiss**, a still-true condition re-opens only after **debounce**. **Re-alert** re-fires webhook/email/push on a still-open row after N hours (host-down default 24h).

## Alerts inbox

`/notifications` filters: status, **severity**, **category**, **type** (full catalog), server, page size. **Dismiss matching open** uses the current filters; **Dismiss all open** is the whole inbox.

## Webhook

1. Enable **Settings webhook** and set URL.  
2. Optional number / recipients (legacy Signal-style payload).  
3. Optional shared secret → sent as `Authorization: Bearer …` and `X-PiHerder-Webhook-Secret`.  
4. Choose events: **notifications** · **job summaries** · **backup scripts**.  
5. Min severity for notification events (`info` / `warning` / `critical`).  
6. Optional **notification categories** (subset of the policy table). Empty/all = every category.  
7. **Send test**.

Payload shape (stable fields; `type` / `category` added in v1.3 for notification events):

```json
{
  "message": "[warning] Title: body",
  "event": "notification",
  "severity": "warning",
  "type": "host_down",
  "category": "host",
  "number": "",
  "recipients": [],
  "link_url": "/dns/physical?focus=n:host-3#map"
}
```

`event` is one of `notification` | `job` | `backup` | `test`.

## SMTP

1. Enable SMTP · host · port · STARTTLS / SSL / none.  
2. Username / password (password Fernet-encrypted at rest).  
3. From email + display name.  
4. Optional **Alert email** — recipients + min severity + the same category checkboxes.  
5. **Send test email**.  
6. Optional **Allow “Forgot password”** on login (requires SMTP ready).

## Password recovery (G1-lite)

- Visible on sign-in only when SMTP is enabled, host + from set, and “Allow Forgot password” is on.  
- Email always returns a generic success (no account enumeration).  
- Token is random, stored **hashed**, expires in **1 hour**, single use.  
- Reset revokes trusted devices and bumps session version (other browsers signed out).  
- Air-gapped / no SMTP: use admin **Users → reset access** or logged-in change password.

## Related

- [Network maps](../integrations/dns-fabric.md) — host-down and gateway/WAN chips  
- [LAN Discovery](../integrations/lan-discovery.md) — new / offline devices  
- [Metrics & legacy webhooks](metrics-webhooks.md)  
- [API tokens](api-tokens.md)  
- [Settings](settings.md)  
