import { api } from "../api.js";
import { errorState, esc, icon, shortDate, skeleton, timeAgo, toast } from "../ui.js";

export const title = "Post";

function letterRow(letter) {
  const meta = [letter.sender, letter.sent_at ? shortDate(letter.sent_at) : ""].filter(Boolean).join(" · ");
  const deadline = letter.deadline ? `<span class="tag yellow">Antwort bis ${esc(shortDate(letter.deadline))}</span>` : "";
  return `
    <li><a class="row" href="${esc(letter.url)}" target="_blank" rel="noopener">
      <span class="row-icon ${letter.unread ? "blue" : ""}">${icon("envelope-simple")}</span>
      <div class="row-main">
        <div class="row-title">${letter.unread ? '<span class="unread-dot" aria-label="ungelesen"></span>' : ""}${esc(letter.title)}</div>
        <div class="row-sub">${esc(meta)} ${deadline}</div>
      </div>
      ${icon("arrow-right", "sm")}
    </a></li>`;
}

function threadRow(thread) {
  // Nachrichten öffnen in der App. Ohne Nummer (Stand von vor dem Update, bis zum nächsten Abruf) geht es nach Schulmanager.
  const inApp = Boolean(thread.subscription_id);
  const link = inApp
    ? `href="#/post?thread=${encodeURIComponent(thread.subscription_id)}"`
    : `href="${esc(thread.url)}" target="_blank" rel="noopener"`;
  return `
    <li><a class="row" ${link}>
      <span class="row-icon ${thread.unread ? "blue" : ""}">${icon("chat-circle-text")}</span>
      <div class="row-main">
        <div class="row-title">${thread.unread ? '<span class="unread-dot" aria-label="ungelesen"></span>' : ""}${esc(thread.subject)}</div>
        <div class="row-sub">${esc(thread.sender)}${thread.preview ? ` · ${esc(thread.preview)}` : ""}</div>
      </div>
      <span class="row-aside">${thread.unread ? `<span class="tag blue">${thread.unread} neu</span>` : esc(timeAgo(thread.last_at))}</span>
    </a></li>`;
}

// ── Eine Unterhaltung ────────────────────────────────────────────────

// Links im Text anklickbar machen. Der Text ist vorher maskiert, die Treffer enthalten also weder Anführungszeichen noch
// Tags; gefunden wird nur http(s).
const URL_PATTERN = /https?:\/\/(?:(?!&quot;|&#39;|&lt;|&gt;)\S)+/g;

function linkify(text) {
  return esc(text).replace(URL_PATTERN, (match) => {
    const url = match.replace(/[.,;:!?)\]]+$/, "");
    return `<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>${match.slice(url.length)}`;
  });
}

function when(timestamp) {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return "";
  const sameYear = date.getFullYear() === new Date().getFullYear();
  const day = date.toLocaleDateString("de-DE", { weekday: "short", day: "2-digit", month: "2-digit", ...(sameYear ? {} : { year: "2-digit" }) });
  return `${day} · ${date.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })}`;
}

// Diese Typen zeigt der Server direkt an (Bilder als Vorschau, PDF im Browser), alles andere lädt man herunter.
const PREVIEW_IMAGES = ["image/jpeg", "image/png", "image/gif", "image/webp"];

