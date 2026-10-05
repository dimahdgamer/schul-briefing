import { api } from "../api.js";
import { decimal, errorState, esc, icon, shortDate, skeleton, timeAgo } from "../ui.js";

export const title = "Noten";

// Noten 1–6: 1 steht oben (besser). Punkte 0–15: 15 steht oben.
function scale(system) {
  return system === 1
    ? { min: 0, max: 15, ticks: [0, 5, 10, 15] }
    : { min: 1, max: 6, ticks: [1, 2, 3, 4, 5, 6] };
}

function yFor(value, system, top, height) {
  const s = scale(system);
  const t = (value - s.min) / (s.max - s.min);
  return system === 1 ? top + (1 - t) * height : top + t * height;
}

function sparkline(subject) {
  const points = subject.grades.filter((g) => g.numeric !== null).slice(-12);
  if (points.length < 2) return `<svg class="spark" viewBox="0 0 96 30" aria-hidden="true"></svg>`;
  const w = 96;
  const h = 30;
  const step = (w - 6) / (points.length - 1);
  const coords = points.map((g, i) => [3 + i * step, yFor(g.numeric, subject.system, 3, h - 6)]);
  const path = coords.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const [lx, ly] = coords[coords.length - 1];
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" aria-hidden="true"><path class="line" d="${path}"/><circle class="last" cx="${lx.toFixed(1)}" cy="${ly.toFixed(1)}" r="3"/></svg>`;
}

