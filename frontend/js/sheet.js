import { esc } from "./ui.js";

// Eingabefenster, das von unten hereinkommt (native <dialog>: Esc, Fokus und Hintergrund gibt es geschenkt).
//
// fields:   [{ name, label, type, required, half, list, options, help, maxlength, placeholder, visible(values) }]
// onSubmit: bekommt die Werte, wirft bei Fehlern (die Meldung erscheint im Fenster)
// onDelete: optional, zeigt einen Löschen-Knopf
// onChange: optional, { name, values, set } nach jeder Eingabe, z. B. um ein Ende vorzuschlagen

function fieldHtml(field, value) {
  const id = `sheet-${field.name}`;
  let control;
  if (field.type === "chips") {
    // Mehrfachauswahl, der Wert steht als "0,3" in einem versteckten Feld
    const selected = new Set(String(value ?? "").split(",").filter(Boolean));
    control = `<input type="hidden" name="${field.name}" value="${esc([...selected].join(","))}" />
      <div class="chips" role="group" aria-label="${esc(field.label)}" data-chips="${field.name}">${field.options
      .map(([v, label]) => `<button type="button" class="chip" data-chip="${esc(v)}" aria-pressed="${selected.has(String(v))}">${esc(label)}</button>`)
      .join("")}</div>`;
  } else if (field.type === "select") {
    control = `<select class="input" id="${id}" name="${field.name}">${field.options
      .map(([v, label]) => `<option value="${esc(v)}" ${String(value ?? "") === String(v) ? "selected" : ""}>${esc(label)}</option>`)
      .join("")}</select>`;
  } else {
    control = `<input class="input" id="${id}" name="${field.name}" type="${field.type || "text"}" value="${esc(value)}"
      ${field.list ? `list="${id}-list"` : ""} ${field.maxlength ? `maxlength="${field.maxlength}"` : ""}
      ${field.placeholder ? `placeholder="${esc(field.placeholder)}"` : ""} autocomplete="off" />`;
    if (field.list) {
      control += `<datalist id="${id}-list">${field.list.map((v) => `<option value="${esc(v)}"></option>`).join("")}</datalist>`;
    }
  }
  return `
    <div class="sheet-field ${field.half ? "half" : ""}" data-field="${field.name}">
      <label class="field-label" for="${id}">${esc(field.label)}</label>
      ${control}
      ${field.help ? `<span class="field-help">${esc(field.help)}</span>` : ""}
    </div>`;
}

export function openSheet({ title, fields, values = {}, submitLabel = "Speichern", onSubmit, onDelete, onChange }) {
  const dialog = document.createElement("dialog");
  dialog.className = "sheet";
  dialog.setAttribute("aria-labelledby", "sheet-title");
  dialog.tabIndex = -1;
  dialog.innerHTML = `
    <form class="sheet-body" novalidate>
      <header class="sheet-head">
        <h2 id="sheet-title">${esc(title)}</h2>
        <button type="button" class="btn ghost small" data-sheet="close">Abbrechen</button>
      </header>
      <div class="sheet-grid">${fields.map((field) => fieldHtml(field, values[field.name])).join("")}</div>
      <p class="sheet-error" role="alert" hidden></p>
      <div class="sheet-actions">
        ${onDelete ? '<button type="button" class="btn small" data-sheet="delete">Löschen</button>' : "<span></span>"}
        <button type="submit" class="btn primary">${esc(submitLabel)}</button>
      </div>
    </form>`;
  document.body.appendChild(dialog);

  const form = dialog.querySelector("form");
  const error = dialog.querySelector(".sheet-error");
  const raw = () => Object.fromEntries(fields.map((f) => [f.name, form.elements[f.name].value.trim()]));
  // Ausgeblendete Felder zählen als leer, sonst bliebe z. B. eine Bis-Stunde stehen
  const read = () => {
    const all = raw();
    return Object.fromEntries(fields.map((f) => [f.name, f.visible && !f.visible(all) ? "" : all[f.name]]));
  };
  const sync = () => {
    const all = raw();
    for (const f of fields) {
      if (f.visible) dialog.querySelector(`[data-field="${f.name}"]`).hidden = !f.visible(all);
    }
  };
  const set = (name, value) => {
    form.elements[name].value = value;
  };
  const show = (message) => {
    error.textContent = message;
    error.hidden = false;
  };
  // Direkt entfernen, statt aufs close-Ereignis zu warten (das kommt erst in einer späteren Aufgabe)
  const closeSheet = () => {
    dialog.close();
    dialog.remove();
  };
  const run = async (button, action) => {
    error.hidden = true;
    button.disabled = true;
    try {
      await action();
      closeSheet();
    } catch (failure) {
      show(failure.message || "Das hat nicht geklappt.");
    } finally {
      button.disabled = false;
    }
  };

  form.addEventListener("input", (event) => {
    if (onChange && event.target.name) onChange({ name: event.target.name, values: raw(), set });
    sync();
  });
  dialog.querySelectorAll("[data-chips]").forEach((group) => {
    group.addEventListener("click", (event) => {
      const chip = event.target.closest("[data-chip]");
      if (!chip) return;
      chip.setAttribute("aria-pressed", String(chip.getAttribute("aria-pressed") !== "true"));
      const input = form.elements[group.dataset.chips];
      input.value = [...group.querySelectorAll('[aria-pressed="true"]')].map((c) => c.dataset.chip).join(",");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const values = read();
    const missing = fields.find((f) => f.required && !values[f.name]);
    if (missing) return show(`${missing.label} fehlt.`);
    run(event.submitter || form.querySelector("[type=submit]"), () => onSubmit(values));
  });
  dialog.querySelector('[data-sheet="close"]').addEventListener("click", closeSheet);
  dialog.querySelector('[data-sheet="delete"]')?.addEventListener("click", (event) => {
    if (confirm("Diesen Eintrag wirklich löschen?")) run(event.currentTarget, onDelete);
  });
  // Ein Tipp neben das Fenster schließt es
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) closeSheet();
  });
  // Esc schließt das Fenster von selbst, dann räumt dieses Ereignis auf
  dialog.addEventListener("close", () => dialog.remove());

  sync();
  dialog.showModal();
  // Der Browser fokussiert sonst den Abbrechen-Knopf und zeichnet einen Rahmen darum
  dialog.focus({ preventScroll: true });
}
