import { api } from "../api.js";
import { currentSubscription, disablePush, enablePush, isIos, isStandalone, pushSupported } from "../push.js";
import { errorState, esc, icon, skeleton, timeAgo, toast } from "../ui.js";

export const title = "Einstellungen";

const NOTIFY = [
  ["notify_lessons", "Stundenplan", "Entfall, Vertretung, Raumänderung"],
  ["notify_exams", "Klassenarbeiten", "Neu eingetragen, verschoben, entfernt"],
  ["notify_homework", "Hausaufgaben", "Neu eingetragene Aufgaben"],
  ["notify_grades", "Noten", "Neue Noten"],
  ["notify_letters", "Elternbriefe", "Neue Briefe der Schule"],
  ["notify_messages", "Nachrichten", "Neue Nachrichten im Messenger"],
  ["notify_calendar", "Termine", "Neue Einträge im Schulkalender"],
];

const REMINDER_DAYS = [1, 2, 3, 5, 7, 14];
const MODULE_NAMES = {
  lessons: "Stundenplan", homework: "Hausaufgaben", exams: "Klassenarbeiten", grades: "Noten",
  letters: "Elternbriefe", threads: "Nachrichten", calendar: "Kalender",
};

function readTheme() {
  try {
    return localStorage.getItem("theme") || "system";
  } catch {
    return "system";
  }
}

