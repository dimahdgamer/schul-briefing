import { api } from "../api.js";
import { errorState, esc, icon, shortDate, skeleton, timeAgo } from "../ui.js";

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
  return `
    <li><a class="row" href="${esc(thread.url)}" target="_blank" rel="noopener">
      <span class="row-icon ${thread.unread ? "blue" : ""}">${icon("chat-circle-text")}</span>
      <div class="row-main">
        <div class="row-title">${thread.unread ? '<span class="unread-dot" aria-label="ungelesen"></span>' : ""}${esc(thread.subject)}</div>
        <div class="row-sub">${esc(thread.sender)}${thread.preview ? ` · ${esc(thread.preview)}` : ""}</div>
      </div>
      <span class="row-aside">${thread.unread ? `<span class="tag blue">${thread.unread} neu</span>` : esc(timeAgo(thread.last_at))}</span>
    </a></li>`;
}

export async function render(main, params, ctx) {
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
    <p class="foot-status">Öffnet in Schulmanager, erst dort gilt ein Brief als gelesen.</p>`;
}
