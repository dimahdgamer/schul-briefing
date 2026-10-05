import { api } from "./api.js";

export function isIos() {
  return /iphone|ipad|ipod/i.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
}

export function isStandalone() {
  return window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;
}

export function pushSupported() {
  return "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
}

function urlBase64ToUint8Array(base64) {
  const padding = "=".repeat((4 - (base64.length % 4)) % 4);
  const raw = atob((base64 + padding).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

export async function currentSubscription() {
  if (!pushSupported()) return null;
  const registration = await navigator.serviceWorker.ready;
  return registration.pushManager.getSubscription();
}

export async function enablePush() {
  if (!pushSupported()) {
    throw new Error(
      isIos() && !isStandalone()
        ? "Auf dem iPhone zuerst über Teilen → „Zum Home-Bildschirm“ installieren und die App von dort öffnen."
        : "Dieser Browser unterstützt keine Push-Benachrichtigungen."
    );
  }
  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    throw new Error("Benachrichtigungen wurden nicht erlaubt. Das lässt sich in den Systemeinstellungen ändern.");
  }
  const registration = await navigator.serviceWorker.ready;
  const { key } = await api("/push/key");
  let subscription = await registration.pushManager.getSubscription();
  if (!subscription) {
    subscription = await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(key),
    });
  }
  await api("/push/subscribe", { method: "POST", body: subscription.toJSON() });
  return subscription;
}

export async function disablePush() {
  const subscription = await currentSubscription();
  if (!subscription) return;
  await api("/push/unsubscribe", { method: "POST", body: { endpoint: subscription.endpoint } }).catch(() => {});
  await subscription.unsubscribe();
}

/** Abo beim Server auffrischen, falls der Browser es erneuert hat. */
export async function syncSubscription() {
  try {
    const subscription = await currentSubscription();
    if (subscription && Notification.permission === "granted") {
      await api("/push/subscribe", { method: "POST", body: subscription.toJSON() });
    }
  } catch {
    // Ohne Netz oder ohne Abo ist hier nichts zu tun.
  }
}
