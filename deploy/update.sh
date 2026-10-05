#!/usr/bin/env bash
# Holt das neueste Image von GitHub und startet den Container neu, falls es sich
# geändert hat. Läuft per cron alle 5 Minuten und schreibt nur bei Updates oder
# Fehlern etwas ins Log.
#
# Alles steckt in main(), weil sich das Skript beim Update selbst überschreibt:
# Bash liest die Funktion vollständig ein, bevor sie läuft.

main() {
  set -euo pipefail
  cd "$(dirname "$(readlink -f "$0")")"

  local image old new
  image="$(grep -E '^IMAGE=' .env | head -n 1 | cut -d= -f2-)"
  if [ -z "$image" ]; then
    echo "$(date '+%F %T') IMAGE fehlt in .env"
    exit 1
  fi

  old="$(docker image inspect -f '{{.Id}}' "$image" 2>/dev/null || true)"
  if ! docker pull -q "$image" > /dev/null 2>&1; then
    echo "$(date '+%F %T') Image konnte nicht geladen werden (Netz oder Login bei ghcr.io?)"
    exit 1
  fi
  new="$(docker image inspect -f '{{.Id}}' "$image")"

  if [ "$old" = "$new" ] && docker compose ps --status running -q | grep -q .; then
    exit 0
  fi

  # Pi-Dateien (Compose, Skripte, Vorlagen) aus dem neuen Image übernehmen
  docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/out" --entrypoint sh "$image" \
    -c 'cp /app/deploy/docker-compose.yml /app/deploy/setup.sh /app/deploy/update.sh /app/deploy/.env.example /app/deploy/*.conf /out/'
  chmod +x setup.sh update.sh

  docker compose up -d --remove-orphans
  docker image prune -f > /dev/null
  echo "$(date '+%F %T') aktualisiert auf ${new:7:12}"
  exit 0
}

main "$@"