function detailChart(container, subject) {
  const points = subject.grades.filter((g) => g.numeric !== null);
  if (points.length < 2) {
    container.innerHTML = "";
    return;
  }
  const width = Math.max(260, container.clientWidth);
  const height = 150;
  const pad = { left: 28, right: 12, top: 12, bottom: 22 };
  const innerW = width - pad.left - pad.right;
  const innerH = height - pad.top - pad.bottom;
  const s = scale(subject.system);
  const step = innerW / Math.max(1, points.length - 1);
  const coords = points.map((g, i) => [pad.left + i * step, yFor(g.numeric, subject.system, pad.top, innerH)]);

  const grid = s.ticks.map((t) => {
    const y = yFor(t, subject.system, pad.top, innerH).toFixed(1);
    return `<line x1="${pad.left}" x2="${width - pad.right}" y1="${y}" y2="${y}"/><text x="${pad.left - 8}" y="${y}" dy="3.5" text-anchor="end">${t}</text>`;
  }).join("");
  const firstDate = points[0].date ? shortDate(points[0].date) : "";
  const lastDate = points[points.length - 1].date ? shortDate(points[points.length - 1].date) : "";
  const path = coords.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const dots = coords.map(([x, y], i) => `<circle class="dot ${i === coords.length - 1 ? "last" : ""}" data-i="${i}" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="4.5"/>`).join("");
  const hits = coords.map(([x], i) => `<rect class="hit" data-i="${i}" x="${(x - step / 2).toFixed(1)}" y="0" width="${Math.max(step, 24).toFixed(1)}" height="${height}"/>`).join("");

  container.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Notenverlauf ${esc(subject.subject)}">
      <g class="grid">${grid}
        <text x="${pad.left}" y="${height - 4}">${esc(firstDate)}</text>
        <text x="${width - pad.right}" y="${height - 4}" text-anchor="end">${esc(lastDate)}</text>
      </g>
      <path class="line" d="${path}"/>
      ${dots}
      ${hits}
    </svg>
    <div class="tooltip" hidden></div>`;

  const svg = container.querySelector("svg");
  const tip = container.querySelector(".tooltip");
  const show = (i) => {
    const g = points[i];
    const [x, y] = coords[i];
    svg.querySelectorAll(".dot").forEach((d) => d.classList.toggle("active", Number(d.dataset.i) === i));
    tip.innerHTML = `<strong>${esc(g.value)}</strong> ${g.type ? `· ${esc(g.type)}` : ""}<br><span class="muted">${esc(g.date ? shortDate(g.date) : "")}${g.weight !== 1 ? ` · Gewicht ${esc(decimal(g.weight, g.weight % 1 ? 1 : 0))}` : ""}</span>`;
    const rect = svg.getBoundingClientRect();
    tip.style.left = `${Math.min(Math.max((x / width) * rect.width, 70), rect.width - 70)}px`;
    tip.style.top = `${(y / height) * rect.height}px`;
    tip.hidden = false;
  };
  const hide = () => {
    tip.hidden = true;
    svg.querySelectorAll(".dot").forEach((d) => d.classList.remove("active"));
  };
  svg.querySelectorAll(".hit").forEach((hit) => {
    hit.addEventListener("pointerenter", () => show(Number(hit.dataset.i)));
    hit.addEventListener("click", () => show(Number(hit.dataset.i)));
  });
  svg.addEventListener("pointerleave", hide);
}

function gradeTable(subject) {
  const rows = [...subject.grades].reverse().map((g) => `
    <tr>
      <td class="d">${esc(g.date ? shortDate(g.date) : "–")}</td>
      <td>${esc(g.type || "Note")}${g.comment ? `<div class="muted" style="font-size:12.5px">${esc(g.comment)}</div>` : ""}</td>
      <td class="d num">${g.weight !== 1 ? `×${esc(decimal(g.weight, g.weight % 1 ? 1 : 0))}` : ""}</td>
      <td class="v num">${esc(g.value)}</td>
    </tr>`).join("");
  return `<table class="grade-table">
    <thead><tr><th>Datum</th><th>Art</th><th class="num">Gew.</th><th class="num">Note</th></tr></thead>
    <tbody>${rows}</tbody></table>
    ${subject.final ? `<p class="muted" style="margin:12px 0 0">Zeugnisnote: <strong class="mono" style="color:var(--ink)">${esc(subject.final)}</strong></p>` : ""}`;
}

export async function render(main, params, ctx) {
  main.innerHTML = skeleton(6);
  let data;
  try {
    data = await api("/grades");
  } catch (error) {
    if (!ctx.isCurrent()) return;
    main.innerHTML = errorState(error);
    main.querySelector("[data-action=retry]")?.addEventListener("click", () => ctx.rerender());
    return;
  }
  if (!ctx.isCurrent()) return;

  const subjects = data.subjects;
  const total = subjects.reduce((n, s) => n + s.grades.length, 0);
  const usesPoints = data.overall === null && data.overall_points !== null;
  const overall = usesPoints ? data.overall_points : data.overall;

  if (data.available === false) {
    main.innerHTML = `
      <header class="view-head reveal"><h1 class="display">Noten</h1></header>
      <div class="empty reveal"><span class="serif">Nicht freigeschaltet</span>Deine Schule zeigt Noten in Schulmanager nicht an. Sobald sie das tut, erscheinen sie hier automatisch.</div>`;
    return;
  }

  if (!subjects.length) {
    main.innerHTML = `
      <header class="view-head reveal"><h1 class="display">Noten</h1></header>
      <div class="empty reveal"><span class="serif">Noch keine Noten</span>Sobald Lehrkräfte Noten eintragen und freigeben, erscheinen sie hier.</div>`;
    return;
  }

  main.innerHTML = `
    <header class="view-head reveal">
      <p class="eyebrow">${total} Noten in ${subjects.length} Fächern · Stand ${esc(timeAgo(data.updated))}</p>
      <h1 class="display">Noten</h1>
    </header>
    <section class="card pad reveal" style="--i:1">
      <div class="hero-figure">
        <span class="value">${esc(decimal(overall))}</span>
        <span class="label">Durchschnitt über alle Fächer<br>${usesPoints ? "Mittel der Fachschnitte, in Punkten" : "Mittel der Fachschnitte"}</span>
      </div>
    </section>
    <section class="section reveal" style="--i:2">
      <h2 class="section-title">Fächer <span class="aside">Verlauf · Schnitt</span></h2>
      <div class="card" id="subjects">
        ${subjects.map((s, i) => `
          <div class="subject" data-index="${i}">
            <button class="subject-head" type="button" aria-expanded="false" aria-controls="sd-${i}">
              <span><span class="subject-name">${esc(s.subject)}</span><br><span class="subject-count">${s.grades.length} ${s.grades.length === 1 ? "Note" : "Noten"}</span></span>
              ${sparkline(s)}
              <span class="subject-avg">${esc(decimal(s.average))}</span>
              <span class="caret">${icon("caret-right", "sm")}</span>
            </button>
            <div class="subject-detail" id="sd-${i}">
              <div class="chart"></div>
              ${gradeTable(s)}
            </div>
          </div>`).join("")}
      </div>
    </section>
    <p class="foot-status">Der Schnitt ist eine Schätzung aus den eingetragenen Noten und ihrer Gewichtung. Plus/Minus zählt ±0,3.</p>`;

  main.querySelectorAll(".subject-head").forEach((button) => {
    button.addEventListener("click", () => {
      const wrap = button.closest(".subject");
      const open = !wrap.classList.contains("open");
      wrap.classList.toggle("open", open);
      button.setAttribute("aria-expanded", String(open));
      if (open) detailChart(wrap.querySelector(".chart"), subjects[Number(wrap.dataset.index)]);
    });
  });
}
