import { api } from "./api.js";

// Die geladene Version steckt im Pfad der Module: /a/<build>/js/version.js
const match = new URL(import.meta.url).pathname.match(/^\/a\/([^/]+)\//);
export const LOADED_BUILD = match ? match[1] : "";

/** Service Worker aktualisieren, alle Caches leeren und die App frisch laden. */
export async function hardReload() {
  try {
    const registration = await navigator.serviceWorker?.getRegistration();
    await registration?.update();
    if (window.caches) {
      const keys = await caches.keys();
      await Promise.all(keys.map((key) => caches.delete(key)));
    }
  } catch {
    // Neu laden klappt auch ohne aufgeräumten Cache.
  }
  location.reload();
}

/** Liefert die Server-Version, wenn sie neuer ist als die geladene, sonst null. */
export async function newerVersion() {
  try {
    const me = await api("/me");
    return me.build && LOADED_BUILD && me.build !== LOADED_BUILD ? me : null;
  } catch {
    return null;
  }
}
