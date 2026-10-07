// Kleine Helfer für Markup, Datumsformate und Rückmeldungen.

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

export function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ESC[c]);
}

// Relativ zum Modul auflösen, damit die Icons dieselbe Version wie der Code haben
const SPRITE = new URL("../icons/sprite.svg", import.meta.url).pathname;

export function icon(name, cls = "") {
  return `<svg class="icon ${cls}" aria-hidden="true"><use href="${SPRITE}#i-${name}"></use></svg>`;
}

// Das Logo: ein S aus Stundenplan-Kacheln, die mittlere ist die aktuelle Stunde. Quelle: icons/logo.svg
const LOGO_CELLS = [[0, 0], [1, 0], [2, 0], [0, 1], [0, 2], [1, 2], [2, 2], [2, 3], [0, 4], [1, 4], [2, 4]];

export function logo(size = 30) {
  const cells = LOGO_CELLS.map(([c, r]) => {
    const hit = c === 1 && r === 2;
    return `<rect class="${hit ? "hit" : "cell"}" x="${137 + c * 84}" y="${98 + r * 66}" width="70" height="52" rx="16"/>`;
  }).join("");
  return `<svg class="logo" viewBox="0 0 512 512" width="${size}" height="${size}" aria-hidden="true"><rect class="bg" width="512" height="512" rx="116"/>${cells}</svg>`;
}

// Passwortfeld mit Auge-Knopf zum Anzeigen. Danach wirePasswordToggles(root) aufrufen.
export function passwordField({ id, placeholder = "", autocomplete = "current-password", name = "password" }) {
  return `
    <div class="pw">
      <input class="input" id="${esc(id)}" name="${esc(name)}" type="password" autocomplete="${esc(autocomplete)}" autocapitalize="none" autocorrect="off" spellcheck="false"${placeholder ? ` placeholder="${esc(placeholder)}"` : ""} required />
      <button type="button" class="pw-toggle" data-pw-toggle aria-pressed="false" aria-label="Passwort anzeigen" aria-controls="${esc(id)}">${icon("eye")}${icon("eye-slash")}</button>
    </div>`;
}

export function wirePasswordToggles(root) {
  root.querySelectorAll("[data-pw-toggle]").forEach((button) => {
    const input = root.querySelector(`#${button.getAttribute("aria-controls")}`);
    // Der Knopf soll das Feld nicht verlassen lassen, sonst klappt auf dem Handy die Tastatur zu
    button.addEventListener("mousedown", (event) => event.preventDefault());
    button.addEventListener("click", () => {
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      button.setAttribute("aria-pressed", String(show));
      button.setAttribute("aria-label", show ? "Passwort verbergen" : "Passwort anzeigen");
      input.focus({ preventScroll: true });
    });
  });
}

export const WEEKDAYS = ["Sonntag", "Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag"];
export const WEEKDAYS_SHORT = ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"];
export const MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"];

export function parseDate(iso) {
  const [y, m, d] = String(iso).slice(0, 10).split("-").map(Number);
  return new Date(y, m - 1, d);
}

export function isoDate(date) {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

export function addDays(iso, days) {
  const d = parseDate(iso);
  d.setDate(d.getDate() + days);
  return isoDate(d);
}

export function daysBetween(fromIso, toIso) {
  return Math.round((parseDate(toIso) - parseDate(fromIso)) / 86400000);
}

export function longDate(iso) {
  const d = parseDate(iso);
  return `${WEEKDAYS[d.getDay()]}, ${d.getDate()}. ${MONTHS[d.getMonth()]}`;
}

export function shortDate(iso) {
  const d = parseDate(iso);
  return `${WEEKDAYS_SHORT[d.getDay()]} ${String(d.getDate()).padStart(2, "0")}.${String(d.getMonth() + 1).padStart(2, "0")}.`;
}

export function relativeDay(iso, todayIso) {
  const diff = daysBetween(todayIso, iso);
  if (diff === 0) return "Heute";
  if (diff === 1) return "Morgen";
  if (diff === -1) return "Gestern";
  if (diff === 2) return "Übermorgen";
  if (diff > 2 && diff < 7) return WEEKDAYS[parseDate(iso).getDay()];
  return shortDate(iso);
}

export function inDays(iso, todayIso) {
  const diff = daysBetween(todayIso, iso);
  if (diff === 0) return "heute";
  if (diff === 1) return "morgen";
  if (diff < 0) return `vor ${-diff} Tagen`;
  return `in ${diff} Tagen`;
}

export function isoWeek(iso) {
  const d = parseDate(iso);
  d.setDate(d.getDate() + 3 - ((d.getDay() + 6) % 7));
  const firstThursday = new Date(d.getFullYear(), 0, 4);
  return 1 + Math.round(((d - firstThursday) / 86400000 - 3 + ((firstThursday.getDay() + 6) % 7)) / 7);
}

export function mondayOf(iso) {
  const d = parseDate(iso);
  d.setDate(d.getDate() - ((d.getDay() + 6) % 7));
  return isoDate(d);
}

export function timeOf(timestamp) {
  if (!timestamp) return "";
  const d = new Date(timestamp);
  return d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
}

export function timeAgo(timestamp) {
  if (!timestamp) return "noch nie";
  const seconds = Math.round((Date.now() - new Date(timestamp).getTime()) / 1000);
  if (seconds < 60) return "gerade eben";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `vor ${minutes} Min.`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `vor ${hours} Std.`;
  return new Date(timestamp).toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" }) + " " + timeOf(timestamp);
}

export function minutesOf(hhmm) {
  if (!hhmm) return null;
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
}

export function decimal(value, digits = 2) {
  if (value === null || value === undefined) return "–";
  return Number(value).toFixed(digits).replace(".", ",");
}

export function plural(n, one, many) {
  return `${n} ${n === 1 ? one : many}`;
}

export const STATE_LABEL = {
  cancelled: ["Entfall", "red"],
  eva: ["EVA", "violet"],
  exam: ["Klausur", "red"],
  leave: ["Beurlaubt", "blue"],
  external: ["Extern", ""],
  substitution: ["Vertretung", "yellow"],
  "room-change": ["Raum", "blue"],
  extra: ["Zusätzlich", "blue"],
  event: ["Termin", "green"],
};

export function stateTag(state) {
  const entry = STATE_LABEL[state];
  return entry ? `<span class="tag ${entry[1]}">${entry[0]}</span>` : "";
}

export function toast(message, kind = "info", ms = 2800) {
  const host = document.getElementById("toasts");
  if (!host) return;
  const el = document.createElement("div");
  el.className = `toast ${kind === "error" ? "error" : ""}`;
  el.textContent = message;
  host.appendChild(el);
  setTimeout(() => {
    el.classList.add("out");
    el.addEventListener("animationend", () => el.remove(), { once: true });
  }, ms);
}

export function skeleton(rows = 4) {
  return `
    <div class="view-head">
      <div class="skeleton" style="width:60%;height:44px;margin-top:44px;border-radius:8px"></div>
    </div>
    ${Array.from({ length: rows }, () => '<div class="skeleton" style="height:64px;margin-bottom:10px"></div>').join("")}
  `;
}

export function errorState(error, retryLabel = "Erneut versuchen") {
  return `
    <div class="error-state">
      <div class="notice red">${icon("warning-circle")}<p>${esc(error.message || error)}</p></div>
      <div class="btn-row" style="margin-top:14px"><button class="btn" data-action="retry">${icon("arrows-clockwise", "sm")}${esc(retryLabel)}</button></div>
    </div>`;
}

export function query(params) {
  const s = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== ""));
  const str = s.toString();
  return str ? `?${str}` : "";
}
