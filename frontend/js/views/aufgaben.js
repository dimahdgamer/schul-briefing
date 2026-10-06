import { api } from "../api.js";
import { openSheet } from "../sheet.js";
import { addDays, daysBetween, errorState, esc, icon, inDays, longDate, plural, query, relativeDay, shortDate, skeleton, timeAgo, toast } from "../ui.js";

export const title = "Aufgaben";

const TABS = ["hausaufgaben", "klausuren", "beurlaubung", "fehlzeiten"];
const EXAM_TYPES = ["Klausur", "Test", "Klassenarbeit"];

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
          <div class="row-sub">aufgegeben ${esc(shortDate(h.assigned || h.due))}${h.teacher ? ` · ${esc(h.teacher)}` : ""}${h.due_estimated ? " · Fälligkeit geschätzt" : ""}${h.eva ? ' · <span class="tag violet">EVA</span>' : ""}</div>
        </div>
      </li>`).join("");
    return `<section class="section reveal" style="--i:${gi}"><h2 class="section-title">${esc(label)}<span class="aside">${groups.get(key).length}</span></h2><ul class="list">${rows}</ul></section>`;
  }).join("");
}

function examTime(e) {
  if (e.hour) return `${e.hour}. Stunde`;
  if (e.start && e.end) return `${e.start}–${e.end} Uhr`;
  return e.start ? `${e.start} Uhr` : "";
}

function examsView(data) {
  const today = data.today;
  const upcoming = data.items.filter((e) => e.date >= today);
  const past = data.items.filter((e) => e.date < today).reverse().slice(0, 8);
  if (!upcoming.length && !past.length) {
    return `<div class="empty reveal"><span class="serif">Keine Arbeiten</span>Im Moment sind keine Klassenarbeiten oder Tests eingetragen. Klausuren kannst du oben selbst eintragen.</div>`;
  }
  const edit = (e) => (e.manual
    ? `<div class="exam-actions"><span class="own-tag">selbst eingetragen</span> <button class="btn ghost small" data-edit-exam="${esc(e.id)}">Bearbeiten</button></div>`
    : "");
  const card = (e, i) => {
    const days = daysBetween(today, e.date);
    const when = [longDate(e.date), examTime(e)].filter(Boolean).join(", ");
    return `
      <li class="exam ${days <= 3 ? "soon" : ""} reveal" style="--i:${i}">
        <div class="exam-count" aria-hidden="true"><span class="n">${days}</span><span class="u">${days === 1 ? "Tag" : "Tage"}</span></div>
        <div>
          <div class="exam-subject">${esc(e.subject)}</div>
          <div class="row-sub"><span class="tag ${days <= 3 ? "red" : ""}">${esc(e.type)}</span> ${esc(when)}</div>
          ${e.comment ? `<div class="row-text">${esc(e.comment)}</div>` : ""}
          <span class="visually-hidden">${esc(inDays(e.date, today))}</span>
          ${edit(e)}
        </div>
      </li>`;
  };
  const pastRows = past.map((e) => `
    <li class="row"><span class="row-icon">${icon("check", "sm")}</span>
      <div class="row-main"><div class="row-title">${esc(e.subject)}</div><div class="row-sub">${esc(e.type)} · ${esc(shortDate(e.date))}</div></div>
      ${e.manual ? `<button class="btn ghost small" data-edit-exam="${esc(e.id)}">Bearbeiten</button>` : ""}</li>`).join("");
  return `
    ${upcoming.length ? `<ul class="list">${upcoming.map(card).join("")}</ul>` : `<div class="empty"><span class="serif">Nichts in Sicht</span>Keine kommenden Arbeiten eingetragen.</div>`}
    ${past.length ? `<section class="section"><h2 class="section-title">Zuletzt geschrieben</h2><ul class="list">${pastRows}</ul></section>` : ""}`;
}

// ── Beurlaubungen ────────────────────────────────────────────────────

function hoursText(l) {
  if (!l.hour_from) return "";
  return l.hour_to && l.hour_to !== l.hour_from ? `${l.hour_from}.–${l.hour_to}. Stunde` : `${l.hour_from}. Stunde`;
}

function leaveDates(l) {
  return l.from === l.to ? longDate(l.from) : `${shortDate(l.from)} bis ${shortDate(l.to)}`;
}

function leavesView(data) {
  const today = data.today;
  const sorted = [...data.leaves].sort((a, b) => a.from.localeCompare(b.from));
  const upcoming = sorted.filter((l) => l.to >= today);
  const past = sorted.filter((l) => l.to < today).reverse().slice(0, 8);
  if (!upcoming.length && !past.length) {
    return `<div class="empty reveal"><span class="serif">Keine Beurlaubungen</span>Trage hier ein, wenn die Schule dich freistellt. Dann weiß die App, dass du nicht hin musst, und schickt an diesen Tagen kein Morgen-Briefing.</div>`;
  }
  const row = (l, i) => `
    <li class="row reveal" style="--i:${i}">
      <span class="row-icon blue">${icon("door-open")}</span>
      <div class="row-main">
        <div class="row-title">${esc(leaveDates(l))}</div>
        <div class="row-sub">${esc([hoursText(l) || "ganztägig", l.reason].filter(Boolean).join(" · "))}</div>
      </div>
      <span class="row-aside">${l.to < today ? "" : l.from > today ? esc(inDays(l.from, today)) : "läuft"}</span>
      <button class="btn ghost small" data-edit-leave="${esc(l.id)}">Bearbeiten</button>
    </li>`;
  return `
    ${upcoming.length ? `<ul class="list">${upcoming.map(row).join("")}</ul>` : `<div class="empty"><span class="serif">Nichts geplant</span>Keine kommende Beurlaubung eingetragen.</div>`}
    ${past.length ? `<section class="section"><h2 class="section-title">Vergangene</h2><ul class="list">${past.map(row).join("")}</ul></section>` : ""}`;
}

// ── Fehlzeiten aus dem Klassenbuch ───────────────────────────────────

function markTag(mark) {
  return `<span class="tag ${mark === "Unentschuldigt" ? "red" : "green"}">${esc(mark)}</span>`;
}

function absenceRow(a, i) {
  return `
    <li class="row reveal" style="--i:${i}">
      <span class="row-icon ${a.unexcused ? "red" : "green"}">${icon(a.unexcused ? "warning-circle" : "check")}</span>
      <div class="row-main">
        <div class="row-title">${esc(shortDate(a.date))} <span class="muted">${esc(a.span)}</span></div>
        <div class="row-sub">${esc(a.info.join(" · "))}</div>
      </div>
      <div class="btn-row" style="justify-content:flex-end">${a.marks.map(markTag).join("")}</div>
    </li>`;
}

function absencesView(data, onlyUnexcused) {
  if (!data.available) {
    const text = data.reason === "disabled"
      ? "Deine Schule gibt die Fehlzeiten für Schüler nicht frei, oder das Klassenbuch ist dort nicht aktiv. Dann gibt es nichts abzurufen."
      : data.reason === "error"
        ? `Der Abruf hat nicht geklappt (${esc(data.error)}). Die App versucht es später noch einmal.`
        : "Die Fehlzeiten werden beim nächsten Abruf geladen. Das dauert höchstens ein paar Minuten.";
    return `<div class="empty reveal"><span class="serif">Keine Fehlzeiten-Daten</span>${text}</div>`;
  }
  const totals = data.totals;
  const open = data.items.filter((a) => a.unexcused);
  const done = data.items.filter((a) => !a.unexcused);
  const tiles = `
    <div class="bento reveal">
      <div class="tile">
        <span class="tile-label">Fehlstunden</span>
        <span class="tile-value">${totals.absent}</span>
        <span class="tile-note">${totals.total ? `von ${totals.total} Stunden` : "dieses Schuljahr"}</span>
      </div>
      <div class="tile">
        <span class="tile-label">Unentschuldigt</span>
        <span class="tile-value">${totals.unexcused}</span>
        <span class="tile-note">${data.has_list ? `${plural(data.unexcused_entries, "Eintrag", "Einträge")} offen` : "Stunden"}</span>
      </div>
    </div>`;
  const noList = data.has_list ? "" : `<p class="muted reveal">Die Liste der einzelnen Fehlzeiten ist bei deiner Schule nicht abrufbar, hier steht nur die Statistik.</p>`;
  const openSection = open.length
    ? `<section class="section"><h2 class="section-title">Noch nicht entschuldigt<span class="aside">${open.length}</span></h2>
        <ul class="list">${open.map(absenceRow).join("")}</ul></section>`
    : data.has_list ? `<div class="notice green reveal">${icon("check")}<p>Alle Fehlzeiten sind entschuldigt.</p></div>` : "";
  const doneSection = !onlyUnexcused && done.length
    ? `<section class="section"><h2 class="section-title">Entschuldigt<span class="aside">${done.length}</span></h2>
        <ul class="list">${done.map(absenceRow).join("")}</ul></section>`
    : "";
  const subjects = data.by_subject.length
    ? `<details class="guest-help"><summary>Fehlstunden nach Fach</summary>
        <table class="data-table" style="margin-top:10px"><thead><tr><th>Fach</th><th class="num">Fehlstunden</th><th class="num">unentschuldigt</th></tr></thead>
        <tbody>${data.by_subject.map((s) => `<tr><td>${esc(s.subject)}</td><td class="num">${s.absent} von ${s.total}</td><td class="num">${s.unexcused}</td></tr>`).join("")}</tbody></table>
      </details>`
    : "";
  return `${tiles}${noList}${openSection}${doneSection}${subjects}
    <p class="muted" style="margin-top:18px">Stand ${esc(timeAgo(data.fetched_at))}, laut Klassenbuch im Schulmanager.</p>`;
}

// ── Eingabe ──────────────────────────────────────────────────────────

async function loadOwn() {
  try {
    return await api("/own");
  } catch (error) {
    toast(error.message, "error");
    return null;
  }
}

function addMinutes(hhmm, minutes) {
  const [h, m] = hhmm.split(":").map(Number);
  const total = h * 60 + m + minutes;
  if (total >= 24 * 60) return "";
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}

export async function editExam(existing, ctx) {
  const own = await loadOwn();
  if (!own) return;
  let endTouched = Boolean(existing);
  openSheet({
    title: existing ? "Klausur bearbeiten" : "Klausur eintragen",
    fields: [
      { name: "subject", label: "Fach", required: true, list: own.subjects, maxlength: 60, placeholder: "z. B. Mathematik" },
      { name: "date", label: "Datum", type: "date", required: true },
      { name: "start", label: "Von", type: "time", required: true, half: true },
      { name: "end", label: "Bis", type: "time", required: true, half: true },
      { name: "type", label: "Art", list: EXAM_TYPES, maxlength: 30 },
      { name: "comment", label: "Notiz (optional)", maxlength: 200, placeholder: "z. B. Raum" },
    ],
    values: existing || { date: own.today, type: "Klausur" },
    submitLabel: existing ? "Speichern" : "Eintragen",
    onChange: ({ name, values, set }) => {
      if (name === "end") endTouched = true;
      // Klausuren dauern meist zwei Stunden: das Ende vorschlagen, solange man es nicht selbst ändert
      if (name === "start" && !endTouched && values.start) set("end", addMinutes(values.start, 90));
    },
    onSubmit: async (values) => {
      await api(existing ? `/own/exams/${existing.id}` : "/own/exams", { method: existing ? "PUT" : "POST", body: values });
      toast(existing ? "Klausur gespeichert" : "Klausur eingetragen");
      ctx.rerender();
    },
    onDelete: existing
      ? async () => {
        await api(`/own/exams/${existing.id}`, { method: "DELETE" });
        toast("Klausur gelöscht");
        ctx.rerender();
      }
      : undefined,
  });
}

export async function editLeave(existing, ctx) {
  const own = await loadOwn();
  if (!own) return;
  const singleDay = (v) => !v.to || v.to === v.from;
  openSheet({
    title: existing ? "Beurlaubung bearbeiten" : "Beurlaubung eintragen",
    fields: [
      { name: "from", label: "Von", type: "date", required: true, half: true },
      { name: "to", label: "Bis", type: "date", half: true, help: "Leer lassen für einen einzelnen Tag" },
      {
        name: "hour_from", label: "Ab", type: "select", half: true, visible: singleDay,
        options: [["", "Ganzer Tag"], ...own.hours.map((h) => [h.hour, `${h.hour}. Stunde`])],
      },
      {
        name: "hour_to", label: "Bis einschließlich", type: "select", half: true,
        visible: (v) => singleDay(v) && Boolean(v.hour_from),
        options: own.hours.map((h) => [h.hour, `${h.hour}. Stunde`]),
      },
      { name: "reason", label: "Grund (optional)", maxlength: 200, placeholder: "z. B. Arzttermin" },
    ],
    values: existing
      ? { ...existing, to: existing.to === existing.from ? "" : existing.to }
      : { from: own.today, to: "", hour_from: "", hour_to: "1" },
    submitLabel: existing ? "Speichern" : "Eintragen",
    onChange: ({ name, values, set }) => {
      // Beginnt die Beurlaubung in einer Stunde, soll sie nicht davor enden
      if (name === "hour_from" && values.hour_from && (!values.hour_to || Number(values.hour_to) < Number(values.hour_from))) {
        set("hour_to", values.hour_from);
      }
    },
    onSubmit: async (values) => {
      await api(existing ? `/own/leaves/${existing.id}` : "/own/leaves", { method: existing ? "PUT" : "POST", body: values });
      toast(existing ? "Beurlaubung gespeichert" : "Beurlaubung eingetragen");
      ctx.rerender();
    },
    onDelete: existing
      ? async () => {
        await api(`/own/leaves/${existing.id}`, { method: "DELETE" });
        toast("Beurlaubung gelöscht");
        ctx.rerender();
      }
      : undefined,
  });
}

// ── Seite ────────────────────────────────────────────────────────────

const LABELS = { hausaufgaben: "Hausaufgaben", klausuren: "Klausuren", beurlaubung: "Beurlaubung", fehlzeiten: "Fehlzeiten" };
const HEADINGS = { hausaufgaben: "Haus&shy;aufgaben", klausuren: "Klassen&shy;arbeiten", beurlaubung: "Beur&shy;laubung", fehlzeiten: "Fehl&shy;zeiten" };

export async function render(main, params, ctx) {
  const tab = TABS.includes(params.tab) ? params.tab : "hausaufgaben";
  main.innerHTML = skeleton(4);
  let data;
  try {
    data = await api({ klausuren: "/exams", beurlaubung: "/own", fehlzeiten: "/absences" }[tab] || "/homework");
  } catch (error) {
    if (!ctx.isCurrent()) return;
    main.innerHTML = errorState(error);
    main.querySelector("[data-action=retry]")?.addEventListener("click", () => ctx.rerender());
    return;
  }
  if (!ctx.isCurrent()) return;

  const hideDone = readPref("hideDone", false);
  const onlyUnexcused = readPref("onlyUnexcused", false);
  const eyebrow = {
    hausaufgaben: () => `${data.items.filter((h) => !h.done && h.due >= data.today).length} offen`,
    klausuren: () => `${data.items.filter((e) => e.date >= data.today).length} anstehend`,
    beurlaubung: () => `${data.leaves.filter((l) => l.to >= data.today).length} geplant`,
    fehlzeiten: () => (data.available ? `${data.totals.unexcused} unentschuldigt` : "Klassenbuch"),
  }[tab]();
  const actions = {
    hausaufgaben: `<button class="btn ghost small" data-action="toggle-done" aria-pressed="${hideDone}">${hideDone ? "Erledigte zeigen" : "Erledigte ausblenden"}</button>`,
    klausuren: `<button class="btn small" data-action="add-exam">+ Klausur eintragen</button>`,
    beurlaubung: `<button class="btn small" data-action="add-leave">+ Beurlaubung eintragen</button>`,
    fehlzeiten: data.available && data.has_list
      ? `<button class="btn ghost small" data-action="toggle-unexcused" aria-pressed="${onlyUnexcused}">${onlyUnexcused ? "Auch entschuldigte zeigen" : "Nur unentschuldigte"}</button>`
      : "",
  };
  const body = {
    klausuren: () => examsView(data),
    beurlaubung: () => leavesView(data),
    fehlzeiten: () => absencesView(data, onlyUnexcused),
  }[tab]?.() ?? homeworkView(data, hideDone);

  main.innerHTML = `
    <header class="view-head reveal">
      <p class="eyebrow">${eyebrow}</p>
      <h1 class="display">${HEADINGS[tab]}</h1>
    </header>
    <div class="daynav reveal" style="margin-bottom:8px">
      <div class="segmented wide" role="group" aria-label="Ansicht">
        ${TABS.map((t) => `<a href="#/aufgaben${t === "hausaufgaben" ? "" : query({ tab: t })}" ${tab === t ? 'aria-current="page"' : ""}>${LABELS[t]}</a>`).join("")}
      </div>
    </div>
    <div class="btn-row reveal" style="margin-bottom:6px">${actions[tab]}</div>
    <div id="list">${body}</div>`;

  main.querySelector("[data-action=toggle-done]")?.addEventListener("click", () => {
    writePref("hideDone", !hideDone);
    ctx.rerender();
  });
  main.querySelector("[data-action=toggle-unexcused]")?.addEventListener("click", () => {
    writePref("onlyUnexcused", !onlyUnexcused);
    ctx.rerender();
  });
  main.querySelector("[data-action=add-exam]")?.addEventListener("click", () => editExam(null, ctx));
  main.querySelector("[data-action=add-leave]")?.addEventListener("click", () => editLeave(null, ctx));
  main.querySelectorAll("[data-edit-exam]").forEach((button) => {
    button.addEventListener("click", () => editExam(data.items.find((e) => e.id === button.dataset.editExam), ctx));
  });
  main.querySelectorAll("[data-edit-leave]").forEach((button) => {
    button.addEventListener("click", () => editLeave(data.leaves.find((l) => l.id === button.dataset.editLeave), ctx));
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
