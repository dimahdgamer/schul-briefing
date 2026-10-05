// Service Worker: Push-Empfang, Offline-Cache, Klick auf Benachrichtigungen.
//
// CSS, JS und Schriften kommen versioniert unter /a/<build>/… und ändern sich nie,
// sie werden deshalb direkt aus dem Cache bedient. Die Startseite und die API
// kommen immer zuerst aus dem Netz, damit Updates und neue Daten sofort ankommen.

const CACHE = "schule-v2";

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.add("/")).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

function buildOf(url) {
  const match = url.pathname.match(/^\/a\/([^/]+)\//);
  return match ? match[1] : null;
}

// Wenn eine neue Version geladen wird, die Dateien älterer Versionen wegräumen
async function pruneOldBuilds(cache, build) {
  const keys = await cache.keys();
  await Promise.all(
    keys.filter((request) => {
      const old = buildOf(new URL(request.url));
      return old && old !== build;
    }).map((request) => cache.delete(request))
  );
}

async function cacheFirst(request, url) {
  const cache = await caches.open(CACHE);
  const cached = await cache.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (response.ok) {
    await cache.put(request, response.clone());
    const build = buildOf(url);
    if (build) pruneOldBuilds(cache, build);
  }
  return response;
}

async function networkFirst(request, fallbackKey) {
  const cache = await caches.open(CACHE);
  try {
    const response = await fetch(request);
    if (response.ok) await cache.put(fallbackKey || request, response.clone());
    return response;
  } catch {
    const cached = await cache.match(fallbackKey || request);
    return cached || Response.error();
  }
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin || url.pathname.startsWith("/cal/")) return;

  if (url.pathname.startsWith("/a/") || url.pathname.startsWith("/fonts/") || url.pathname.startsWith("/icons/")) {
    event.respondWith(cacheFirst(request, url));
  } else if (request.mode === "navigate") {
    event.respondWith(networkFirst(request, "/"));
  } else {
    event.respondWith(networkFirst(request));
  }
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
