import { api } from "./api.js";
import { openSheet } from "./sheet.js";
import { toast } from "./ui.js";

// Eingabefenster, die an mehreren Stellen gebraucht werden

const COURSE_DAYS = [["0", "Mo"], ["1", "Di"], ["2", "Mi"], ["3", "Do"], ["4", "Fr"]];

export function editCourse(existing, ctx) {
  openSheet({
    title: existing ? "Unterricht bearbeiten" : "Eigenen Unterricht eintragen",
    fields: [
      { name: "subject", label: "Fach", required: true, maxlength: 60, placeholder: "z. B. Russisch" },
      { name: "weekdays", label: "Wochentage", type: "chips", required: true, options: COURSE_DAYS },
      { name: "start", label: "Von", type: "time", required: true, half: true },
      { name: "end", label: "Bis", type: "time", required: true, half: true },
      { name: "place", label: "Ort (optional)", maxlength: 60, placeholder: "z. B. Andere Schule" },
      { name: "from", label: "Gültig ab", type: "date", half: true },
      { name: "to", label: "Gültig bis", type: "date", half: true, help: "Beide leer: gilt immer" },
    ],
    values: existing ? { ...existing, weekdays: existing.weekdays.join(",") } : {},
    submitLabel: existing ? "Speichern" : "Eintragen",
    onSubmit: async (values) => {
      const body = { ...values, weekdays: values.weekdays.split(",").filter(Boolean).map(Number) };
      await api(existing ? `/own/courses/${existing.id}` : "/own/courses", { method: existing ? "PUT" : "POST", body });
      toast(existing ? "Unterricht gespeichert" : "Unterricht eingetragen");
      ctx.rerender();
    },
    onDelete: existing
      ? async () => {
        await api(`/own/courses/${existing.id}`, { method: "DELETE" });
        toast("Unterricht gelöscht");
        ctx.rerender();
      }
      : undefined,
  });
}
