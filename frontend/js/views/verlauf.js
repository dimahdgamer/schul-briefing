import { api } from "../api.js";
import { errorState, esc, icon, isoDate, relativeDay, skeleton, timeOf } from "../ui.js";

export const title = "Verlauf";

const CATEGORY = {
  lessons: ["calendar-x", "yellow", "Stundenplan"],
  homework: ["notebook", "", "Hausaufgaben"],
  exams: ["exam", "red", "Klassenarbeit"],
  grades: ["chart-line-up", "green", "Noten"],
  letters: ["envelope-simple", "blue", "Elternbrief"],
  messages: ["chat-circle-text", "blue", "Nachricht"],
  calendar: ["calendar-dots", "green", "Termin"],
  briefing: ["sun-horizon", "", "Briefing"],
  reminder: ["bell-ringing", "red", "Erinnerung"],
  system: ["warning-circle", "red", "System"],
};

function rows(items, todayIso) {
  let lastDay = null;
  const out = [];
  for (const item of items) {
    const day = isoDate(new Date(item.created_at));
    if (day !== lastDay) {
      if (lastDay !== null) out.push("</ul>");
      out.push(`<h2 class="timeline-day">${esc(relativeDay(day, todayIso))}</h2><ul class="list">`);
      lastDay = day;
    }
    const [ic, tone, label] = CATEGORY[item.category] || ["bell-simple", "", item.category];
    const href = item.url && item.url.startsWith("/#/") ? item.url.slice(1) : "";
    const inner = `
      <span class="row-icon ${tone}">${icon(ic)}</span>
      <div class="row-main">
        <div class="row-title">${esc(item.title)}</div>
        <div class="row-text">${esc(item.body)}</div>
      </div>
      <span class="row-aside">${esc(timeOf(item.created_at))}<br><span class="visually-hidden">${esc(label)}</span>${item.pushed ? icon("bell-simple", "sm") : ""}</span>`;
    out.push(`<li>${href ? `<a class="row ${item.seen ? "" : "unseen"}" href="${esc(href)}">${inner}</a>` : `<div class="row ${item.seen ? "" : "unseen"}">${inner}</div>`}</li>`);
  }
  if (lastDay !== null) out.push("</ul>");
  return out.join("");
}

export async function render(main, params, ctx) {
  main.innerHTML = skeleton(6);
  let data;
  try {
    data = await api("/events?limit=40");
  } catch (error) {
    if (!ctx.isCurrent()) return;
    main.innerHTML = errorState(error);
    main.querySelector("[data-action=retry]")?.addEventListener("click", () => ctx.rerender());
    return;
  }
  if (!ctx.isCurrent()) return;

  const todayIso = isoDate(new Date());
  let items = data.items;

  main.innerHTML = `
    <header class="view-head reveal">
      <p class="eyebrow">${data.unseen ? `${data.unseen} neu` : "Alles gesehen"}</p>
      <h1 class="display">Verlauf</h1>
      <p class="lede">Jede erkannte Änderung, jedes Briefing und jede Erinnerung, die neuesten zuerst.</p>
    </header>
    <div id="timeline" class="reveal" style="--i:1">
      ${items.length ? rows(items, todayIso) : '<div class="empty"><span class="serif">Noch ruhig</span>Sobald sich im Schulmanager etwas ändert, steht es hier.</div>'}
    </div>
    ${items.length >= 40 ? '<div class="btn-row" style="justify-content:center;margin-top:20px"><button class="btn" data-action="more">Ältere laden</button></div>' : ""}`;

  if (data.unseen) {
    api("/events/seen", { method: "POST" }).then(() => ctx.refreshBadge()).catch(() => {});
  }

  main.querySelector("[data-action=more]")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      const more = await api(`/events?limit=40&before=${items[items.length - 1].id}`);
      items = items.concat(more.items);
      main.querySelector("#timeline").innerHTML = rows(items, todayIso);
      if (more.items.length < 40) button.remove();
      else button.disabled = false;
    } catch {
      button.disabled = false;
    }
  });
}
