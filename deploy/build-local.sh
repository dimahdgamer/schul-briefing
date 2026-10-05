#!/usr/bin/env bash
# Notlösung: das Image direkt auf dem Pi bauen, wenn GitHub Actions gerade nicht baut.
#
#   ~/schul-briefing-src/deploy/build-local.sh
#
# Holt den neuesten Code, baut das Image unter demselben Namen wie das von GitHub
# und startet die App neu. Danach pausiert das automatische Update (Datei
# .local-build), bis GitHub denselben Stand gebaut hat. Sonst würde update.sh
# die lokale Version wieder durch die ältere von GitHub ersetzen.

set -euo pipefail

SRC="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
APP="${APP:-$HOME/schul-briefing}"

if [ ! -f "$APP/.env" ]; then
  echo "Keine $APP/.env gefunden. Erst die normale Einrichtung machen (setup.sh)."
  exit 1
fi
image="$(grep -E '^IMAGE=' "$APP/.env" | head -n 1 | cut -d= -f2-)"
if [ -z "$image" ]; then
  echo "IMAGE fehlt in $APP/.env"
  exit 1
fi

echo "Hole neuesten Code ..."
git -C "$SRC" pull --ff-only
sha="$(git -C "$SRC" rev-parse HEAD)"

# Zuerst das neue update.sh, damit cron während des Builds nichts überschreibt
cp "$SRC/deploy/update.sh" "$APP/update.sh"
chmod +x "$APP/update.sh"
echo "$sha" > "$APP/.local-build"

echo "Baue Image (dauert ein paar Minuten) ..."
docker build --build-arg IMAGE_NAME="$image" --build-arg GIT_SHA="$sha" -t "$image" "$SRC"

cp "$SRC/deploy/docker-compose.yml" "$SRC/deploy/setup.sh" "$SRC"/deploy/*.conf "$APP/"
chmod +x "$APP/setup.sh"

cd "$APP"
docker compose up -d
docker image prune -f > /dev/null
echo ""
echo "Fertig: Version ${sha:0:7} läuft."
echo "Automatische Updates von GitHub sind pausiert, bis GitHub diesen Stand gebaut hat."
