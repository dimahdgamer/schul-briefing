// Service Worker: Push-Empfang, Offline-Cache, Klick auf Benachrichtigungen.

const VERSION = "v1";
const SHELL_CACHE = `shell-${VERSION}`;
const DATA_CACHE = `data-${VERSION}`;
const SHELL = [
  "/",
  "/index.html",
  "/css/app.css",
  "/js/app.js",
  "/js/api.js",
  "/js/ui.js",
  "/js/push.js",
  "/js/views/heute.js",
  "/js/views/woche.js",
  "/js/views/aufgaben.js",
  "/js/views/noten.js",
  "/js/views/post.js",
  "/js/views/verlauf.js",
  "/js/views/einstellungen.js",
  "/js/views/login.js",
  "/icons/sprite.svg",
  "/icons/icon-192.png",
  "/icons/badge-96.png",
  "/fonts/Geist-Variable.woff2",
  "/fonts/GeistMono-Variable.woff2",
  "/fonts/InstrumentSerif-Regular.woff2",
  "/fonts/InstrumentSerif-Italic.woff2",
  "/manifest.webmanifest",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL_CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => ![SHELL_CACHE, DATA_CACHE].includes(k)).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin || url.pathname.startsWith("/cal/")) return;

  // API: erst Netz, bei Ausfall der zuletzt geladene Stand
  if (url.pathname.startsWith("/api/")) {
    event.respondWith(
      fetch(request)
        .then((response) => {
          if (response.ok) {
            const copy = response.clone();
            caches.open(DATA_CACHE).then((cache) => cache.put(request, copy));
          }
          return response;
        })
        .catch(() => caches.match(request).then((cached) => cached || Response.error()))
    );
    return;
  }

  // Schriften und Icons ändern sich nie: direkt aus dem Cache
  if (url.pathname.startsWith("/fonts/") || url.pathname.startsWith("/icons/")) {
    event.respondWith(
      caches.match(request).then((cached) => cached || fetch(request).then((response) => {
        const copy = response.clone();
        if (response.ok) caches.open(SHELL_CACHE).then((cache) => cache.put(request, copy));
        return response;
      }))
    );
    return;
  }

  // App-Code: erst Netz (damit Updates sofort greifen), offline aus dem Cache
  event.respondWith(
    fetch(request)
      .then((response) => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(SHELL_CACHE).then((cache) => cache.put(request, copy));
        }
        return response;
      })
      .catch(() =>
        caches.match(request, { ignoreSearch: true }).then((cached) => cached || caches.match("/index.html"))
      )
  );
});

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = { title: "Schule", body: event.data ? event.data.text() : "" };
  }
  const title = data.title || "Schule";
  const options = {
    body: data.body || "",
    icon: "/icons/icon-192.png",
    badge: "/icons/badge-96.png",
    tag: data.tag || undefined,
    renotify: Boolean(data.tag),
    timestamp: data.ts ? Date.parse(data.ts) : Date.now(),
    data: { url: data.url || "/" },
  };
  event.waitUntil(
    Promise.all([
      self.registration.showNotification(title, options),
      self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
        clients.forEach((client) => client.postMessage({ type: "push" }));
      }),
      self.navigator && "setAppBadge" in self.navigator ? self.navigator.setAppBadge().catch(() => {}) : null,
    ])
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = new URL(event.notification.data?.url || "/", self.location.origin).href;
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if (new URL(client.url).origin === self.location.origin) {
          client.postMessage({ type: "navigate", url: event.notification.data?.url || "/" });
          return client.focus();
        }
      }
      return self.clients.openWindow(target);
    })
  );
});

self.addEventListener("pushsubscriptionchange", (event) => {
  event.waitUntil(
    (async () => {
      const { key } = await fetch("/api/push/key").then((r) => r.json());
      const padding = "=".repeat((4 - (key.length % 4)) % 4);
      const raw = atob((key + padding).replace(/-/g, "+").replace(/_/g, "/"));
      const subscription = await self.registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: Uint8Array.from(raw, (c) => c.charCodeAt(0)),
      });
      await fetch("/api/push/subscribe", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(subscription.toJSON()),
      });
    })()
  );
});
