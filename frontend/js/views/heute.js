import { api } from "../api.js";
import {
  errorState, esc, icon, inDays, longDate, minutesOf, plural, query, relativeDay,
  shortDate, skeleton, stateTag, timeAgo, toast,
} from "../ui.js";

export const title = "Heute";

function lede(day, data) {
  const brk = day.break;
  if (!day.lessons.length) {
    if (brk && brk.kind !== "weekend") {
      return `${esc(brk.name)}. Die Schule beginnt wieder am ${esc(longDate(brk.back))}.`;
    }
    if (brk) return `Wochenende. Weiter geht es am ${esc(longDate(brk.back))}.`;
    if (!day.in_window) return "Für diesen Tag liegen noch keine Stundenplandaten vor.";
    return "Für diesen Tag ist kein Unterricht eingetragen.";
  }
  if (day.all_cancelled) return "Der gesamte Unterricht fällt aus.";
  const parts = [];
  if (day.late_start && day.first_lesson) {
    parts.push(`Die ersten Stunden fallen aus, du musst erst um <span class="mono">${esc(day.start)}</span> da sein.`);
  } else {
    parts.push(`Unterricht von <span class="mono">${esc(day.start)}</span> bis <span class="mono">${esc(day.end)}</span> Uhr.`);
  }
  if (day.early_end) parts.push(`Früher Schluss um <span class="mono">${esc(day.end)}</span>.`);
  const n = day.changes.length;
  parts.push(n ? `${plural(n, "Änderung", "Änderungen")} im Plan.` : "Alles nach Plan.");
  return parts.join(" ");
}

function lessonRow(lesson, index) {
  const meta = [];
  if (lesson.state === "substitution" && lesson.original_subject && lesson.original_subject !== lesson.subject) {
    meta.push(`<span class="was">statt ${esc(lesson.original_subject)}</span>`);
  }
  if (lesson.teacher) meta.push(`<span>${esc(lesson.teacher)}</span>`);
  if (lesson.room) {
    const was = lesson.state === "room-change" && lesson.original_room ? ` <span class="was">statt ${esc(lesson.original_room)}</span>` : "";
    meta.push(`<span class="mono">${esc(lesson.room)}</span>${was}`);
  }
  return `
    <li class="lesson is-${esc(lesson.state)} reveal" style="--i:${index}" data-start="${esc(lesson.start)}" data-end="${esc(lesson.end)}">
      <div class="lesson-time">
        <span class="lesson-hour">${esc(lesson.hour || "–")}</span>
        <span class="lesson-clock">${esc(lesson.start)}</span>
      </div>
      <div class="lesson-body">
        <div class="lesson-subject">${esc(lesson.subject)}</div>
        <div class="lesson-meta">${meta.join("<span aria-hidden=\"true\">·</span>")}</div>
      </div>
      <div class="lesson-state">${stateTag(lesson.state)}</div>
      ${lesson.comment ? `<div class="lesson-note">${esc(lesson.comment)}</div>` : ""}
      <span class="now-progress" hidden></span>
    </li>`;
}

function lessonList(lessons) {
  const rows = [];
  let previousEnd = null;
  lessons.forEach((lesson, i) => {
    const start = minutesOf(lesson.start);
    if (previousEnd !== null && start !== null && start - previousEnd >= 15 && lesson.state !== "cancelled") {
      const gap = start - previousEnd;
      rows.push(`<li class="gap-row" aria-hidden="true">${gap > 30 ? "Freistunde" : "Pause"} · ${gap} Min.</li>`);
    }
    rows.push(lessonRow(lesson, i));
    if (lesson.state !== "cancelled" && lesson.end) previousEnd = minutesOf(lesson.end);
  });
  return `<ol class="lessons" aria-label="Stunden">${rows.join("")}</ol>`;
}

function tiles(day, data) {
  const active = day.lessons.filter((l) => l.state !== "cancelled");
  const startNote = day.late_start
    ? `statt ${esc(day.planned_start)}`
    : day.first_lesson
      ? `${esc(day.first_lesson.subject)}${day.first_lesson.room ? ` · ${esc(day.first_lesson.room)}` : ""}`
      : "";
  const endNote = day.early_end ? `statt ${esc(day.planned_end)}` : plural(active.length, "Stunde", "Stunden");
  const changeTags = [...new Set(day.changes.map((c) => c.state))].map(stateTag).join(" ");
  const hwOpen = day.homework_due.filter((h) => !h.done);
  const exam = day.exams_today[0] || data.next_exam;

  return `
    <div class="bento">
      <div class="tile wide reveal" style="--i:1">
        <span class="tile-label">Beginn ${day.late_start ? '<span class="tag yellow">später</span>' : ""}</span>
        <span class="tile-value hero">${esc(day.start || "–")}</span>
        <span class="tile-note">${startNote}</span>
      </div>
      <div class="tile reveal" style="--i:2">
        <span class="tile-label">Schluss ${day.early_end ? '<span class="tag green">früher</span>' : ""}</span>
        <span class="tile-value">${esc(day.end || "–")}</span>
        <span class="tile-note">${endNote}</span>
      </div>
      <div class="tile reveal" style="--i:3">
        <span class="tile-label">Änderungen</span>
        <span class="tile-value">${day.changes.length}</span>
        <span class="tile-note">${changeTags || "Alles nach Plan"}</span>
      </div>
      <a class="tile reveal" style="--i:4" href="#/aufgaben">
        <span class="tile-label">Hausaufgaben fällig</span>
        <span class="tile-value">${hwOpen.length}</span>
        <span class="tile-note">${hwOpen.length ? esc([...new Set(hwOpen.map((h) => h.subject))].join(", ")) : "Nichts offen"}</span>
      </a>
      <a class="tile reveal" style="--i:5" href="#/aufgaben?tab=klausuren">
        <span class="tile-label">Nächste Arbeit</span>
        <span class="tile-value" style="font-size:22px;line-height:1.2">${exam ? esc(exam.subject) : "Keine"}</span>
        <span class="tile-note">${exam ? `${esc(exam.type)} · ${esc(inDays(exam.date, day.date))}` : "In den nächsten Wochen nichts eingetragen"}</span>
      </a>
    </div>`;
}

