// Kleine Helfer für Markup, Datumsformate und Rückmeldungen.

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

export function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ESC[c]);
}

export function icon(name, cls = "") {
  return `<svg class="icon ${cls}" aria-hidden="true"><use href="/icons/sprite.svg#i-${name}"></use></svg>`;
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
      <div class="skeleton" style="width:140px;height:14px;border-radius:4px"></div>
      <div class="skeleton" style="width:72%;height:48px;margin-top:12px;border-radius:8px"></div>
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
