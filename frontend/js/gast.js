import { api } from "./api.js";
import { esc, timeAgo, toast } from "./ui.js";

// Öffentliche Seiten für Freunde, ohne Anmeldung an der App:
//   /einladung/<token>  Schulmanager-Login eingeben und den Kalender einrichten
//   /freund/<token>     eigener Kalender-Link, Login ändern, alles löschen
// Der geheime Link in der Adresse ist der einzige Zugang. Er wird nirgends weitergegeben.

const root = document.getElementById("root");
const [, kind, token] = location.pathname.split("/");
const BASE = `/${encodeURIComponent(token || "")}`;

function shell(inner) {
  root.innerHTML = `
    <main class="guest">
      <div class="guest-brand"><span class="brand-mark" style="width:30px;height:30px;font-size:21px;border-radius:8px">S</span>Schulkalender</div>
      ${inner}
    </main>`;
}

function message(title, text) {
  shell(`<div class="empty reveal"><span class="serif">${esc(title)}</span>${esc(text)}</div>`);
}

function loginForm({ submit, consent }) {
  return `
    <form class="guest-form" novalidate>
      <label class="field-label" for="g-email">Benutzername oder E-Mail bei Schulmanager</label>
      <input class="input" id="g-email" name="email" autocomplete="username" autocapitalize="none" spellcheck="false" required />
      <label class="field-label" for="g-pw">Passwort bei Schulmanager</label>
      <input class="input" id="g-pw" name="password" type="password" autocomplete="current-password" required />
      ${consent ? `<label class="guest-check"><input type="checkbox" name="consent" /><span>Ich bin damit einverstanden, dass mein Login so gespeichert und genutzt wird, wie oben beschrieben.</span></label>` : ""}
      <p class="form-error" role="alert"></p>
      <button class="btn primary" type="submit">${esc(submit)}</button>
    </form>`;
}

// Verbindet ein Login-Formular mit einer Aktion. Fehler erscheinen unter dem Formular.
function wireForm(form, action) {
  const error = form.querySelector(".form-error");
  const button = form.querySelector("button[type=submit]");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    error.textContent = "";
    const email = form.email.value.trim();
    const password = form.password.value;
    if (!email || !password) {
      error.textContent = "Bitte Benutzername und Passwort eingeben.";
      return;
    }
    if (form.consent && !form.consent.checked) {
      error.textContent = "Bitte stimme der Speicherung zu.";
      return;
    }
    button.disabled = true;
    button.textContent = "Wird geprüft …";
    try {
      await action({ email, password, consent: form.consent ? true : undefined });
    } catch (failure) {
      error.textContent = failure.message || "Das hat nicht geklappt.";
      button.disabled = false;
      button.textContent = button.dataset.label;
      form.password.select();
    }
  });
  button.dataset.label = button.textContent;
}

// ── Einladung ────────────────────────────────────────────────────────

async function invitePage() {
  let info;
  try {
    info = await api(`/invite${BASE}`);
  } catch (error) {
    message("Link ungültig", error.message);
    return;
  }
  shell(`
    <header class="reveal">
      <p class="eyebrow">Einladung</p>
      <h1 class="display">Hallo ${esc(info.label)}.</h1>
      <p class="lede">Dein Stundenplan als Kalender im Handy, mit Vertretungen, Entfall, EVA und Klassenarbeiten. Das dauert eine Minute.</p>
    </header>

    <section class="card pad reveal" style="--i:1">
      <h2 class="guest-h">So funktioniert es</h2>
      <ol class="guest-list">
        <li>Du gibst dein Schulmanager-Login ein. Die Seite prüft es sofort.</li>
        <li>Mit dem Login werden regelmäßig dein Stundenplan, deine Klassenarbeiten und die Schultermine abgerufen.</li>
        <li>Du bekommst einen Kalender-Link, den du im Handy abonnierst.</li>
      </ol>
    </section>

    <section class="notice yellow reveal" style="--i:2;flex-direction:column;gap:6px">
      <strong>Das solltest du wissen</strong>
      <ul class="guest-list">
        <li>Dein Passwort wird verschlüsselt auf dem Server gespeichert, der diese Seite betreibt. Wer diesen Server betreibt, kann es technisch trotzdem lesen. Gib es nur ein, wenn du ihm vertraust.</li>
        <li>Es wird nichts anderes abgerufen: keine Nachrichten, keine Elternbriefe, keine Noten.</li>
        <li>Du kannst jederzeit auf deiner Seite alles löschen. Danach ist dein Login weg.</li>
        <li>Das ist kein Angebot von Schulmanager Online und keine offizielle App.</li>
      </ul>
    </section>

    <section class="card pad reveal" style="--i:3">
      ${loginForm({ submit: "Kalender einrichten", consent: true })}
    </section>`);

  wireForm(root.querySelector("form"), async ({ email, password, consent }) => {
    const result = await api(`/invite${BASE}`, { method: "POST", body: { email, password, consent } });
    location.replace(result.manage);
  });
}

