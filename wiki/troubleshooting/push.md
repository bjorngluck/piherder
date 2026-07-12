# Push / PWA

## No push on Android

1. Trusted HTTPS (not self-signed).  
2. `PIHERDER_PUBLIC_URL` matches the origin you open.  
3. Web logs show VAPID ready.  
4. Account → Enable on this device → Send test.  
5. OS/browser notification permission granted.

## No push on iOS

1. iOS **16.4+**.  
2. **Add to Home Screen** and open **from the icon**.  
3. Plain Safari tab will not work.  
4. Trusted cert required.

## Push used to work, then died

- VAPID keys rotated / DB wiped without same keys → re-enable on each device.  
- Browser endpoint expired → Enable again.  
- Subscription still listed but stale — remove and re-add.

## In-app notifications work, push does not

Expected if VAPID missing or insecure origin — fix TLS/VAPID first.
