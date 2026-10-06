import { api } from "../api.js";
import { editCourse } from "../forms.js";
import { addDays, errorState, esc, icon, isoWeek, mondayOf, query, skeleton } from "../ui.js";

export const title = "Woche";

function dayColumn(day, todayIso, index) {
  const [weekday] = day.label.split(", ");
  const isToday = day.date === todayIso;
  const head = `<h3>${esc(weekday)} <span class="mono">${isToday ? '<span class="today-mark">heute</span> ' : ""}${esc(day.short.split(" ")[1])}</span></h3>`;
  let body;
  if (day.holiday && !day.lessons.length) {
    body = `<div class="week-holiday">${esc(day.holiday)}</div>`;
  } else if (!day.lessons.length && !day.exams.length && !day.events.length && !day.leaves.length) {
    body = `<div class="week-holiday">Kein Unterricht eingetragen</div>`;
  } else {
    const exams = day.exams.map((e) => `
      <li class="exam-row"><span class="h">${icon("exam", "sm")}</span><span class="s">${esc(e.subject)}</span><span class="r">${esc([e.type, e.hour ? e.hour + ". Std" : e.manual && e.end ? `${e.start}–${e.end}` : e.start].filter(Boolean).join(" · "))}</span></li>`).join("");
    // Eine Beurlaubung ohne Stundenplan-Daten (weit voraus) soll trotzdem sichtbar sein
    const leaves = day.leaves
      .filter((l) => !day.lessons.some((x) => x.state === "leave"))
      .map((l) => `<li class="is-leave"><span class="h">${icon("door-open", "sm")}</span><span class="s">Beurlaubt</span><span class="r">${esc(l.hours || "ganztägig")}</span></li>`).join("");
    const events = day.events.map((ev) => `
      <li><span class="h">${icon("calendar-dots", "sm")}</span><span class="s">${esc(ev.title)}</span><span class="r">${ev.all_day ? "" : esc(ev.start.slice(11, 16))}</span></li>`).join("");
    const lessons = day.lessons.map((l) => {
      const label = l.state === "cancelled" ? "Entfall" : l.state === "eva" ? "EVA" : l.state === "leave" ? "Beurlaubt" : l.state === "substitution" ? `Vertr. ${l.teacher || ""}` : l.room;
      return `<li class="is-${esc(l.state)}" title="${esc([l.subject, l.teacher, l.room].filter(Boolean).join(" · "))}">
        <span class="h">${esc(l.hour)}</span><span class="s">${esc(l.subject)}</span><span class="r">${esc(label || "")}</span></li>`;
    }).join("");
    body = `<ul class="mini">${events}${exams}${leaves}${lessons}</ul>`;
  }
  return `<div class="week-day reveal ${isToday ? "is-today" : ""}" style="--i:${index}" id="d-${esc(day.date)}">
    <a href="#/heute${query({ date: day.date })}" style="text-decoration:none;color:inherit">${head}</a>${body}</div>`;
}

export async function render(main, params, ctx) {
  main.innerHTML = skeleton(5);
  let data;
  try {
    data = await api(`/week${query({ start: params.start })}`);
  } catch (error) {
    if (!ctx.isCurrent()) return;
    main.innerHTML = errorState(error);
    main.querySelector("[data-action=retry]")?.addEventListener("click", () => ctx.rerender());
    return;
  }
  if (!ctx.isCurrent()) return;

  const monday = data.monday;
  const thisMonday = mondayOf(data.today);
  const friday = addDays(monday, 4);
  const changes = data.days.reduce((n, d) => n + d.lessons.filter((l) => !["regular", "leave"].includes(l.state)).length, 0);
  const fmt = (iso) => `${iso.slice(8, 10)}.${iso.slice(5, 7)}.`;

  main.innerHTML = `
    <nav class="daynav reveal" aria-label="Woche wechseln">
      <a class="btn ghost" href="#/woche${query({ start: addDays(monday, -7) })}">${icon("arrow-left", "sm")} Vorherige</a>
      ${monday !== thisMonday ? `<a class="btn ghost" href="#/woche">Diese Woche</a>` : ""}
      <a class="btn ghost" href="#/woche${query({ start: addDays(monday, 7) })}">Nächste ${icon("arrow-right", "sm")}</a>
    </nav>
    <header class="view-head reveal">
      <p class="eyebrow"><span>KW ${isoWeek(monday)}</span><span>· ${fmt(monday)}–${fmt(friday)}</span>${data.source === "live" ? '<span class="tag blue">live geladen</span>' : ""}</p>
      <h1 class="display">${monday === thisMonday ? "Diese Woche" : monday > thisMonday ? "Kommende Woche" : "Vergangene Woche"}</h1>
      <p class="lede">${changes ? `${changes} ${changes === 1 ? "Änderung" : "Änderungen"} gegenüber dem regulären Plan.` : "Keine Änderungen gegenüber dem regulären Plan."}</p>
    </header>
    <div class="week">${data.days.map((d, i) => dayColumn(d, data.today, i)).join("")}</div>
    ${data.has_courses ? "" : `<p class="muted" style="margin-top:22px">Fehlt Unterricht, den der Schulmanager nicht kennt (z. B. ein Kurs an einer anderen Schule)? <button type="button" class="btn small" style="margin-top:8px" data-action="add-course">+ Eigenen Unterricht eintragen</button></p>`}`;
  main.querySelector("[data-action=add-course]")?.addEventListener("click", () => editCourse(null, ctx));

  if (monday === thisMonday && window.matchMedia("(max-width: 959px)").matches) {
    const today = main.querySelector(`#d-${CSS.escape(data.today)}`);
    if (today && data.days[0].date !== data.today) today.scrollIntoView({ block: "start", behavior: "smooth" });
  }
}
