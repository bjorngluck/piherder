/* PiHerder service worker — PWA install + Web Push (minimal shell cache).
 *
 * CSS/JS must be network-first. Cache-first on themes.css previously hid
 * theme updates until the SW cache was manually cleared.
 */
const CACHE = "piherder-shell-v5";
const SHELL = [
  "/static/favicon.png?v=20260722b",
  "/static/icons/icon-192.png?v=20260722b",
  "/static/manifest.webmanifest",
];

function isVersionedShellAsset(pathname) {
  // Icons / manifest only — safe to cache-first. Never cache CSS/JS first.
  return (
    pathname === "/static/manifest.webmanifest" ||
    pathname.startsWith("/static/icons/") ||
    pathname === "/static/favicon.png" ||
    pathname === "/static/favicon.ico"
  );
}

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// Navigations: network-first. Static CSS/JS: network-first. Icons: cache-first.
// Never put authenticated HTML pages into the shell cache.
self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  if (url.pathname.startsWith("/static/")) {
    if (isVersionedShellAsset(url.pathname)) {
      event.respondWith(
        caches.match(req).then((cached) => {
          const network = fetch(req).then((res) => {
            if (res && res.ok) {
              const clone = res.clone();
              caches.open(CACHE).then((c) => c.put(req, clone));
            }
            return res;
          }).catch(() => cached);
          return cached || network;
        })
      );
      return;
    }

    // CSS, JS, images used by UI — always prefer network so deploys apply
    event.respondWith(
      fetch(req)
        .then((res) => {
          if (res && res.ok && (url.pathname.endsWith(".css") || url.pathname.endsWith(".js"))) {
            // Optional offline fallback only; do not serve stale CSS on success path
            const clone = res.clone();
            caches.open(CACHE).then((c) => c.put(req, clone)).catch(() => {});
          }
          return res;
        })
        .catch(() => caches.match(req))
    );
    return;
  }
});

self.addEventListener("push", (event) => {
  let data = { title: "PiHerder", body: "", url: "/notifications", tag: "piherder" };
  try {
    if (event.data) {
      const parsed = event.data.json();
      // Declarative Web Push (Safari 18.4+ / iOS 18.4+) — also readable by classic SW
      if (parsed && parsed.web_push === 8030 && parsed.notification) {
        const n = parsed.notification;
        data = {
          title: n.title || parsed.title || "PiHerder",
          body: n.body != null ? n.body : (parsed.body || ""),
          url: n.navigate || parsed.url || "/notifications",
          tag: n.tag || parsed.tag || "piherder",
        };
      } else {
        data = Object.assign(data, parsed);
      }
    }
  } catch (e) {
    try {
      data.body = event.data ? event.data.text() : "";
    } catch (_) {}
  }
  // Prefer path-only for same-origin navigation when possible
  let path = data.url || "/notifications";
  try {
    if (path.startsWith("http://") || path.startsWith("https://")) {
      const u = new URL(path);
      if (u.origin === self.location.origin) path = u.pathname + u.search + u.hash;
    }
  } catch (_) {}
  const opts = {
    body: data.body || "",
    tag: data.tag || "piherder",
    renotify: true,
    data: { url: path },
    icon: "/static/icons/icon-192.png?v=20260722b",
    badge: "/static/icons/icon-192.png?v=20260722b",
  };
  event.waitUntil(self.registration.showNotification(data.title || "PiHerder", opts));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const path = (event.notification.data && event.notification.data.url) || "/notifications";
  const target = new URL(path, self.location.origin).href;
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if (client.url && "focus" in client) {
          client.navigate(target);
          return client.focus();
        }
      }
      if (self.clients.openWindow) return self.clients.openWindow(target);
    })
  );
});