function fileSize(bytes) {
  if (!bytes) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toLocaleString("de-DE", { maximumFractionDigits: 1 })} MB`;
}

function fileUrl(subscriptionId, message, file) {
  return `/api/thread/${encodeURIComponent(subscriptionId)}/file/${encodeURIComponent(message.id)}/${encodeURIComponent(file.id)}`;
}

function fileChip(subscriptionId, message, file) {
  const viewable = file.type === "application/pdf";
  const href = fileUrl(subscriptionId, message, file) + (viewable ? "" : "?download=1");
  return `
    <a class="msg-file" href="${esc(href)}" target="_blank" rel="noopener" title="${viewable ? "Ansehen" : "Herunterladen"}">
      ${icon(viewable ? "eye" : "download-simple", "sm")}<span class="name">${esc(file.name)}</span><span class="size">${esc(fileSize(file.size))}</span>
    </a>`;
}

function fileBlock(subscriptionId, message, file) {
  if (!PREVIEW_IMAGES.includes(file.type)) return fileChip(subscriptionId, message, file);
  const url = esc(fileUrl(subscriptionId, message, file));
  return `
    <a class="msg-image" href="${url}" target="_blank" rel="noopener" data-chip="${esc(`${message.id}/${file.id}`)}">
      <img src="${url}" alt="${esc(file.name)}" loading="lazy" />
    </a>`;
}

function messageBlock(message, subscriptionId) {
  const files = message.attachments.length
    ? `<div class="msg-files">${message.attachments.map((file) => fileBlock(subscriptionId, message, file)).join("")}</div>`
    : "";
  return `
    <article class="msg${message.mine ? " mine" : ""}">
      <div class="msg-head">
        <span class="msg-name">${message.mine ? "Ich" : esc(message.sender || "Unbekannt")}</span>
        <time class="msg-time" datetime="${esc(message.at)}">${esc(when(message.at))}</time>
      </div>
      <div class="msg-text">${linkify(message.text)}</div>
      ${files}
    </article>`;
}

const BACK = `<div class="thread-nav"><a class="btn ghost small" href="#/post">${icon("arrow-left", "sm")}Post</a></div>`;

async function renderThread(main, subscriptionId, ctx) {
  main.innerHTML = skeleton(4);
  const path = `/thread/${encodeURIComponent(subscriptionId)}`;
  let data;
  try {
    data = await api(path);
  } catch (error) {
    if (!ctx.isCurrent()) return;
    main.innerHTML = BACK + errorState(error);
    main.querySelector("[data-action=retry]")?.addEventListener("click", () => ctx.rerender());
    return;
  }
  if (!ctx.isCurrent()) return;

  const thread = data.thread;
  let items = data.items;
  let more = data.more;
  const meta = [thread.sender && `Von ${thread.sender}`, thread.recipients && `an ${thread.recipients}`].filter(Boolean).join(" ");

  main.innerHTML = `
    <div class="reveal">${BACK}</div>
    <header class="page-head reveal"><h1 class="page-title thread-title">${esc(thread.subject)}</h1></header>
    ${meta ? `<p class="thread-meta reveal">${esc(meta)}</p>` : ""}
    <div class="thread-more" hidden><button class="btn small" type="button">Ältere Nachrichten laden</button></div>
    <div class="msgs"></div>
    ${thread.can_answer ? COMPOSE : '<p class="compose-off">In dieser Unterhaltung sind keine Antworten möglich.</p>'}
    <div class="btn-row thread-actions">
      <a class="btn small" href="${esc(thread.url)}" target="_blank" rel="noopener">${icon("arrow-right", "sm")}In Schulmanager öffnen</a>
    </div>
    <p class="foot-status">Beim Öffnen gilt die Unterhaltung in Schulmanager als gelesen.</p>`;

  const list = main.querySelector(".msgs");
  const moreBox = main.querySelector(".thread-more");
  const moreButton = moreBox.querySelector("button");

  function draw() {
    list.innerHTML = items.length
      ? items.map((message) => messageBlock(message, thread.subscription_id)).join("")
      : '<div class="empty">Keine Nachrichten in dieser Unterhaltung.</div>';
    moreBox.hidden = !more;
    // Lässt sich ein Bild nicht laden (zu groß, nicht mehr vorhanden), bleibt der Name als Link zum Herunterladen
    list.querySelectorAll(".msg-image img").forEach((img) => {
      img.addEventListener("error", () => {
        const [messageId, fileId] = img.closest(".msg-image").dataset.chip.split("/");
        const message = items.find((m) => m.id === messageId);
        const file = message?.attachments.find((f) => f.id === fileId);
        if (file) img.closest(".msg-image").outerHTML = fileChip(thread.subscription_id, message, { ...file, type: "" });
      }, { once: true });
    });
  }

  moreButton.addEventListener("click", async () => {
    moreButton.disabled = true;
    try {
      const older = await api(`${path}?before=${encodeURIComponent(items[0].id)}`);
      if (!ctx.isCurrent()) return;
      const height = document.documentElement.scrollHeight;
      items = [...older.items, ...items];
      more = older.more;
      draw();
      window.scrollBy(0, document.documentElement.scrollHeight - height); // der Text, den man gerade liest, bleibt stehen
    } catch (error) {
      toast(error.message || "Konnte nicht geladen werden.", "error");
    }
    moreButton.disabled = false;
  });

  draw();
  requestAnimationFrame(() => list.lastElementChild?.scrollIntoView({ block: "end" })); // die neueste Nachricht zuerst

  // Wie in Schulmanager: Wer die Unterhaltung öffnet, hat sie gelesen. Markiert wird bis zur neuesten angezeigten Nachricht.
  if (thread.unread > 0 && items.length) {
    const foot = main.querySelector(".foot-status");
    api(`${path}/read`, { method: "POST", body: { message_id: items[items.length - 1].id } })
      .then(() => {
        foot.textContent = "In Schulmanager als gelesen markiert.";
      })
      .catch((error) => toast(error.message || "Konnte nicht als gelesen markiert werden.", "error"));
  }

  if (thread.can_answer) wireCompose();

  // ── Antworten ──
  // Enter macht eine neue Zeile (auf dem Handy und am Rechner), gesendet wird nur mit dem Knopf oder Strg/Cmd+Enter,
  // und immer erst nach einer Bestätigung: Eine gesendete Nachricht lässt sich nicht zurückholen.
  function wireCompose() {
    const form = main.querySelector(".compose");
    const input = form.querySelector("textarea");
    const send = form.querySelector("[type=submit]");
    const count = form.querySelector(".compose-count");
    const draftKey = `reply-draft:${thread.subscription_id}`;
    let attempt = null; // derselbe Text behält seine request_id, so sendet ein wiederholter Versuch nie doppelt

    const draft = {
      load: () => { try { return sessionStorage.getItem(draftKey) || ""; } catch { return ""; } },
      save: (text) => { try { text ? sessionStorage.setItem(draftKey, text) : sessionStorage.removeItem(draftKey); } catch { /* Entwurf ist nur Komfort */ } },
    };

    function refresh() {
      send.disabled = !input.value.trim();
      input.style.height = "auto";
      input.style.height = `${Math.min(input.scrollHeight + 2, 260)}px`;
      const length = input.value.length;
      count.hidden = length < 2500;
      count.textContent = `${length.toLocaleString("de-DE")} / ${MESSAGE_LIMIT.toLocaleString("de-DE")}`;
    }

    input.value = draft.load();
    refresh();
    input.addEventListener("input", () => {
      draft.save(input.value);
      refresh();
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        form.requestSubmit();
      }
    });

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const text = input.value.trim();
      if (!text) return;
      if (!attempt || attempt.text !== text) attempt = { text, id: newRequestId() };
      confirmSend({
        thread,
        text,
        onSend: async () => {
          const result = await api(`${path}/send`, { method: "POST", body: { text, request_id: attempt.id } });
          if (!ctx.isCurrent()) return;
          attempt = null;
          input.value = "";
          draft.save("");
          refresh();
          if (!result.message) return ctx.rerender(); // unerwartete Antwort von Schulmanager: Unterhaltung neu laden
          if (!items.some((m) => m.id === result.message.id)) items = [...items, result.message];
          draw();
          requestAnimationFrame(() => list.lastElementChild?.scrollIntoView({ block: "end" }));
          toast(result.duplicate ? "Diese Nachricht war schon gesendet." : "Nachricht gesendet.");
        },
      });
    });
  }
}

const MESSAGE_LIMIT = 3000; // wie bei Schulmanager

const COMPOSE = `
  <form class="compose" novalidate>
    <label class="field-label" for="reply-text">Antworten</label>
    <textarea id="reply-text" class="input compose-text" rows="3" maxlength="${MESSAGE_LIMIT}" placeholder="Antwort schreiben …" autocomplete="off"></textarea>
    <div class="compose-foot">
      <span class="compose-hint">Enter macht eine neue Zeile. Gesendet wird erst nach deiner Bestätigung.</span>
      <span class="compose-count" hidden></span>
      <button type="submit" class="btn primary small" disabled>Senden …</button>
    </div>
  </form>`;

function newRequestId() {
  if (globalThis.crypto?.randomUUID) return crypto.randomUUID();
  return Array.from({ length: 24 }, () => Math.floor(Math.random() * 36).toString(36)).join("");
}

// Bestätigung vor dem Senden: Unterhaltung, Empfänger und der Text so, wie er ankommt. `onSend` wirft bei Fehlern,
// die Meldung erscheint dann im Fenster und der Text bleibt erhalten.
function confirmSend({ thread, text, onSend }) {
  const to = thread.recipients || thread.sender || "";
  const dialog = document.createElement("dialog");
  dialog.className = "sheet";
  dialog.setAttribute("aria-labelledby", "send-title");
  dialog.tabIndex = -1;
  dialog.innerHTML = `
    <div class="sheet-body">
      <header class="sheet-head">
        <h2 id="send-title">Nachricht senden?</h2>
        <button type="button" class="btn ghost small" data-act="cancel">Abbrechen</button>
      </header>
      <p class="sheet-error" role="alert" hidden></p>
      <dl class="send-facts">
        <div><dt>Unterhaltung</dt><dd>${esc(thread.subject)}</dd></div>
        ${to ? `<div><dt>Empfänger</dt><dd>${esc(to)}</dd></div>` : ""}
      </dl>
      <div class="send-preview">${esc(text)}</div>
      <p class="send-note">Eine gesendete Nachricht lässt sich nicht zurückholen.</p>
      <div class="sheet-actions"><span></span><button type="button" class="btn primary" data-act="send">Jetzt senden</button></div>
    </div>`;
  document.body.appendChild(dialog);

  const error = dialog.querySelector(".sheet-error");
  const sendButton = dialog.querySelector('[data-act="send"]');
  const cancelButton = dialog.querySelector('[data-act="cancel"]');
  let sending = false;
  const close = () => {
    dialog.close();
    dialog.remove();
  };

  cancelButton.addEventListener("click", close);
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog && !sending) close();
  });
  dialog.addEventListener("cancel", (event) => {
    if (sending) event.preventDefault(); // Esc während des Sendens ignorieren
  });
  dialog.addEventListener("close", () => dialog.remove());
  sendButton.addEventListener("click", async () => {
    if (sending) return;
    sending = true;
    error.hidden = true;
    sendButton.disabled = cancelButton.disabled = true;
    sendButton.textContent = "Sende …";
    try {
      await onSend();
      close();
    } catch (failure) {
      error.textContent = failure.message || "Das hat nicht geklappt.";
      error.hidden = false;
      sendButton.textContent = "Erneut senden";
      sendButton.disabled = cancelButton.disabled = false;
    } finally {
      sending = false;
    }
  });

  dialog.showModal();
  dialog.focus({ preventScroll: true });
}

// ── Liste ────────────────────────────────────────────────────────────

export async function render(main, params, ctx) {
  if (params.thread) return renderThread(main, params.thread, ctx);

  main.innerHTML = skeleton(5);
  let data;
  try {
    data = await api("/inbox");
  } catch (error) {
    if (!ctx.isCurrent()) return;
    main.innerHTML = errorState(error);
    main.querySelector("[data-action=retry]")?.addEventListener("click", () => ctx.rerender());
    return;
  }
  if (!ctx.isCurrent()) return;

  const unreadLetters = data.letters.filter((l) => l.unread).length;
  const unreadMessages = data.threads.reduce((n, t) => n + t.unread, 0);

  main.innerHTML = `
    <header class="page-head reveal">
      <h1 class="page-title">Post</h1>
      ${unreadLetters + unreadMessages ? `<span class="aside">${unreadLetters + unreadMessages} ungelesen</span>` : ""}
    </header>
    <section class="section reveal" style="--i:1">
      <h2 class="section-title">Elternbriefe <span class="aside">${data.letters.length}</span></h2>
      ${data.letters.length ? `<ul class="list">${data.letters.slice(0, 30).map(letterRow).join("")}</ul>` : '<div class="empty">Keine Briefe vorhanden.</div>'}
    </section>
    <section class="section reveal" style="--i:2">
      <h2 class="section-title">Nachrichten <span class="aside">${data.threads.length}</span></h2>
      ${data.threads.length ? `<ul class="list">${data.threads.slice(0, 30).map(threadRow).join("")}</ul>` : '<div class="empty">Keine Nachrichten vorhanden.</div>'}
    </section>
    <p class="foot-status">Nachrichten öffnen hier und gelten in Schulmanager dann als gelesen. Briefe öffnen in Schulmanager, erst dort gilt ein Brief als gelesen.</p>`;
}
