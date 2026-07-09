# Feature Plan: PWA + Push Notifications (Android First)

**Document:** `docs/FEATURE_PLAN_PWA_PUSH_NOTIFICATIONS.md`  
**Status:** Phase 1 shipped · Phase 2 decided + polish (2026-07-10)  
**Owner:** Bjorn  

**Related:** [ADMIN.md](ADMIN.md) §6 (TLS, hostname, VAPID) · [DECISION_IOS_PUSH.md](DECISION_IOS_PUSH.md) · in-app alerts: [FEATURE_PLAN_IAM_2FA_UPDATES_NOTIFICATIONS.md](FEATURE_PLAN_IAM_2FA_UPDATES_NOTIFICATIONS.md)

---

## Goal

Make PiHerder feel like a proper mobile app by turning it into an installable **Progressive Web App (PWA)**, with a strong focus on **Android** push notifications. iOS uses the **same Web Push stack** once installed to the Home Screen (see decision doc).

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
| VAPID (push) | **Auto-generated at startup** → encrypted in `pushvapidconfig`; optional env override |

Self-signed (`Caddyfile.dev`) does **not** give reliable Android/iOS Web Push.

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
| Auto VAPID keys | Done — generate once at startup; Fernet in DB; env override |
| Hook on new notifications | Done — `upsert_notification` create path |
| Per-user preferences | Done — Account card + `PushPreference` |
| Trusted TLS + hostname ops | Done — Caddy + compose + `certs/` |

**Events that can push:** `backup_failed`, `os_updates`, `reboot_pending`, `container_updates`, `herder_backup_failed` (only on **new** open rows; preference-filtered).

### Phase 2: iOS + investigation (decided + polish)

| Item | Status |
|------|--------|
| iOS Push investigation | **Done** — [DECISION_IOS_PUSH.md](DECISION_IOS_PUSH.md) |
| Decision document | **Done** — same Web Push path; no native/hybrid |
| iOS PWA polish | **Done** — Share → Add to Home Screen banner; Account steps; client guards |
| Declarative-compatible payload | **Done** — `web_push: 8030` + classic SW fields |
| Optional hybrid wrapper | **Deferred** — only if Web Push fails in the field |
| Admin UI cert upload | Stretch (volume mount is primary) |

**iOS user path:** Safari → Share → Add to Home Screen → open icon → Account → Enable (iOS **16.4+**).

---

## Architecture (as implemented)

```
Domain event → notifications.upsert_notification()
                 → _maybe_webhook()
                 → _maybe_push()  # new open rows only
                      → pywebpush to opted-in PushSubscriptions
                         (dual payload: classic + Declarative Web Push shape)

Browser → SW register → Notification permission → POST /api/push/subscribe
Account → preference form POST /auth/account/push-preferences
iOS    → must be Home Screen standalone before PushManager subscribe
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

**Phase 2:**
- [x] Clear iOS push recommendation (Web Push only; no hybrid by default)
- [x] Install + Account UX for iPhone/iPad
- [ ] Real-device smoke test when hardware is available (operator)

---

**End of Feature Plan**
