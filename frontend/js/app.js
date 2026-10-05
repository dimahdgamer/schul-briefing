import { api, setUnauthorizedHandler } from "./api.js";
import { syncSubscription } from "./push.js";
import { icon } from "./ui.js";
import * as heute from "./views/heute.js";
import * as woche from "./views/woche.js";
import * as aufgaben from "./views/aufgaben.js";
import * as noten from "./views/noten.js";
import * as post from "./views/post.js";
import * as verlauf from "./views/verlauf.js";
import * as einstellungen from "./views/einstellungen.js";
import * as login from "./views/login.js";

const ROUTES = { heute, woche, aufgaben, noten, post, verlauf, einstellungen };

const TABS = [
  ["heute", "Heute", "house"],
  ["woche", "Woche", "calendar-dots"],
  ["aufgaben", "Aufgaben", "notebook"],
  ["noten", "Noten", "chart-line-up"],
  ["post", "Post", "envelope-simple"],
];

const root = document.getElementById("root");
let shellReady = false;
let cleanup = null;
let lastRender = 0;
let renderToken = 0;

function parseHash() {
  const hash = location.hash.replace(/^#\/?/, "");
  const [path, qs] = hash.split("?");
  return { name: path || "heute", params: Object.fromEntries(new URLSearchParams(qs || "")) };
}

export function navigate(hash, { replace = false } = {}) {
  if (replace) history.replaceState(null, "", hash);
  else history.pushState(null, "", hash);
  render();
}

function shell() {
  const sideLinks = TABS.map(
    ([key, label, ic]) => `<a class="side-link" href="#/${key}" data-route="${key}">${icon(ic)}<span>${label}</span></a>`
  ).join("");
  root.innerHTML = `
    <a class="skip-link" href="#main" data-skip>Zum Inhalt springen</a>
    <div class="app">
      <aside class="sidebar" aria-label="Navigation">
        <a class="brand" href="#/heute"><span class="brand-mark">S</span><span class="brand-name">Schule</span></a>
        ${sideLinks}
        <div class="side-sep"></div>
        <a class="side-link" href="#/verlauf" data-route="verlauf">${icon("clock-counter-clockwise")}<span>Verlauf</span><span class="count" data-badge></span></a>
        <a class="side-link" href="#/einstellungen" data-route="einstellungen">${icon("gear-six")}<span>Einstellungen</span></a>
        <div class="side-foot" id="side-foot"></div>
      </aside>
      <div class="content">
        <header class="topbar" id="topbar">
          <a class="brand" href="#/heute"><span class="brand-mark">S</span><span class="brand-name">Schule</span></a>
          <nav class="topbar-actions" aria-label="Weitere">
            <a class="icon-btn" href="#/verlauf" data-route="verlauf" aria-label="Verlauf">${icon("bell-simple")}<span class="badge-dot" data-badge hidden></span></a>
            <a class="icon-btn" href="#/einstellungen" data-route="einstellungen" aria-label="Einstellungen">${icon("gear-six")}</a>
          </nav>
        </header>
        <div id="top-sentinel" style="height:1px;margin-top:-1px"></div>
        <main class="main" id="main" tabindex="-1"></main>
      </div>
      <nav class="tabbar" aria-label="Hauptnavigation">
        ${TABS.map(([key, label, ic]) => `<a class="tab" href="#/${key}" data-route="${key}">${icon(ic, "lg")}<span>${label}</span></a>`).join("")}
      </nav>
    </div>`;

  root.querySelector("[data-skip]").addEventListener("click", (event) => {
    event.preventDefault();
    document.getElementById("main").focus();
  });
  const topbar = document.getElementById("topbar");
  new IntersectionObserver(([entry]) => topbar.classList.toggle("scrolled", !entry.isIntersecting)).observe(
    document.getElementById("top-sentinel")
  );
  shellReady = true;
}

function markActive(name) {
  document.querySelectorAll("[data-route]").forEach((el) => {
    if (el.dataset.route === name) el.setAttribute("aria-current", "page");
    else el.removeAttribute("aria-current");
  });
}

export async function refreshBadge() {
  try {
    const { unseen } = await api("/events?limit=1");
    document.querySelectorAll("[data-badge]").forEach((el) => {
      el.textContent = unseen > 99 ? "99+" : unseen ? String(unseen) : "";
      if (el.classList.contains("badge-dot")) el.hidden = !unseen;
    });
    if ("setAppBadge" in navigator) {
      if (unseen) navigator.setAppBadge(unseen).catch(() => {});
      else navigator.clearAppBadge().catch(() => {});
    }
  } catch {
    // Badge ist nur Kosmetik.
  }
}

async function render() {
  const { name, params } = parseHash();
  const token = ++renderToken;
  if (typeof cleanup === "function") cleanup();
  cleanup = null;

  if (name === "login") {
    shellReady = false;
    document.title = "Anmelden · Schule";
    await login.render(root, params, { navigate });
    return;
  }

  const view = ROUTES[name];
  if (!view) {
    navigate("#/heute", { replace: true });
    return;
  }
  if (!shellReady) shell();
  markActive(name);
  document.title = `${view.title} · Schule`;
  const main = document.getElementById("main");
  lastRender = Date.now();
  const ctx = { navigate, refreshBadge, rerender: render, isCurrent: () => token === renderToken };
  try {
    cleanup = await view.render(main, params, ctx);
  } catch (error) {
    console.error(error);
  }
  if (token !== renderToken && typeof cleanup === "function") cleanup();
}

async function boot() {
  setUnauthorizedHandler(() => {
    if (!location.hash.startsWith("#/login")) navigate("#/login", { replace: true });
  });

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch((err) => console.warn("Service Worker:", err));
    navigator.serviceWorker.addEventListener("message", (event) => {
      if (event.data?.type === "push") {
        refreshBadge();
        if (!location.hash.startsWith("#/login")) render();
      }
      if (event.data?.type === "navigate" && event.data.url) {
        location.hash = event.data.url.replace(/^\/?#?/, "#");
      }
    });
  }

  let me;
  try {
    me = await api("/me");
  } catch {
    me = { authenticated: true }; // offline: den gecachten Stand zeigen
  }
  if (!me.authenticated) {
    navigate("#/login", { replace: true });
  } else {
    if (location.hash.startsWith("#/login") || !location.hash) history.replaceState(null, "", "#/heute");
    await render();
    refreshBadge();
    syncSubscription();
  }

  window.addEventListener("hashchange", render);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && Date.now() - lastRender > 60_000 && !location.hash.startsWith("#/login")) {
      render();
      refreshBadge();
    }
  });
}

boot();
