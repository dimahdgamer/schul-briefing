import { api } from "../api.js";
import { addDays, daysBetween, errorState, esc, icon, inDays, longDate, query, relativeDay, shortDate, skeleton, toast } from "../ui.js";

export const title = "Aufgaben";

function readPref(key, fallback) {
  try {
    const value = localStorage.getItem(key);
    return value === null ? fallback : value === "1";
  } catch {
    return fallback;
  }
}

function writePref(key, value) {
  try {
    localStorage.setItem(key, value ? "1" : "0");
  } catch {
    // Privater Modus: Einstellung gilt nur für diese Sitzung.
  }
}

function homeworkView(data, hideDone) {
  const today = data.today;
  const items = data.items.filter((h) => h.due >= addDays(today, -14));
  const visible = items.filter((h) => !(hideDone && h.done));
  if (!visible.length) {
    return `<div class="empty reveal"><span class="serif">Nichts zu tun</span>${hideDone && items.length ? "Alle Hausaufgaben sind erledigt." : "Es sind keine Hausaufgaben eingetragen."}</div>`;
  }
  const groups = new Map();
  for (const h of visible) {
    const key = h.due < today ? "older" : h.due;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(h);
  }
  const order = [...groups.keys()].sort((a, b) => (a === "older" ? 1 : b === "older" ? -1 : a.localeCompare(b)));
  return order.map((key, gi) => {
    const label = key === "older" ? "Frühere" : `${relativeDay(key, today)}${daysBetween(today, key) > 1 ? ` · ${shortDate(key)}` : ""}`;
    const rows = groups.get(key).map((h) => `
      <li class="row ${h.done ? "is-done" : ""}">
        <input type="checkbox" class="check" data-hw="${esc(h.id)}" ${h.done ? "checked" : ""} aria-label="${esc(h.subject)} erledigt" />
        <div class="row-main">
          <div class="row-title">${esc(h.subject)}</div>
          <div class="row-text">${esc(h.text)}</div>
          <div class="row-sub">aufgegeben ${esc(shortDate(h.assigned || h.due))}${h.teacher ? ` · ${esc(h.teacher)}` : ""}${h.due_estimated ? " · Fälligkeit geschätzt" : ""}</div>
        </div>
      </li>`).join("");
    return `<section class="section reveal" style="--i:${gi}"><h2 class="section-title">${esc(label)}<span class="aside">${groups.get(key).length}</span></h2><ul class="list">${rows}</ul></section>`;
  }).join("");
}

function examsView(data) {
  const today = data.today;
  const upcoming = data.items.filter((e) => e.date >= today);
  const past = data.items.filter((e) => e.date < today).reverse().slice(0, 8);
  if (!upcoming.length && !past.length) {
    return `<div class="empty reveal"><span class="serif">Keine Arbeiten</span>Im Moment sind keine Klassenarbeiten oder Tests eingetragen.</div>`;
  }
  const card = (e, i) => {
    const days = daysBetween(today, e.date);
    const when = [longDate(e.date), e.hour ? `${e.hour}. Stunde` : e.start ? `${e.start} Uhr` : ""].filter(Boolean).join(", ");
    return `
      <li class="exam ${days <= 3 ? "soon" : ""} reveal" style="--i:${i}">
        <div class="exam-count" aria-hidden="true"><span class="n">${days}</span><span class="u">${days === 1 ? "Tag" : "Tage"}</span></div>
        <div>
          <div class="exam-subject">${esc(e.subject)}</div>
          <div class="row-sub"><span class="tag ${days <= 3 ? "red" : ""}">${esc(e.type)}</span> ${esc(when)}</div>
          ${e.comment ? `<div class="row-text">${esc(e.comment)}</div>` : ""}
          <span class="visually-hidden">${esc(inDays(e.date, today))}</span>
        </div>
      </li>`;
  };
  const pastRows = past.map((e) => `
    <li class="row"><span class="row-icon">${icon("check", "sm")}</span>
      <div class="row-main"><div class="row-title">${esc(e.subject)}</div><div class="row-sub">${esc(e.type)} · ${esc(shortDate(e.date))}</div></div></li>`).join("");
  return `
    ${upcoming.length ? `<ul class="list">${upcoming.map(card).join("")}</ul>` : `<div class="empty"><span class="serif">Nichts in Sicht</span>Keine kommenden Arbeiten eingetragen.</div>`}
    ${past.length ? `<section class="section"><h2 class="section-title">Zuletzt geschrieben</h2><ul class="list">${pastRows}</ul></section>` : ""}`;
}

export async function render(main, params, ctx) {
  const tab = params.tab === "klausuren" ? "klausuren" : "hausaufgaben";
  main.innerHTML = skeleton(4);
  let data;
  try {
    data = await api(tab === "klausuren" ? "/exams" : "/homework");
  } catch (error) {
    if (!ctx.isCurrent()) return;
    main.innerHTML = errorState(error);
    main.querySelector("[data-action=retry]")?.addEventListener("click", () => ctx.rerender());
    return;
  }
  if (!ctx.isCurrent()) return;

  const hideDone = readPref("hideDone", false);
  const openCount = tab === "hausaufgaben" ? data.items.filter((h) => !h.done && h.due >= data.today).length : 0;
  const upcomingCount = tab === "klausuren" ? data.items.filter((e) => e.date >= data.today).length : 0;

  main.innerHTML = `
    <header class="view-head reveal">
      <p class="eyebrow">${tab === "klausuren" ? `${upcomingCount} anstehend` : `${openCount} offen`}</p>
      <h1 class="display">${tab === "klausuren" ? "Klassen&shy;arbeiten" : "Haus&shy;aufgaben"}</h1>
    </header>
    <div class="daynav reveal" style="margin-bottom:8px">
      <div class="segmented" role="group" aria-label="Ansicht">
        <a href="#/aufgaben" ${tab === "hausaufgaben" ? 'aria-current="page"' : ""}>Hausaufgaben</a>
        <a href="#/aufgaben${query({ tab: "klausuren" })}" ${tab === "klausuren" ? 'aria-current="page"' : ""}>Klausuren</a>
      </div>
      ${tab === "hausaufgaben" ? `<button class="btn ghost small" data-action="toggle-done" aria-pressed="${hideDone}">${hideDone ? "Erledigte zeigen" : "Erledigte ausblenden"}</button>` : ""}
    </div>
    <div id="list">${tab === "klausuren" ? examsView(data) : homeworkView(data, hideDone)}</div>`;

  main.querySelector("[data-action=toggle-done]")?.addEventListener("click", () => {
    writePref("hideDone", !hideDone);
    ctx.rerender();
  });

  main.querySelectorAll("[data-hw]").forEach((box) => {
    box.addEventListener("change", async () => {
      box.closest(".row").classList.toggle("is-done", box.checked);
      const item = data.items.find((h) => h.id === box.dataset.hw);
      if (item) item.done = box.checked;
      try {
        await api(`/homework/${encodeURIComponent(box.dataset.hw)}`, { method: "POST", body: { done: box.checked } });
        if (hideDone && box.checked) setTimeout(() => box.closest("li")?.remove(), 450);
      } catch (error) {
        toast(error.message, "error");
      }
    });
  });
}
