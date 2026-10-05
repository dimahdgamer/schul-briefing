#!/usr/bin/env bash
# Einmalige Einrichtung auf dem Pi (im Ordner ~/schul-briefing ausführen).
# Mehrfaches Ausführen schadet nicht.

set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

env_value() {
  grep -E "^$1=" .env 2>/dev/null | head -n 1 | cut -d= -f2- || true
}

if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
  echo ""
  echo "Die Datei .env wurde angelegt. Bitte ausfüllen:"
  echo "    nano $PWD/.env"
  echo "Danach ./setup.sh noch einmal ausführen."
  exit 0
fi

missing=()
for key in IMAGE SM_EMAIL SM_PASSWORD APP_PASSWORD PUBLIC_URL VAPID_SUBJECT; do
  [ -z "$(env_value "$key")" ] && missing+=("$key")
done
if [ ${#missing[@]} -gt 0 ]; then
  echo "In .env fehlt noch: ${missing[*]}"
  exit 1
fi
if grep -qE "DEIN-GITHUB-NAME|deine-adresse@example.org" .env; then
  echo "In .env stehen noch Platzhalter (DEIN-GITHUB-NAME bzw. deine-adresse@example.org)."
  exit 1
fi

# Der Container läuft als Benutzer mit UID 1000 und muss in data/ schreiben dürfen
mkdir -p data
if [ "$(stat -c %u data)" != "1000" ]; then
  echo "Passe Besitzer von data/ an (sudo) ..."
  sudo chown -R 1000:1000 data
fi

chmod +x setup.sh update.sh

echo "Lade Image und starte ..."
docker compose pull
docker compose up -d

# Automatische Updates alle 5 Minuten, nur einmal eintragen
line="*/5 * * * * $PWD/update.sh >> $PWD/update.log 2>&1"
( crontab -l 2>/dev/null | grep -vF "$PWD/update.sh" || true; echo "$line" ) | crontab -
echo "Automatische Updates eingerichtet (alle 5 Minuten)."

port="$(env_value APP_PORT)"
port="${port:-8470}"
echo ""
echo "Warte auf den Start ..."
for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:$port/healthz" > /dev/null 2>&1; then
    echo "Die App läuft lokal auf http://127.0.0.1:$port"
    exit 0
  fi
  sleep 2
done
echo "Die App antwortet noch nicht. Logs ansehen mit:  docker compose logs --tail 50"
exit 1