function applyTheme(theme) {
  try {
    if (theme === "system") localStorage.removeItem("theme");
    else localStorage.setItem("theme", theme);
  } catch {
    // Ohne Speicher gilt die Wahl nur bis zum Neuladen.
  }
  if (theme === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
}

const toggle = (key, s, label) => `<input type="checkbox" class="switch" data-setting="${key}" ${s[key] ? "checked" : ""} aria-label="${esc(label)}" />`;
const time = (key, s, label) => `<input type="time" class="input" data-setting="${key}" value="${esc(s[key])}" step="300" aria-label="${esc(label)}" />`;

function pushSection(state) {
  let body;
  if (!pushSupported()) {
    body = isIos() && !isStandalone()
      ? `<div class="notice yellow">${icon("push-pin")}<p><strong>Auf dem iPhone:</strong> Unten auf <kbd>Teilen</kbd> tippen, dann <kbd>Zum Home-Bildschirm</kbd>. Danach die App vom Home-Bildschirm öffnen und hier die Benachrichtigungen aktivieren (ab iOS 16.4).</p></div>`
      : `<div class="notice">${icon("warning-circle")}<p>Dieser Browser unterstützt keine Push-Benachrichtigungen.</p></div>`;
    return `<div class="card pad">${body}</div>`;
  }
  const active = Boolean(state.subscription) && Notification.permission === "granted";
  const denied = Notification.permission === "denied";
  return `
    <div class="card">
      <div class="field">
        <div>
          <div class="field-label">${active ? "Auf diesem Gerät aktiv" : "Auf diesem Gerät aus"}</div>
          <div class="field-help">${denied ? "Benachrichtigungen sind im Browser blockiert. Bitte in den Website- bzw. Systemeinstellungen erlauben." : `${state.devices} ${state.devices === 1 ? "Gerät" : "Geräte"} insgesamt registriert`}</div>
        </div>
        <button class="btn ${active ? "" : "primary"} small" data-action="${active ? "push-off" : "push-on"}" ${denied ? "disabled" : ""}>${active ? "Deaktivieren" : "Aktivieren"}</button>
      </div>
      <div class="field">
        <div><div class="field-label">Testnachricht</div><div class="field-help">Schickt sofort eine Nachricht an alle Geräte.</div></div>
        <button class="btn small" data-action="push-test">${icon("bell-ringing", "sm")}Senden</button>
      </div>
    </div>`;
}

function statusSection(status) {
  const account = status.account || {};
  const moduleErrors = Object.entries(status.module_errors || {});
  const syncs = (status.syncs || []).slice(0, 5).map((s) => `
    <tr><td class="d">${esc(timeAgo(s.started_at))}</td><td>${esc(s.trigger || "")}</td>
    <td class="num">${s.ok ? `${s.changes} Änd.` : '<span class="tag red">Fehler</span>'}</td></tr>`).join("");
  return `
    <div class="card">
      <div class="field">
        <div><div class="field-label">${esc(account.name || "Schulmanager-Konto")}${status.demo ? ' <span class="tag yellow">Demo</span>' : ""}</div>
        <div class="field-help">${account.class ? `Klasse ${esc(account.class)} · ` : ""}zuletzt erfolgreich ${esc(timeAgo(status.last_success))}</div></div>
        <button class="btn small" data-action="sync">${icon("arrows-clockwise", "sm")}Abrufen</button>
      </div>
      ${status.last_error ? `<div class="field"><div class="notice red" style="width:100%">${icon("warning-circle")}<p>${esc(status.last_error)}</p></div></div>` : ""}
      ${moduleErrors.length ? `<div class="field"><div class="field-help">Nicht verfügbar: ${moduleErrors.map(([k, v]) => `${esc(MODULE_NAMES[k] || k)} (${esc(v)})`).join(", ")}</div></div>` : ""}
      ${syncs ? `<div class="field stack"><table class="grade-table"><thead><tr><th>Abruf</th><th>Auslöser</th><th class="num">Ergebnis</th></tr></thead><tbody>${syncs}</tbody></table></div>` : ""}
    </div>`;
}

export async function render(main, params, ctx) {
  main.innerHTML = skeleton(6);
  let settings, status, ical, subscription, devices;
  try {
    [settings, status, ical, subscription, devices] = await Promise.all([
      api("/settings"),
      api("/status"),
      api("/ical"),
      currentSubscription().catch(() => null),
      api("/push/devices").then((d) => d.devices.length),
    ]);
  } catch (error) {
    if (!ctx.isCurrent()) return;
    main.innerHTML = errorState(error);
    main.querySelector("[data-action=retry]")?.addEventListener("click", () => ctx.rerender());
    return;
  }
  if (!ctx.isCurrent()) return;
  const s = settings;
  const theme = readTheme();

  main.innerHTML = `
    <header class="view-head reveal">
      <p class="eyebrow">Briefing, Push und Abruf</p>
      <h1 class="display">Einstellungen</h1>
    </header>

    <section class="section reveal" style="--i:1">
      <h2 class="section-title">Benachrichtigungen</h2>
      ${pushSection({ subscription, devices })}
    </section>

    <section class="section reveal" style="--i:2">
      <h2 class="section-title">Morgen-Briefing</h2>
      <div class="card">
        <div class="field">
          <div><div class="field-label">Briefing am Morgen</div><div class="field-help">Nur an Schultagen, nicht in den Ferien</div></div>
          ${toggle("briefing_enabled", s, "Briefing am Morgen")}
        </div>
        <div class="field"><div class="field-label">Uhrzeit</div>${time("briefing_time", s, "Uhrzeit Morgen-Briefing")}</div>
        <div class="field">
          <div><div class="field-label">Abend-Vorschau</div><div class="field-help">Am Vorabend eines Schultags, auch Sonntagabend</div></div>
          ${toggle("evening_enabled", s, "Abend-Vorschau")}
        </div>
        <div class="field"><div class="field-label">Uhrzeit</div>${time("evening_time", s, "Uhrzeit Abend-Vorschau")}</div>
        <div class="field stack">
          <div class="btn-row">
            <button class="btn small" data-action="preview" data-kind="morning">Vorschau Morgen</button>
            <button class="btn small" data-action="preview" data-kind="evening">Vorschau Abend</button>
            <button class="btn small ghost" data-action="send-briefing">${icon("bell-ringing", "sm")}Briefing jetzt senden</button>
          </div>
          <div id="preview"></div>
        </div>
      </div>
    </section>

    <section class="section reveal" style="--i:3">
      <h2 class="section-title">Klausur-Erinnerungen</h2>
      <div class="card">
        <div class="field"><div class="field-label">Erinnern</div>${toggle("reminder_enabled", s, "Klausur-Erinnerungen")}</div>
        <div class="field"><div class="field-label">Uhrzeit</div>${time("reminder_time", s, "Uhrzeit Klausur-Erinnerung")}</div>
        <div class="field stack">
          <div><div class="field-label">Tage vorher</div><div class="field-help">Mehrfachauswahl</div></div>
          <div class="chips" role="group" aria-label="Tage vorher">
            ${REMINDER_DAYS.map((d) => `<button class="chip" type="button" data-day="${d}" aria-pressed="${s.reminder_days.includes(d)}">${d}</button>`).join("")}
          </div>
        </div>
      </div>
    </section>

    <section class="section reveal" style="--i:4">
      <h2 class="section-title">Sofort melden</h2>
      <div class="card">
        ${NOTIFY.map(([key, label, help]) => `<div class="field"><div><div class="field-label">${label}</div><div class="field-help">${help}</div></div>${toggle(key, s, label)}</div>`).join("")}
        <div class="field">
          <div><div class="field-label">Notenwert in der Nachricht</div><div class="field-help">Aus: nur „Neue Note in Mathe“, damit die Note nicht auf dem Sperrbildschirm steht</div></div>
          ${toggle("grade_values_in_push", s, "Notenwert in der Nachricht")}
        </div>
      </div>
    </section>

    <section class="section reveal" style="--i:5">
      <h2 class="section-title">Abruf</h2>
      <div class="card">
        <div class="field">
          <div><div class="field-label">Intervall an Schultagen</div><div class="field-help">Wie oft Schulmanager abgefragt wird</div></div>
          <select class="input" data-setting="poll_interval">
            ${[10, 15, 20, 30, 60].map((m) => `<option value="${m}" ${s.poll_interval === m ? "selected" : ""}>alle ${m} Min.</option>`).join("")}
          </select>
        </div>
        <div class="field">
          <div><div class="field-label">Intervall Wochenende & Ferien</div></div>
          <select class="input" data-setting="weekend_poll_interval">
            ${[30, 60, 120, 240].map((m) => `<option value="${m}" ${s.weekend_poll_interval === m ? "selected" : ""}>alle ${m >= 60 ? `${m / 60} Std.` : `${m} Min.`}</option>`).join("")}
          </select>
        </div>
        <div class="field"><div><div class="field-label">Abrufen ab</div><div class="field-help">Nachts wird nicht abgefragt</div></div>${time("poll_start", s, "Abrufen ab")}</div>
        <div class="field"><div class="field-label">Abrufen bis</div>${time("poll_end", s, "Abrufen bis")}</div>
      </div>
    </section>

    <section class="section reveal" style="--i:6">
      <h2 class="section-title">Kalender-Abo</h2>
      <div class="card">
        <div class="field stack">
          <div class="field-help">Stundenplan mit Vertretungen, Klassenarbeiten und Schultermine im Handy-Kalender. Der Link ist geheim, wer ihn kennt, sieht den Plan.</div>
          <div class="copy-field">
            <input class="input" readonly value="${esc(ical.url)}" aria-label="Kalender-Link" id="ical-url" />
            <button class="btn small" data-action="copy">${icon("copy", "sm")}Kopieren</button>
          </div>
          <div class="btn-row">
            <a class="btn small" href="${esc(ical.webcal)}">${icon("calendar-dots", "sm")}Im Kalender öffnen</a>
            <button class="btn small ghost" data-action="ical-new">Neuen Link erzeugen</button>
          </div>
        </div>
      </div>
    </section>

    <section class="section reveal" style="--i:7">
      <h2 class="section-title">Darstellung</h2>
      <div class="card">
        <div class="field">
          <div class="field-label">Farbschema</div>
          <div class="segmented" role="group" aria-label="Farbschema">
            ${[["system", "System"], ["light", "Hell"], ["dark", "Dunkel"]].map(([v, l]) => `<button type="button" data-theme="${v}" aria-pressed="${theme === v}">${l}</button>`).join("")}
          </div>
        </div>
      </div>
    </section>

    <section class="section reveal" style="--i:8">
      <h2 class="section-title">Status</h2>
      ${statusSection(status)}
    </section>

    <div class="btn-row reveal" style="margin-top:32px;justify-content:center">
      <button class="btn ghost" data-action="logout">${icon("sign-out", "sm")}Abmelden</button>
    </div>`;

  // ── Einstellungen speichern ──
  let saveTimer = null;
  const pending = {};
  const flush = async () => {
    clearTimeout(saveTimer);
    const body = { ...pending };
    Object.keys(pending).forEach((k) => delete pending[k]);
    if (!Object.keys(body).length) return;
    try {
      Object.assign(s, await api("/settings", { method: "PUT", body }));
      toast("Gespeichert");
    } catch (error) {
      toast(error.message, "error");
    }
  };
  const save = (patch) => {
    Object.assign(pending, patch);
    clearTimeout(saveTimer);
    saveTimer = setTimeout(flush, 400);
  };

  main.querySelectorAll("[data-setting]").forEach((input) => {
    input.addEventListener("change", () => {
      const key = input.dataset.setting;
      let value = input.type === "checkbox" ? input.checked : input.value;
      if (input.tagName === "SELECT") value = Number(value);
      if (input.type === "time" && !value) return;
      save({ [key]: value });
    });
  });

  main.querySelectorAll("[data-day]").forEach((chip) => {
    chip.addEventListener("click", () => {
      const pressed = chip.getAttribute("aria-pressed") !== "true";
      chip.setAttribute("aria-pressed", String(pressed));
      const days = [...main.querySelectorAll("[data-day]")]
        .filter((c) => c.getAttribute("aria-pressed") === "true")
        .map((c) => Number(c.dataset.day));
      save({ reminder_days: days });
    });
  });

  main.querySelectorAll("[data-theme]").forEach((button) => {
    button.addEventListener("click", () => {
      applyTheme(button.dataset.theme);
      main.querySelectorAll("[data-theme]").forEach((b) => b.setAttribute("aria-pressed", String(b === button)));
    });
  });

  // ── Aktionen ──
  const on = (action, handler) => main.querySelectorAll(`[data-action="${action}"]`).forEach((el) => el.addEventListener("click", handler));

  on("push-on", async () => {
    try {
      await enablePush();
      toast("Benachrichtigungen aktiviert");
      ctx.rerender();
    } catch (error) {
      toast(error.message, "error", 5000);
    }
  });
  on("push-off", async () => {
    await disablePush();
    toast("Benachrichtigungen auf diesem Gerät deaktiviert");
    ctx.rerender();
  });
  on("push-test", async () => {
    try {
      const { delivered } = await api("/push/test", { method: "POST" });
      toast(delivered ? `An ${delivered} ${delivered === 1 ? "Gerät" : "Geräte"} gesendet` : "Kein Gerät registriert", delivered ? "info" : "error");
    } catch (error) {
      toast(error.message, "error");
    }
  });
  on("preview", async (event) => {
    const kind = event.currentTarget.dataset.kind;
    try {
      const result = await api(`/briefing/${kind}`);
      main.querySelector("#preview").innerHTML = `
        <div class="notice" style="margin-top:12px;flex-direction:column;gap:4px">
          <strong>${esc(result.push.title)}</strong>
          <span style="white-space:pre-line">${esc(result.push.body)}</span>
        </div>`;
    } catch (error) {
      toast(error.message, "error");
    }
  });
  on("send-briefing", async () => {
    try {
      const { delivered } = await api("/briefing/morning/send", { method: "POST" });
      toast(delivered ? "Briefing gesendet" : "Kein Gerät registriert", delivered ? "info" : "error");
    } catch (error) {
      toast(error.message, "error");
    }
  });
  on("copy", async () => {
    const field = main.querySelector("#ical-url");
    try {
      await navigator.clipboard.writeText(field.value);
      toast("Link kopiert");
    } catch {
      field.select();
      toast("Bitte manuell kopieren", "error");
    }
  });
  on("ical-new", async () => {
    if (!confirm("Der alte Link funktioniert danach nicht mehr. Fortfahren?")) return;
    const fresh = await api("/ical/regenerate", { method: "POST" });
    main.querySelector("#ical-url").value = fresh.url;
    toast("Neuer Link erzeugt");
  });
  on("sync", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      const result = await api("/sync", { method: "POST" });
      toast(result.ok ? `Abruf fertig: ${result.changes} Änderungen` : result.error, result.ok ? "info" : "error");
      ctx.rerender();
    } catch (error) {
      toast(error.message, "error");
      button.disabled = false;
    }
  });
  on("logout", async () => {
    await api("/logout", { method: "POST" }).catch(() => {});
    location.replace("/#/login");
    location.reload();
  });

  return () => {
    flush();
  };
}