// ── Seite eines Freundes ─────────────────────────────────────────────

function statusBlock(info) {
  const account = info.account || {};
  const who = [account.name, account.class ? `Klasse ${account.class}` : ""].filter(Boolean).join(" · ");
  if (info.state === "needs_login") {
    return `<div class="notice red reveal"><p><strong>Dein Login funktioniert nicht mehr.</strong> Vielleicht hast du dein Passwort geändert. Gib es unten neu ein, dann geht es weiter. Bis dahin bleibt der Kalender auf dem letzten Stand.</p></div>`;
  }
  if (info.state === "waiting") {
    return `<div class="notice blue reveal"><p><strong>Der erste Abruf steht noch aus.</strong> Das dauert höchstens ein paar Minuten, dann füllt sich der Kalender.</p></div>`;
  }
  return `<p class="muted reveal">${who ? `${esc(who)} · ` : ""}zuletzt aktualisiert ${esc(timeAgo(info.last_success))}</p>`;
}

function calendarBlock(info) {
  return `
    <section class="card pad reveal" style="--i:2">
      <h2 class="guest-h">Kalender abonnieren</h2>
      <div class="btn-row" style="margin-bottom:12px">
        <a class="btn primary" href="${esc(info.webcal)}">Im Kalender abonnieren</a>
      </div>
      <div class="copy-field">
        <input class="input" readonly value="${esc(info.url)}" aria-label="Kalender-Link" id="g-url" />
        <button class="btn small" type="button" data-action="copy">Kopieren</button>
      </div>
      <details class="guest-help">
        <summary>Anleitung für iPhone und Android</summary>
        <p><strong>iPhone:</strong> Auf „Im Kalender abonnieren“ tippen und bestätigen. Oder: Einstellungen → Kalender → Accounts → Account hinzufügen → Andere → Kalenderabo hinzufügen, dort den Link einfügen.</p>
        <p><strong>Android:</strong> Die Google-Kalender-App kann Abos nicht direkt hinzufügen. Am Computer calendar.google.com öffnen, links bei „Weitere Kalender“ auf + und „Per URL“ wählen, den Link einfügen. Danach erscheint der Kalender auch auf dem Handy.</p>
        <p>Kalender-Apps laden Abos je nach App alle paar Stunden bis zu einmal am Tag neu. Änderungen im Stundenplan kommen deshalb nicht sofort an.</p>
      </details>
    </section>`;
}

function uiBlock(info) {
  if (info.ui === "offered") {
    return `
      <section class="card pad reveal" style="--i:3">
        <h2 class="guest-h">App-Oberfläche für dich</h2>
        <p style="margin:0 0 10px">Dein Freund bietet dir seine App an: dein Stundenplan mit Vertretungen, Hausaufgaben, Klausuren und Fehlzeiten, ein Morgen-Briefing und Benachrichtigungen auf dem Handy, dazu eigene Einträge für Klausuren und Beurlaubungen.</p>
        <ul class="guest-list">
          <li>Dafür ruft die App zusätzlich deine Hausaufgaben und, falls deine Schule sie freigibt, deine Fehlzeiten ab. Nachrichten und Elternbriefe werden nie abgerufen.</li>
          <li>Du meldest dich mit einem eigenen Zugangscode an, den du hier bekommst.</li>
          <li>Dein Kalender-Link bleibt genau, wie er ist.</li>
          <li>Du kannst die Oberfläche hier jederzeit wieder abschalten. Dann werden die zusätzlichen Daten gelöscht.</li>
        </ul>
        <div class="btn-row" style="margin-top:14px"><button class="btn primary" type="button" data-action="ui-activate">Oberfläche aktivieren</button></div>
      </section>`;
  }
  if (info.ui === "on") {
    return `
      <section class="card pad reveal" style="--i:3">
        <h2 class="guest-h">Deine App</h2>
        <p style="margin:0 0 10px">Öffne die App-Adresse und melde dich mit deinem Zugangscode an. Der Code ist nur für dich, gib ihn nicht weiter.</p>
        <div class="field-label">Zugangscode</div>
        <div class="copy-field" style="margin:6px 0 12px">
          <input class="input" readonly value="${esc(info.code || "")}" aria-label="Zugangscode" id="g-code" style="letter-spacing:0.08em" />
          <button class="btn small" type="button" data-action="copy" data-target="g-code">Kopieren</button>
        </div>
        <div class="btn-row">
          <a class="btn primary" href="${esc(info.app_url)}">App öffnen</a>
          <button class="btn small" type="button" data-action="ui-newcode">Neuen Code erzeugen</button>
          <button class="btn small" type="button" data-action="ui-off">Oberfläche abschalten</button>
        </div>
        <details class="guest-help">
          <summary>Auf den Home-Bildschirm legen</summary>
          <p><strong>iPhone:</strong> App-Adresse in Safari öffnen, Teilen, „Zum Home-Bildschirm“. Danach die App von dort öffnen und den Code eingeben (ab iOS 16.4 gibt es dann auch Benachrichtigungen).</p>
          <p><strong>Android:</strong> App-Adresse in Chrome öffnen, Menü, „App installieren“.</p>
        </details>
      </section>`;
  }
  return "";
}

