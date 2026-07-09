/* PiHerder service worker — PWA install + Web Push (minimal shell cache). */
const CACHE = "piherder-shell-v1";
const SHELL = [
  "/static/css/themes.css",
  "/static/favicon.png",
  "/static/icons/icon-192.png",
  "/static/manifest.webmanifest",
];

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

// Network-first for navigations; cache-first only for listed static assets.
// Never put authenticated HTML pages into the shell cache.
self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  if (url.pathname.startsWith("/static/")) {
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
  }
});

self.addEventListener("push", (event) => {
  let data = { title: "PiHerder", body: "", url: "/notifications", tag: "piherder" };
  try {
    if (event.data) {
      const parsed = event.data.json();
      data = Object.assign(data, parsed);
    }
  } catch (e) {
    try {
      data.body = event.data ? event.data.text() : "";
    } catch (_) {}
  }
  const opts = {
    body: data.body || "",
    tag: data.tag || "piherder",
    renotify: true,
    data: { url: data.url || "/notifications" },
    icon: "/static/icons/icon-192.png",
    badge: "/static/icons/icon-192.png",
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
