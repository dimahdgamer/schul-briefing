import { api } from "../api.js";
import { currentSubscription, disablePush, enablePush, isIos, isStandalone, pushSupported } from "../push.js";
import { editCourse } from "../forms.js";
import { openSheet } from "../sheet.js";
import { errorState, esc, icon, longDate, shortDate, skeleton, timeAgo, toast } from "../ui.js";
import { LOADED_BUILD, hardReload } from "../version.js";

export const title = "Einstellungen";

const NOTIFY = [
  ["notify_lessons", "Stundenplan", "Entfall, EVA, Vertretung, Raumänderung"],
  ["notify_exams", "Klassenarbeiten", "Neu eingetragen, verschoben, entfernt"],
  ["notify_homework", "Hausaufgaben", "Neu eingetragene Aufgaben"],
  ["notify_letters", "Elternbriefe", "Neue Briefe der Schule"],
  ["notify_messages", "Nachrichten", "Neue Nachrichten im Messenger"],
  ["notify_calendar", "Termine", "Neue Einträge im Schulkalender"],
  ["notify_absences", "Fehlzeiten", "Neue Fehlzeiten im Klassenbuch, auch unentschuldigte"],
];

const REMINDER_DAYS = [1, 2, 3, 5, 7, 14];
const MODULE_NAMES = {
  lessons: "Stundenplan", homework: "Hausaufgaben", exams: "Klassenarbeiten",
  letters: "Elternbriefe", threads: "Nachrichten", calendar: "Kalender", absences: "Fehlzeiten",
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

function nextBriefingText(next, enabled) {
  if (!enabled) return "Das Morgen-Briefing ist ausgeschaltet.";
  if (!next) return "";
  const why = next.mode === "auto"
    ? ` (erste Stunde: ${esc(next.first_hour)}.)`
    : next.mode === "auto-fallback" ? " (kein Unterricht eingetragen, deshalb die feste Uhrzeit)" : "";
  return `Nächstes Briefing: ${esc(longDate(next.date))} um <span class="mono">${esc(next.time)}</span>${why}`;
}

function briefingSection(s, status, bell) {
  const auto = s.briefing_mode === "auto";
  const hours = bell.hours.filter((h) => h.hour in s.briefing_by_hour);
  return `
    <div class="card">
      <div class="field">
        <div><div class="field-label">Briefing am Morgen</div><div class="field-help">Nur an Schultagen, nicht in den Ferien</div></div>
        ${toggle("briefing_enabled", s, "Briefing am Morgen")}
      </div>
      <div class="field">
        <div class="field-label">Uhrzeit</div>
        <div class="segmented" role="group" aria-label="Uhrzeit des Briefings">
          <button type="button" data-mode="auto" aria-pressed="${auto}">Nach erster Stunde</button>
          <button type="button" data-mode="fixed" aria-pressed="${!auto}">Fest</button>
        </div>
      </div>
      <div class="field stack" id="briefing-auto" ${auto ? "" : "hidden"}>
        <div class="field-help">Das Briefing kommt abhängig davon, zu welcher Stunde du laut Plan anfängst. Fällt die erste Stunde aus, gilt trotzdem ihre Uhrzeit, damit du es rechtzeitig erfährst.</div>
        <div class="hour-grid">
          ${hours.map((h) => `
            <label class="hour-cell">
              <span><span class="mono">${esc(h.hour)}.</span> Stunde <span class="muted mono">${esc(h.start)}</span></span>
              <input type="time" class="input" data-hour="${esc(h.hour)}" value="${esc(s.briefing_by_hour[h.hour])}" step="300" aria-label="Briefing bei Beginn zur ${esc(h.hour)}. Stunde" />
            </label>`).join("")}
        </div>
        <div class="field-help">Ist an einem Schultag kein Unterricht eingetragen, gilt die feste Uhrzeit.</div>
      </div>
      <div class="field" id="briefing-fixed">
        <div class="field-label">${auto ? "Feste Uhrzeit (Ersatz)" : "Feste Uhrzeit"}</div>
        ${time("briefing_time", s, "Feste Uhrzeit Morgen-Briefing")}
      </div>
      <div class="field"><div class="field-help" id="next-briefing">${nextBriefingText(status.next_briefing, s.briefing_enabled)}</div></div>
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
    </div>`;
}

function courseValidity(c) {
  if (c.from && c.to) return `${shortDate(c.from)} bis ${shortDate(c.to)}`;
  if (c.from) return `ab ${shortDate(c.from)}`;
  if (c.to) return `bis ${shortDate(c.to)}`;
  return "";
}

function coursesSection(own) {
  const rows = own.courses.map((c) => `
    <div class="field">
      <div>
        <div class="field-label">${esc(c.subject)} <span class="tag">Extern</span></div>
        <div class="field-help">${esc([c.label, c.place, courseValidity(c)].filter(Boolean).join(" · "))}</div>
        <div class="field-help">${c.next
          ? `Nächster Termin: <strong>${esc(shortDate(c.next))}</strong>`
          : "Kein Termin in den nächsten vier Monaten. Bitte Wochentag, Gültigkeit und Ferien prüfen."}</div>
      </div>
      <button class="btn small" data-edit-course="${esc(c.id)}">Bearbeiten</button>
    </div>`).join("");
  return `
    <div class="card">
      <div class="field stack">
        <div class="field-help">Unterricht, den der Schulmanager nicht kennt, zum Beispiel ein Kurs an einer anderen Schule. Er steht jede Woche im Plan und zählt für den Schluss. In Schulferien und an Feiertagen fällt er aus, an schulfreien Tagen nur deiner Schule (z. B. Lehrertag) nicht. Eine Beurlaubung betrifft ihn auch.</div>
      </div>
      ${rows}
      <div class="field"><button class="btn small" data-action="add-course">+ Eigenen Unterricht eintragen</button></div>
    </div>`;
}

const FRIEND_UI = {
  offered: '<span class="tag yellow">Oberfläche angeboten</span>',
  on: '<span class="tag blue">Oberfläche aktiv</span>',
};

const FRIEND_STATE = {
  ok: '<span class="tag green">aktiv</span>',
  waiting: '<span class="tag yellow">wartet</span>',
  needs_login: '<span class="tag red">Login erneuern</span>',
};

function friendsSection(data) {
  const share = typeof navigator.share === "function";
  const uiHelp = {
    off: "Nur der Kalender-Link.",
    offered: "Du hast die Oberfläche angeboten. Sie wird erst aktiv, wenn dein Freund auf seiner Seite zustimmt.",
    on: "Dein Freund nutzt seine eigene App mit eigenem Zugangscode.",
  };
  const friends = data.friends.map((f) => `
    <div class="field stack">
      <div>
        <div class="field-label">${esc(f.label)} ${FRIEND_STATE[f.state] || ""} ${FRIEND_UI[f.ui] || ""}</div>
        <div class="field-help">${f.state === "needs_login"
          ? "Das Login stimmt nicht mehr, dein Freund muss es auf seiner Seite erneuern."
          : `zuletzt aktualisiert ${esc(timeAgo(f.last_success))}${f.last_error ? ` · ${esc(f.last_error)}` : ""}`}</div>
        <div class="field-help">${esc(uiHelp[f.ui] || uiHelp.off)}</div>
      </div>
      <div class="btn-row">
        <button class="btn small" data-friend-ui="${esc(f.id)}" data-offered="${f.ui === "off" ? "1" : "0"}" data-state="${esc(f.ui)}" data-label="${esc(f.label)}">${f.ui === "off" ? "Oberfläche anbieten" : "Oberfläche zurückziehen"}</button>
        <button class="btn small" data-friend-sync="${esc(f.id)}" ${f.state === "needs_login" ? "disabled" : ""}>Abrufen</button>
        <button class="btn small ghost" data-friend-remove="${esc(f.id)}" data-label="${esc(f.label)}">Entfernen</button>
      </div>
    </div>`).join("");
  const invites = data.invites.map((i) => `
    <div class="field">
      <div>
        <div class="field-label">${esc(i.label)} <span class="tag">Einladung offen</span></div>
        <div class="field-help">gültig bis ${esc(longDate(i.expires.slice(0, 10)))}</div>
      </div>
      <div class="btn-row">
        <button class="btn small" data-invite-send="${esc(i.url)}" data-label="${esc(i.label)}">${share ? "Teilen" : "Link kopieren"}</button>
        <button class="btn small ghost" data-invite-revoke="${esc(i.token)}">Widerrufen</button>
      </div>
    </div>`).join("");
  return `
    <div class="card">
      <div class="field stack">
        <div class="field-help">Freunde bekommen ihren Stundenplan als Kalender-Link. Du schickst ihnen einen Einladungslink, dort geben sie ihr Schulmanager-Login selbst ein. Du siehst es nie. Abgerufen werden nur Stundenplan, Klassenarbeiten und Schultermine, etwa einmal pro Stunde. Optional kannst du einem Freund eine eigene App-Oberfläche anbieten. Sie wird erst aktiv, wenn er auf seiner Seite zustimmt, und ruft dann zusätzlich Hausaufgaben und Fehlzeiten ab.</div>
      </div>
      ${friends}${invites}
      ${!friends && !invites ? '<div class="field"><div class="field-help">Noch niemand eingeladen.</div></div>' : ""}
      <div class="field"><button class="btn small" data-action="invite">+ Freund einladen</button></div>
    </div>`;
}

function appSection(me) {
  const outdated = me.build && LOADED_BUILD && me.build !== LOADED_BUILD;
  const version = me.commit ? `Version <span class="mono">${esc(me.commit)}</span>` : "Version";
  return `
    <div class="card">
      <div class="field">
        <div>
          <div class="field-label">${version} ${outdated ? '<span class="tag yellow">Update verfügbar</span>' : '<span class="tag green">aktuell</span>'}</div>
          <div class="field-help">Auf dem Server: <span class="mono">${esc(me.build || "–")}</span> · Auf diesem Gerät: <span class="mono">${esc(LOADED_BUILD || "–")}</span></div>
        </div>
        <button class="btn small ${outdated ? "primary" : ""}" data-action="hard-reload">${icon("arrows-clockwise", "sm")}App aktualisieren</button>
      </div>
      <div class="field"><div class="field-help">„App aktualisieren“ leert den Zwischenspeicher dieses Geräts und lädt die App neu. Deine Einstellungen und die Anmeldung bleiben erhalten.</div></div>
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
      ${(status.disabled_modules || []).length ? `<div class="field"><div class="field-help">Bei deiner Schule nicht freigeschaltet: ${status.disabled_modules.map((k) => esc(MODULE_NAMES[k] || k)).join(", ")}</div></div>` : ""}
      ${moduleErrors.length ? `<div class="field"><div class="field-help">Nicht verfügbar: ${moduleErrors.map(([k, v]) => `${esc(MODULE_NAMES[k] || k)} (${esc(v)})`).join(", ")}</div></div>` : ""}
      ${syncs ? `<div class="field stack"><table class="data-table"><thead><tr><th>Abruf</th><th>Auslöser</th><th class="num">Ergebnis</th></tr></thead><tbody>${syncs}</tbody></table></div>` : ""}
    </div>`;
}

export async function render(main, params, ctx) {
  main.innerHTML = skeleton(6);
  let settings, status, ical, subscription, devices, bell, me, friends, own;
  try {
    [settings, status, ical, subscription, devices, bell, me, own] = await Promise.all([
      api("/settings"),
      api("/status"),
      api("/ical"),
      currentSubscription().catch(() => null),
      api("/push/devices").then((d) => d.devices.length),
      api("/bell"),
      api("/me"),
      api("/own"),
    ]);
    // Die Verwaltung der Freunde gibt es nur für den Besitzer, ein Freund mit Oberfläche hat sie nicht
    friends = me.role === "owner" ? await api("/friends") : null;
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
      ${briefingSection(s, status, bell)}
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
        ${NOTIFY.filter(([key]) => me.role === "owner" || !["notify_letters", "notify_messages"].includes(key)).map(([key, label, help]) => `<div class="field"><div><div class="field-label">${label}</div><div class="field-help">${help}</div></div>${toggle(key, s, label)}</div>`).join("")}
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
          <div class="field-help">Stundenplan mit Vertretungen, Entfall, EVA, Klausuren, Beurlaubungen und eigenem Unterricht im Handy-Kalender. Der Link ist geheim, wer ihn kennt, sieht den Plan.</div>
          <div class="copy-field">
            <input class="input" readonly value="${esc(ical.url)}" aria-label="Kalender-Link" id="ical-url" />
            <button class="btn small" data-action="copy" data-target="ical-url">${icon("copy", "sm")}Kopieren</button>
          </div>
          <div class="btn-row">
            <a class="btn small" href="${esc(ical.webcal)}">${icon("calendar-dots", "sm")}Im Kalender öffnen</a>
            <button class="btn small ghost" data-action="ical-new">Neuen Link erzeugen</button>
          </div>
        </div>
        ${ical.events_url ? `<div class="field stack">
          <div class="field-label">Schultermine (optional, eigener Kalender)</div>
          <div class="field-help">Die Termine aus dem Schulmanager-Kalender (Elternsprechtag, Wandertag …) stehen nicht im Stundenplan-Kalender, damit er übersichtlich bleibt. Wer sie sehen will, abonniert diesen zweiten Kalender. Er hat dasselbe Geheimnis, ein neuer Link oben erneuert auch diesen.</div>
          <div class="copy-field">
            <input class="input" readonly value="${esc(ical.events_url)}" aria-label="Link Schultermine" id="ical-events-url" />
            <button class="btn small" data-action="copy" data-target="ical-events-url">${icon("copy", "sm")}Kopieren</button>
          </div>
          <div class="btn-row">
            <a class="btn small" href="${esc(ical.events_webcal)}">${icon("calendar-dots", "sm")}Im Kalender öffnen</a>
          </div>
        </div>` : ""}
      </div>
    </section>

    <section class="section reveal" style="--i:6">
      <h2 class="section-title">Eigener Unterricht</h2>
      ${coursesSection(own)}
    </section>

    ${friends ? `<section class="section reveal" style="--i:6">
      <h2 class="section-title">Freunde</h2>
      ${friendsSection(friends)}
    </section>` : ""}

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

    <section class="section reveal" style="--i:9">
      <h2 class="section-title">App</h2>
      ${appSection(me)}
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
      refreshNext();
    } catch (error) {
      toast(error.message, "error");
    }
  };
  const refreshNext = async () => {
    try {
      const fresh = await api("/status");
      const el = main.querySelector("#next-briefing");
      if (el) el.innerHTML = nextBriefingText(fresh.next_briefing, s.briefing_enabled);
    } catch {
      // Nur eine Anzeige, Fehler hier sind egal.
    }
  };
  const save = (patch) => {
    if (patch.briefing_by_hour) {
      patch = { ...patch, briefing_by_hour: { ...(pending.briefing_by_hour || {}), ...patch.briefing_by_hour } };
    }
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

  main.querySelectorAll("[data-hour]").forEach((input) => {
    input.addEventListener("change", () => {
      if (input.value) save({ briefing_by_hour: { [input.dataset.hour]: input.value } });
    });
  });

  main.querySelectorAll("[data-mode]").forEach((button) => {
    button.addEventListener("click", () => {
      const mode = button.dataset.mode;
      main.querySelectorAll("[data-mode]").forEach((b) => b.setAttribute("aria-pressed", String(b === button)));
      main.querySelector("#briefing-auto").hidden = mode !== "auto";
      main.querySelector("#briefing-fixed .field-label").textContent = mode === "auto" ? "Feste Uhrzeit (Ersatz)" : "Feste Uhrzeit";
      save({ briefing_mode: mode });
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
  main.querySelectorAll('[data-action="copy"]').forEach((button) => {
    button.addEventListener("click", async () => {
      const field = main.querySelector(`#${button.dataset.target}`);
      try {
        await navigator.clipboard.writeText(field.value);
        toast("Link kopiert");
      } catch {
        field.select();
        toast("Bitte manuell kopieren", "error");
      }
    });
  });
  on("ical-new", async () => {
    if (!confirm("Der alte Link funktioniert danach nicht mehr. Fortfahren?")) return;
    const fresh = await api("/ical/regenerate", { method: "POST" });
    main.querySelector("#ical-url").value = fresh.url;
    main.querySelector("#ical-events-url").value = fresh.events_url;
    toast("Neuer Link erzeugt");
  });
  // ── Eigener Unterricht ──
  on("add-course", () => editCourse(null, ctx));
  main.querySelectorAll("[data-edit-course]").forEach((button) => {
    button.addEventListener("click", () => editCourse(own.courses.find((c) => c.id === button.dataset.editCourse), ctx));
  });

  // ── Freunde ──
  on("invite", () => openSheet({
    title: "Freund einladen",
    fields: [{ name: "label", label: "Name (nur für dich sichtbar)", required: true, maxlength: 40, placeholder: "z. B. Max" }],
    submitLabel: "Einladung erzeugen",
    onSubmit: async (values) => {
      await api("/friends/invite", { method: "POST", body: values });
      toast("Einladung erzeugt, jetzt teilen");
      ctx.rerender();
    },
  }));
  main.querySelectorAll("[data-invite-send]").forEach((button) => {
    button.addEventListener("click", async () => {
      const url = button.dataset.inviteSend;
      try {
        if (typeof navigator.share === "function") {
          await navigator.share({ title: "Schulkalender", text: "Hier kannst du deinen Stundenplan als Kalender einrichten:", url });
        } else {
          await navigator.clipboard.writeText(url);
          toast("Einladungslink kopiert");
        }
      } catch (error) {
        if (error && error.name === "AbortError") return; // Teilen abgebrochen
        window.prompt("Bitte den Link manuell kopieren:", url);
      }
    });
  });
  main.querySelectorAll("[data-invite-revoke]").forEach((button) => {
    button.addEventListener("click", async () => {
      if (!confirm("Die Einladung widerrufen? Der Link funktioniert dann nicht mehr.")) return;
      try {
        await api(`/friends/invite/${encodeURIComponent(button.dataset.inviteRevoke)}`, { method: "DELETE" });
        ctx.rerender();
      } catch (error) {
        toast(error.message, "error");
      }
    });
  });
  main.querySelectorAll("[data-friend-ui]").forEach((button) => {
    button.addEventListener("click", async () => {
      const offered = button.dataset.offered === "1";
      const withdraw = !offered && button.dataset.state === "on"
        ? `Die Oberfläche von ${button.dataset.label} abschalten? Der Zugangscode wird sofort ungültig, die zusätzlich abgerufenen Daten (Hausaufgaben, Fehlzeiten) und die Geräte für Benachrichtigungen werden gelöscht. Der Kalender-Link bleibt.`
        : null;
      if (withdraw && !confirm(withdraw)) return;
      try {
        await api(`/friends/${encodeURIComponent(button.dataset.friendUi)}/ui`, { method: "POST", body: { offered } });
        toast(offered ? "Angeboten: dein Freund muss auf seiner Seite zustimmen" : "Zurückgezogen");
        ctx.rerender();
      } catch (error) {
        toast(error.message, "error");
      }
    });
  });
  main.querySelectorAll("[data-friend-sync]").forEach((button) => {
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        const result = await api(`/friends/${encodeURIComponent(button.dataset.friendSync)}/sync`, { method: "POST" });
        toast(result.ok ? "Abgerufen" : result.error || "Abruf fehlgeschlagen", result.ok ? "info" : "error");
        ctx.rerender();
      } catch (error) {
        toast(error.message, "error");
        button.disabled = false;
      }
    });
  });
  main.querySelectorAll("[data-friend-remove]").forEach((button) => {
    button.addEventListener("click", async () => {
      if (!confirm(`${button.dataset.label} entfernen? Gespeichertes Login und alle Daten werden gelöscht, der Kalender-Link hört auf zu funktionieren.`)) return;
      try {
        await api(`/friends/${encodeURIComponent(button.dataset.friendRemove)}`, { method: "DELETE" });
        toast("Entfernt");
        ctx.rerender();
      } catch (error) {
        toast(error.message, "error");
      }
    });
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
  on("hard-reload", async (event) => {
    event.currentTarget.disabled = true;
    await hardReload();
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