function homeworkSection(day) {
  if (!day.homework_due.length) return "";
  const items = day.homework_due.map((h) => `
    <li class="row ${h.done ? "is-done" : ""}">
      <input type="checkbox" class="check" data-hw="${esc(h.id)}" ${h.done ? "checked" : ""} aria-label="${esc(h.subject)} erledigt" />
      <div class="row-main">
        <div class="row-title">${esc(h.subject)}</div>
        <div class="row-text">${esc(h.text)}</div>
      </div>
    </li>`).join("");
  return `
    <section class="section reveal" style="--i:6">
      <h2 class="section-title">Hausaufgaben für diesen Tag <a class="aside" href="#/aufgaben">alle</a></h2>
      <ul class="list">${items}</ul>
    </section>`;
}

function examsSection(day) {
  const upcoming = day.exams_upcoming.filter((e) => e.date !== day.date).slice(0, 3);
  const today = day.exams_today;
  if (!today.length && !upcoming.length) return "";
  const row = (e, highlight) => `
    <li><a class="row" href="#/aufgaben?tab=klausuren">
      <span class="row-icon ${highlight ? "red" : ""}">${icon("exam")}</span>
      <div class="row-main">
        <div class="row-title">${esc(e.subject)} <span class="tag ${highlight ? "red" : ""}">${esc(e.type)}</span></div>
        <div class="row-sub">${esc(shortDate(e.date))}${e.hour ? `, ${esc(e.hour)}. Stunde` : ""}${e.comment ? ` · ${esc(e.comment)}` : ""}</div>
      </div>
      <span class="row-aside">${esc(inDays(e.date, day.date))}</span>
    </a></li>`;
  return `
    <section class="section reveal" style="--i:7">
      <h2 class="section-title">Klassenarbeiten</h2>
      <ul class="list">${today.map((e) => row(e, true)).join("")}${upcoming.map((e) => row(e, false)).join("")}</ul>
    </section>`;
}

function eventsSection(day) {
  if (!day.events.length) return "";
  const items = day.events.map((ev) => `
    <li class="row">
      <span class="row-icon green">${icon("calendar-dots")}</span>
      <div class="row-main">
        <div class="row-title">${esc(ev.title)}</div>
        <div class="row-sub">${ev.all_day ? "ganztägig" : esc(ev.start.slice(11, 16)) + " Uhr"}${ev.location ? ` · ${esc(ev.location)}` : ""}</div>
      </div>
    </li>`).join("");
  return `
    <section class="section reveal" style="--i:8">
      <h2 class="section-title">Termine</h2>
      <ul class="list">${items}</ul>
    </section>`;
}

function inboxSection(data) {
  if (!data.unread_letters && !data.unread_messages) return "";
  const parts = [];
  if (data.unread_letters) parts.push(plural(data.unread_letters, "ungelesener Elternbrief", "ungelesene Elternbriefe"));
  if (data.unread_messages) parts.push(plural(data.unread_messages, "neue Nachricht", "neue Nachrichten"));
  return `
    <section class="section reveal" style="--i:9">
      <a class="card pad" href="#/post" style="display:flex;align-items:center;gap:14px">
        <span class="row-icon blue">${icon("envelope-simple")}</span>
        <span class="row-main"><span class="row-title">${esc(parts.join(" und "))}</span></span>
        ${icon("caret-right", "sm")}
      </a>
    </section>`;
}

function breakCard(day, data) {
  const brk = day.break;
  if (!brk || day.lessons.length) return "";
  const until = brk.kind === "weekend" ? "" : `<p class="muted" style="margin:6px 0 0">Frei bis ${esc(shortDate(brk.until))}</p>`;
  return `
    <div class="card break-card reveal" style="--i:1">
      <div class="serif">${esc(brk.name)}</div>
      ${until}
      <div class="btn-row" style="margin-top:18px">
        <a class="btn" href="#/heute${query({ date: brk.back })}">Ersten Schultag ansehen ${icon("arrow-right", "sm")}</a>
      </div>
    </div>`;
}

