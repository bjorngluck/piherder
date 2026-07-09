# Feature Plan: PWA + Push Notifications (Android First)

**Document:** `docs/FEATURE_PLAN_PWA_PUSH_NOTIFICATIONS.md`  
**Status:** Implemented (Phase 1 core) — 2026-07-09  
**Owner:** Bjorn  

**Related:** [ADMIN.md](ADMIN.md) §6 (TLS, hostname, VAPID) · in-app alerts: [FEATURE_PLAN_IAM_2FA_UPDATES_NOTIFICATIONS.md](FEATURE_PLAN_IAM_2FA_UPDATES_NOTIFICATIONS.md)

---

## Goal

Make PiHerder feel like a proper mobile app by turning it into an installable **Progressive Web App (PWA)**, with a strong focus on **Android** push notifications. iOS support is improved where easy; proper iOS push remains a later investigation.

## Vision

Users should be able to:
- Install PiHerder on their home screen (Android + iOS)
- Receive timely push notifications for important events (failed backups, patch availability, reboot required, etc.)
- Have a good mobile experience without needing a native app

## Prerequisites (ops)

| Item | How |
|------|-----|
| Public hostname | `PIHERDER_HOSTNAME` (e.g. `piherder.hacknow.com`) |
| Public URL | `PIHERDER_PUBLIC_URL` (include `:8443` if using compose port map) |
| Trusted TLS | Volume-mount `certs/fullchain.pem` + `certs/privkey.pem` into Caddy |
| VAPID (push only) | `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_CONTACT` |

Self-signed (`Caddyfile.dev`) does **not** give reliable Android Web Push.

---

## Phased Approach

### Phase 1: PWA Foundation + Android Push (shipped)

| Item | Status |
|------|--------|
| Web App Manifest | Done — `/manifest.webmanifest` |
| Service Worker | Done — `/sw.js` (static shell + push) |
| Icons / branding | Done — `app/static/icons/*` |
| Install prompt UX | Done — dismissible banner in `base.html` |
| Push subscription storage | Done — `PushSubscription` model |
| VAPID send path | Done — `pywebpush` + `app/services/push.py` |
| Hook on new notifications | Done — `upsert_notification` create path |
| Per-user preferences | Done — Account card + `PushPreference` |
| Trusted TLS + hostname ops | Done — Caddy + compose + `certs/` |

**Events that can push:** `backup_failed`, `os_updates`, `reboot_pending`, `container_updates`, `herder_backup_failed` (only on **new** open rows; preference-filtered).

### Phase 2: iOS + investigation (not started)

| Item | Priority |
|------|----------|
| iOS PWA polish | Easy meta tags already in base |
| iOS Push investigation | Medium |
| Decision document | Medium |
| Optional hybrid wrapper | Low |
| Admin UI cert upload | Stretch (volume mount is primary) |

---

## Architecture (as implemented)

```
Domain event → notifications.upsert_notification()
                 → _maybe_webhook()
                 → _maybe_push()  # new open rows only
                      → pywebpush to opted-in PushSubscriptions

Browser → SW register → Notification permission → POST /api/push/subscribe
Account → preference form POST /auth/account/push-preferences
```

## Out of Scope (still)

- Full offline SSH workflows
- Native iOS/Android apps
- Background sync for jobs
- Rich push (images, action buttons)
- Automatic Let’s Encrypt as default (operator-supplied PEMs)

## Success Criteria

**Phase 1:**
- [x] Installable PWA assets + standalone display
- [x] Android-oriented Web Push path for key events
- [x] Optional / non-breaking when VAPID unset
- [x] Self-hostable TLS via cert volume + hostname env

**Phase 2:** clear iOS push recommendation (pending research)

---

**End of Feature Plan**
