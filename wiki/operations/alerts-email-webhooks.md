# Alerts: webhooks & email

**Where:** Settings → **Alerts** (`/herder-backups?tab=alerts`) — **admin only**.

PiHerder already surfaces alerts **in-app** (Notifications) and optional **Web Push**.  
This page covers **outbound** delivery:

| Channel | Cap ID | Role |
|---------|--------|------|
| Webhook | **Wh-lite** | POST JSON to n8n / Signal / Discord / custom |
| SMTP email | **H-lite** | Alert mail + optional password recovery (**G1-lite**) |

Env `WEBHOOK_*` still works as a **fallback** when Settings has no URL (compose operators).

## Webhook

1. Enable **Settings webhook** and set URL.  
2. Optional number / recipients (legacy Signal-style payload).  
3. Optional shared secret → sent as `Authorization: Bearer …` and `X-PiHerder-Webhook-Secret`.  
4. Choose events: **notifications** · **job summaries** · **backup scripts**.  
5. Min severity for notification events (`info` / `warning` / `critical`).  
6. **Send test**.

Payload shape (stable fields):

```json
{
  "message": "[warning] Title: body",
  "event": "notification",
  "severity": "warning",
  "number": "",
  "recipients": [],
  "link_url": "/notifications"
}
```

`event` is one of `notification` | `job` | `backup` | `test`.

## SMTP

1. Enable SMTP · host · port · STARTTLS / SSL / none.  
2. Username / password (password Fernet-encrypted at rest).  
3. From email + display name.  
4. Optional **Alert email** — recipients + min severity.  
5. **Send test email**.  
6. Optional **Allow “Forgot password”** on login (requires SMTP ready).

## Password recovery (G1-lite)

- Visible on sign-in only when SMTP is enabled, host + from set, and “Allow Forgot password” is on.  
- Email always returns a generic success (no account enumeration).  
- Token is random, stored **hashed**, expires in **1 hour**, single use.  
- Reset revokes trusted devices and bumps session version (other browsers signed out).  
- Air-gapped / no SMTP: use admin **Users → reset access** or logged-in change password.

## Related

- [Metrics & legacy webhooks](metrics-webhooks.md)  
- [API tokens](api-tokens.md)  
- [Settings](settings.md)  
