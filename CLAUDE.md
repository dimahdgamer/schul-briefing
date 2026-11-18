# Schulmanager App

## graphify immer nutzen

Dieses Projekt hat einen Wissensgraphen in `graphify-out/`. Nutze ihn, wann immer es möglich ist.

- Bei jeder Frage zum Code, zur Architektur oder zu Zusammenhängen zwischen Dateien: zuerst `graphify-out/GRAPH_REPORT.md` lesen.
- Danach gezielt abfragen: `graphify query "<Frage>"`, `graphify path "A" "B"` oder `graphify explain "X"`.
- Dateien erst öffnen, wenn der Graph darauf zeigt. Nicht mit breiten `grep`- oder `cat`-Läufen anfangen.
- Nach jeder Codeänderung `graphify update .` ausführen, damit der Graph aktuell bleibt (kein LLM nötig).
- Steht im Bericht ein älterer Commit als `git rev-parse HEAD`, zuerst `graphify update .`.