function statusNotice(status) {
  if (!status.last_error) return "";
  return `<div class="notice red reveal" style="margin-bottom:18px">${icon("warning-circle")}<p><strong>Letzter Abruf fehlgeschlagen.</strong> ${esc(status.last_error)}</p></div>`;
}

function updateNow(main, dayIso) {
  const now = new Date();
  const todayIso = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  const minutes = now.getHours() * 60 + now.getMinutes();
  main.querySelectorAll(".lesson").forEach((el) => {
    const start = minutesOf(el.dataset.start);
    const end = minutesOf(el.dataset.end);
    const isNow = dayIso === todayIso && start !== null && end !== null && minutes >= start && minutes < end && !el.classList.contains("is-cancelled");
    el.classList.toggle("is-now", isNow);
    const bar = el.querySelector(".now-progress");
    if (!bar) return;
    bar.hidden = !isNow;
    if (isNow) {
      bar.style.width = "100%";
      bar.style.transform = `scaleX(${(minutes - start) / (end - start)})`;
      const left = end - minutes;
      el.querySelector(".lesson-state").innerHTML = `<span class="tag">noch ${left} Min.</span>`;
    }
  });
}

export async function render(main, params, ctx) {
  main.innerHTML = skeleton(5);
  let data;
  try {
    data = await api(`/overview${query({ date: params.date })}`);
  } catch (error) {
    if (!ctx.isCurrent()) return;
    main.innerHTML = errorState(error);
    main.querySelector("[data-action=retry]")?.addEventListener("click", () => ctx.rerender());
    return;
  }
  if (!ctx.isCurrent()) return;

  const day = data.day;
  const isToday = day.date === data.today;
  const account = data.status.account || {};
  const eyebrow = [
    `<span>${esc(relativeDay(day.date, data.today))}</span>`,
    account.class ? `<span>· Klasse ${esc(account.class)}</span>` : "",
    data.status.demo ? '<span class="tag yellow">Demo</span>' : "",
  ].join("");
  const [weekday, rest] = day.label.split(", ");
  const nav = `
    <nav class="daynav reveal" aria-label="Tag wechseln">
      <a class="btn ghost" href="#/heute${query({ date: data.prev_school_day })}" aria-label="Vorheriger Schultag">${icon("arrow-left", "sm")} ${esc(shortDate(data.prev_school_day))}</a>
      ${isToday ? "" : `<a class="btn ghost" href="#/heute${query({ date: data.today })}">Heute</a>`}
      <a class="btn ghost" href="#/heute${query({ date: data.following_school_day })}" aria-label="Nächster Schultag">${esc(shortDate(data.following_school_day))} ${icon("arrow-right", "sm")}</a>
    </nav>`;

  const holiday = data.holidays.find((h) => h.kind === "school" && h.start > data.today);
  const holidayLine = holiday ? `<span>${esc(holiday.name)} ${esc(inDays(holiday.start, data.today))}</span>` : "";

  main.innerHTML = `
    ${nav}
    <header class="view-head reveal">
      <p class="eyebrow">${eyebrow}</p>
      <h1 class="display">${esc(weekday)}, <em>${esc(rest)}</em></h1>
      <p class="lede">${lede(day, data)}</p>
    </header>
    ${statusNotice(data.status)}
    ${breakCard(day, data)}
    ${day.lessons.length ? tiles(day, data) : ""}
    ${day.lessons.length ? `<section class="section"><h2 class="section-title">Stunden <span class="aside">${esc(day.planned_start)}–${esc(day.planned_end)}</span></h2>${lessonList(day.lessons)}</section>` : ""}
    ${examsSection(day)}
    ${homeworkSection(day)}
    ${eventsSection(day)}
    ${inboxSection(data)}
    <footer class="foot-status">
      <span>Stand ${esc(timeAgo(data.status.last_success))}</span>
      <button type="button" data-action="sync">Jetzt abrufen</button>
      ${holidayLine}
    </footer>`;

  main.querySelectorAll("[data-hw]").forEach((box) => {
    box.addEventListener("change", async () => {
      box.closest(".row").classList.toggle("is-done", box.checked);
      try {
        await api(`/homework/${encodeURIComponent(box.dataset.hw)}`, { method: "POST", body: { done: box.checked } });
      } catch (error) {
        toast(error.message, "error");
      }
    });
  });

  main.querySelector("[data-action=sync]").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    button.textContent = "Wird abgerufen …";
    try {
      const result = await api("/sync", { method: "POST" });
      if (result.ok) toast(result.changes ? plural(result.changes, "Änderung gefunden", "Änderungen gefunden") : "Keine Änderungen");
      else toast(result.error || "Abruf fehlgeschlagen", "error");
      ctx.refreshBadge();
      ctx.rerender();
    } catch (error) {
      toast(error.message, "error");
      button.disabled = false;
      button.textContent = "Jetzt abrufen";
    }
  });

  updateNow(main, day.date);
  const timer = setInterval(() => updateNow(main, day.date), 30_000);
  return () => clearInterval(timer);
}