function manageBlock(info) {
  const needsLogin = info.state === "needs_login";
  return `
    <section class="card pad reveal" style="--i:3">
      <h2 class="guest-h">${needsLogin ? "Login erneuern" : "Verwalten"}</h2>
      ${needsLogin ? loginForm({ submit: "Login speichern" }) : `
        <div class="btn-row">
          <button class="btn small" type="button" data-action="toggle-login">Login ändern</button>
          <button class="btn small" type="button" data-action="new-link">Neuen Link erzeugen</button>
          <button class="btn small" type="button" data-action="delete">Alles löschen</button>
        </div>
        <div id="g-login" hidden style="margin-top:14px">${loginForm({ submit: "Login speichern" })}</div>`}
      <p class="field-help" style="margin-top:12px">Speichere diese Seite als Lesezeichen, sie ist dein Zugang. Der Link ist geheim: Wer ihn kennt, kann deinen Kalender-Link ändern oder alles löschen.</p>
    </section>`;
}

async function friendPage(preloaded) {
  let info = preloaded;
  try {
    info = info || await api(`/friend${BASE}`);
  } catch (error) {
    message("Link ungültig", error.message);
    return;
  }
  shell(`
    <header class="reveal">
      <p class="eyebrow">Dein Schulkalender</p>
      <h1 class="display">Hallo ${esc(info.label)}.</h1>
    </header>
    ${statusBlock(info)}
    ${calendarBlock(info)}
    ${uiBlock(info)}
    ${manageBlock(info)}`);

  const redraw = (fresh) => friendPage(fresh);
  root.querySelectorAll("form").forEach((form) => {
    wireForm(form, async ({ email, password }) => {
      const fresh = await api(`/friend${BASE}/login`, { method: "POST", body: { email, password } });
      toast("Login gespeichert");
      redraw(fresh);
    });
  });
  const on = (action, handler) => root.querySelector(`[data-action="${action}"]`)?.addEventListener("click", handler);
  root.querySelectorAll('[data-action="copy"]').forEach((button) => {
    button.addEventListener("click", async () => {
      const field = root.querySelector(`#${button.dataset.target || "g-url"}`);
      try {
        await navigator.clipboard.writeText(field.value);
        toast("Kopiert");
      } catch {
        field.select();
        toast("Bitte manuell kopieren", "error");
      }
    });
  });
  const ui = (action, message, confirmText) => async () => {
    if (confirmText && !confirm(confirmText)) return;
    try {
      redraw(await api(`/friend${BASE}/ui`, { method: "POST", body: { action } }));
      toast(message);
    } catch (error) {
      toast(error.message, "error");
    }
  };
  on("ui-activate", ui("activate", "Oberfläche aktiviert"));
  on("ui-newcode", ui("new-code", "Neuer Zugangscode erzeugt", "Der alte Zugangscode funktioniert danach nicht mehr, du musst dich in der App neu anmelden. Fortfahren?"));
  on("ui-off", ui("deactivate", "Oberfläche abgeschaltet", "Die Oberfläche abschalten? Dein Zugangscode wird ungültig, und die zusätzlich abgerufenen Daten (Hausaufgaben, Fehlzeiten) sowie Geräte für Benachrichtigungen werden gelöscht. Dein Kalender-Link bleibt."));
  on("toggle-login", () => {
    const box = root.querySelector("#g-login");
    box.hidden = !box.hidden;
  });
  on("new-link", async () => {
    if (!confirm("Der alte Kalender-Link funktioniert danach nicht mehr, du musst den neuen im Handy eintragen. Fortfahren?")) return;
    try {
      redraw(await api(`/friend${BASE}/regenerate`, { method: "POST" }));
      toast("Neuer Link erzeugt");
    } catch (error) {
      toast(error.message, "error");
    }
  });
  on("delete", async () => {
    if (!confirm("Dein gespeichertes Login und alle abgerufenen Daten werden gelöscht, der Kalender-Link hört auf zu funktionieren. Wirklich alles löschen?")) return;
    try {
      await api(`/friend${BASE}`, { method: "DELETE" });
      message("Alles gelöscht", "Dein Login und deine Daten sind entfernt. Der Kalender-Link funktioniert nicht mehr. Du kannst den Kalender im Handy jetzt löschen.");
    } catch (error) {
      toast(error.message, "error");
    }
  });
}

if (kind === "einladung") invitePage();
else if (kind === "freund") friendPage();
else message("Seite nicht gefunden", "Bitte den Link aus der Einladung verwenden.");
