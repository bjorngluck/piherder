# Feature Plan: PWA + Push Notifications (Android First)

**Document:** `docs/FEATURE_PLAN_PWA_PUSH_NOTIFICATIONS.md`  
**Status:** Draft  
**Date:** 2026-07-09  
**Owner:** Bjorn  

---

## Goal

Make PiHerder feel like a proper mobile app by turning it into an installable **Progressive Web App (PWA)**, with a strong focus on **Android** push notifications. iOS support will be improved where easy, but proper push notifications on iOS will be investigated in a later phase.

## Vision

Users should be able to:
- Install PiHerder on their home screen (Android + iOS)
- Receive timely push notifications for important events (failed backups, patch availability, reboot required, etc.)
- Have a good mobile experience without needing a native app

## Phased Approach

### Phase 1: PWA Foundation + Android Push Notifications (Primary Focus)

**Objectives:**
- Make PiHerder installable as a PWA
- Deliver working push notifications on **Android**
- Keep the solution self-hosted friendly

**Key Deliverables:**

| Item | Description | Priority |
|------|-------------|----------|
| Web App Manifest | `manifest.json` with proper icons, name, theme color, and `display: standalone` | High |
| Service Worker | Basic service worker for installability + offline shell caching | High |
| Push Notification Infrastructure | Backend support for sending Web Push notifications (VAPID) | High |
| Android Push | Working push notifications on Android (new backup failed, patch available, etc.) | High |
| Notification Preferences | Per-user settings for which events trigger push | Medium |
| Install Prompt / UX | Gentle prompt or banner for users to install the PWA | Medium |
| Icons & Branding | Proper maskable icons and splash screen assets | Medium |

**Technical Notes (Phase 1):**
- Use **Web Push** (service worker + VAPID keys)
- Store push subscriptions in the database (per user)
- Reuse existing notification system where possible (`/notifications`)
- Keep it optional — users who don’t want push can ignore it

### Phase 2: iOS Improvements + Investigation

**Objectives:**
- Improve the PWA experience on iOS
- Investigate realistic options for iOS push notifications

**Key Deliverables:**

| Item | Description | Priority |
|------|-------------|----------|
| iOS PWA Polish | Better meta tags, icons, and standalone mode behavior | Medium |
| iOS Push Investigation | Research options (Web Push limitations, Capacitor, Firebase, etc.) | Medium |
| Decision Document | Clear recommendation on iOS push approach (or why it’s deprioritised) | Medium |
| Optional: Hybrid wrapper | Evaluate Capacitor or similar if native push becomes necessary | Low |

**Note:** iOS Web Push has significant limitations. A proper solution may require a hybrid approach later.

## Out of Scope (for now)

- Full offline functionality (most actions require remote SSH access)
- Native iOS/Android apps
- Background sync for jobs
- Rich push notifications (images, actions) in Phase 1

## Risks & Considerations

| Risk | Impact | Mitigation |
|------|--------|----------|
| Self-hosted push complexity | High | Make push completely optional. Provide clear setup instructions. |
| iOS limitations | Medium | Be transparent that full push on iOS may require extra work later |
| Service worker complexity | Medium | Keep the service worker as simple as possible initially |
| Icon / asset maintenance | Low | Generate icons once and document how to update them |

## Success Criteria

**Phase 1 Success:**
- PiHerder can be installed on Android home screen and opens in standalone mode
- Android users can receive push notifications for at least 2–3 key events (e.g. backup failure, OS patch available)
- No breaking changes to existing functionality
- Solution remains self-hostable with reasonable setup effort

**Phase 2 Success:**
- Clear understanding of iOS push options and trade-offs
- Improved PWA experience on iOS

## Suggested Implementation Order (Phase 1)

1. Add `manifest.json` and basic icons
2. Add minimal service worker for PWA installability
3. Implement push subscription storage + VAPID key handling
4. Add backend endpoint to send push notifications
5. Wire up key events (backup failure, patch checks)
6. Add user preference controls
7. Polish install experience

---

**End of Feature Plan**