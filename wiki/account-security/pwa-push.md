# PWA & Web Push

## What this is

PiHerder can be installed as a **Progressive Web App** and send **Web Push** notifications to enrolled devices when new in-app notifications open (and related resolve events).

## Why it exists

You are not always on the desktop when a backup fails or a monitor goes down. Push brings the same events as the bell inbox to a phone — but browsers only allow reliable push under **trusted HTTPS** and (on iOS) a home-screen install.

<figure class="ph-figure" markdown>
  ![Account push](../assets/screenshots/account-push.svg)
  <figcaption>Enable device + event toggles. <span class="ph-wireframe-badge">wireframe</span></figcaption>
</figure>

---

## End-to-end: phone alerts

1. Complete [Trusted HTTPS](../getting-started/https-tls.md) with a stable `PIHERDER_PUBLIC_URL`.  
2. Confirm web logs show VAPID ready after startup.  
3. On Android: open the site / install PWA → Account → **Enable on this device** → test.  
4. On iOS 16.4+: **Add to Home Screen**, open from icon, then enable.  
5. Toggle event types you care about and save.  
6. Trigger a test notification; confirm the bell and the OS banner.

---

## Prerequisites

1. [Trusted HTTPS](../getting-started/https-tls.md) + stable `PIHERDER_PUBLIC_URL`.  
2. Web service running (VAPID auto-generated on startup).

## VAPID (default)

On web startup PiHerder **auto-generates** a VAPID key pair once and stores it in Postgres (`pushvapidconfig`). Private key is **Fernet-encrypted** with `PIHERDER_MASTER_KEY`. No script required for normal use.

Logs: `Web Push VAPID ready (source=generated)` (or `source=env` if overriding).

!!! warning "Do not rotate keys casually"
    Changing the VAPID private key invalidates every device subscription; users must re-enable push.

### Optional env pin

```bash
# Only if you intentionally pin keys after DB wipe
# VAPID_PUBLIC_KEY=...
# VAPID_PRIVATE_KEY=...
# VAPID_CONTACT=mailto:admin@yourdomain.com
```

## Enable on a device

### Android (Chrome)

1. Install PWA if prompted.  
2. **Account → Push notifications → Enable on this device**.  
3. **Send test notification** (your devices only).  
4. Toggle event types and save.

### iPhone / iPad (iOS 16.4+)

1. Safari → Share → **Add to Home Screen**.  
2. Open the **Home Screen icon** (not a plain Safari tab).  
3. Account → **Enable on this device**.  

Push does **not** work from a plain Safari tab.

## When push fires

Only when a **new** open in-app notification is created (not on every fingerprint refresh). Resolve events may also push if preferences match.

## In-app notifications without push

The bell inbox still works if VAPID is unavailable.

## Troubleshooting

[Push / PWA](../troubleshooting/push.md)
