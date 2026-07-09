# Decision: iOS Push for PiHerder PWA

**Document:** `docs/DECISION_IOS_PUSH.md`  
**Status:** Decided — 2026-07-10  
**Related:** [FEATURE_PLAN_PWA_PUSH_NOTIFICATIONS.md](FEATURE_PLAN_PWA_PUSH_NOTIFICATIONS.md) · [ADMIN.md](ADMIN.md) §6

---

## Question

Should PiHerder invest in iOS-specific push (native app, hybrid wrapper, or extra Web Push work), or is the existing Android-first Web Push path enough for iPhone/iPad users?

## Recommendation (short)

| Choice | Decision |
|--------|----------|
| **Primary path** | **Same Web Push + VAPID stack** as Android — already compatible with iOS Home Screen web apps |
| **Product work now** | **UX polish only** (install steps, Account copy, client guards) + optional **Declarative Web Push** payload for Safari reliability |
| **Native / Capacitor / TWA** | **Do not build** unless real demand after Web Push is validated on iOS |
| **EU special case** | Treat as **same as rest of world** (Apple restored Home Screen web apps after the iOS 17.4 beta scare) |

**Bottom line:** Ship iOS support as “Web Push for installed Home Screen PWAs on iOS 16.4+.” No separate push product; document limits and teach install.

---

## Facts (as of mid‑2026)

### What Apple supports

1. **Web Push on iOS/iPadOS 16.4+** for **web apps saved to the Home Screen** (not for ordinary Safari tabs).
2. Standard **Push API + VAPID** via **Apple Push Notification service** — **no Apple Developer Program membership** required for Web Push.
3. Same origin constraints as elsewhere: **HTTPS**, trusted cert preferred, service worker (for classic Web Push) or Declarative Web Push.
4. **Declarative Web Push** (iOS 18.4+ / Safari 18.5+): browser can show a notification from a standardized JSON payload without depending on service-worker JS for the visible notification. Improves reliability when SW/ITP edge cases would otherwise break classic push. Backwards-compatible if SW still handles the same JSON.

### Hard requirements for a user to get push on iPhone

| Step | Detail |
|------|--------|
| OS | iOS / iPadOS **16.4+** |
| Browser install path | Open PiHerder in **Safari** → Share → **Add to Home Screen** |
| Launch | Open the **Home Screen icon** (standalone), not a Safari tab |
| Permission | Grant notifications when prompted (from the installed web app) |
| Server | VAPID ready; payload always **user-visible** (`userVisibleOnly: true`) |

Chrome/Firefox on iOS are WebKit shells; push is still tied to **Home Screen web apps**, not “Chrome install prompts.”

### Known limitations (accept, don’t “fix” with a wrapper)

| Limitation | Impact on PiHerder |
|------------|-------------------|
| No `beforeinstallprompt` | Must show **manual Share → Add to Home Screen** instructions |
| Not available in Safari tab alone | Account “Enable” must guide users to install first |
| Silent push revoked aggressively | Always send title/body; never empty/background-only payloads |
| 7‑day storage / ITP pressure | Install + occasional open still best practice; Declarative format helps when SW data is cleared |
| No Background Sync / rich hardware APIs | Irrelevant for alert push |
| EU DMA episode (2024) | **Reverted** — Home Screen web apps (incl. push) restored for EU with iOS 17.4 release train |

### Hybrid / native alternatives (rejected for now)

| Option | Cost | Why not now |
|--------|------|-------------|
| Capacitor / Cordova shell | App Store, signing, review, update pipeline | Self-hosted ops tool; App Store friction outweighs benefit |
| Native Swift + APNs | High | Duplicate product; needs Developer Program + distribution model |
| Third-party push SaaS | Ops + data leave host | Conflicts with self-host-first design |

Revisit hybrid **only if** multiple operators report iOS Web Push is unusable in the field after install UX is clear.

---

## How this maps to the current codebase

| Piece | Status |
|-------|--------|
| Manifest + icons + apple-touch-icon | Already present |
| `apple-mobile-web-app-*` meta | Already present |
| Service worker push + click | Already present |
| VAPID + pywebpush | Already present (Android-proven) |
| Soft iOS install banner | Present; improved with explicit Share steps |
| Account copy / iOS guards | Improved in Phase 2 polish |
| Declarative-compatible payload | Implemented (dual-readable: classic SW + `web_push: 8030`) |

**No second subscription store or second send path** is required for iOS.

---

## Decisions

1. **Treat iOS as a first-class client of the same Web Push API**, with documented install requirements.
2. **Do not** build a native or hybrid app in this plan phase.
3. **Do** keep PWA polish cheap: install instructions, standalone detection, clear errors when push APIs are missing.
4. **Do** send **Declarative Web Push–shaped** JSON (plus classic fields) so Safari 18.4+ can fall back without SW, while existing SW code still displays notifications on older clients.
5. **Defer** cert-upload Admin UI (stretch); volume-mounted PEMs remain the ops path.
6. **Success metric:** an iPhone on 16.4+ can Add to Home Screen, enable push on Account, receive **Send test notification**, and receive a real fleet alert — without any Apple developer account.

---

## Operator / user guidance (summary)

**iPhone / iPad**

1. Use Safari on `https://` with a **trusted** certificate (same as Android).
2. Share → **Add to Home Screen**.
3. Open PiHerder from the Home Screen icon.
4. Account → **Enable on this device** → allow notifications.
5. Optional: **Send test notification**.

**If Enable fails:** not installed, iOS &lt; 16.4, permission denied, or VAPID unavailable — UI should say which.

---

## Follow-ups (optional, not blocking)

| Item | Priority |
|------|----------|
| Real-device smoke test (iOS 16.4 / 17 / 18) | High when a device is available |
| Badge count on Home Screen icon | Low |
| Rich actions / images on push | Out of scope |
| Capacitor wrapper | Only on proven Web Push failure + demand |

---

## References

- Apple: [Sending web push notifications in web apps and browsers](https://developer.apple.com/documentation/usernotifications/sending-web-push-notifications-in-web-apps-and-browsers)
- WebKit: [Web Push for web apps on iOS and iPadOS](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/)
- WebKit: [Meet Declarative Web Push](https://webkit.org/blog/16535/meet-declarative-web-push/)
- EU PWA restoration (Mar 2024): Apple restored Home Screen web apps after the iOS 17.4 beta removal plan

---

**End of decision**
