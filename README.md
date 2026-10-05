# Schul-Briefing

Eigene Web-App (PWA) für Schulmanager Online. Sie fragt Stundenplan, Vertretungen,
Hausaufgaben, Klassenarbeiten, Noten, Elternbriefe und Nachrichten regelmäßig ab,
erkennt Änderungen und meldet sie per Push aufs Handy. Jeden Schultag kommt morgens
ein Briefing. Läuft im Docker-Container auf dem Raspberry Pi unter
`https://schule.gayjetlag.de` und aktualisiert sich über GitHub selbst.

> Schulmanager Online hat keine offizielle API. Die App nutzt dieselbe interne
> Schnittstelle wie die Website. Ändert die Schule oder der Anbieter etwas, kann ein
> Modul ausfallen. Die App läuft dann mit den übrigen Modulen weiter und zeigt den
> Fehler unter *Einstellungen → Status*.

## Funktionen

| Bereich | Was passiert |
|---|---|
| **Morgen-Briefing** | Push zur eingestellten Zeit (Standard 06:30), nur an Schultagen. Inhalt: Beginn und Schluss, Ausfälle („Später los: Beginn 08:50“), Vertretungen, fällige Hausaufgaben, nächste Klassenarbeit, ungelesene Post. |
| **Abend-Vorschau** | Optional, am Vorabend eines Schultags (auch Sonntagabend). |
| **Sofort-Meldungen** | Entfall, Vertretung, Raumänderung, „findet doch statt“, neue Hausaufgabe, Klassenarbeit neu/verschoben/entfernt, neue Note, neuer Elternbrief, neue Nachricht, neuer Schultermin. Jede Kategorie lässt sich einzeln abschalten. Viele Änderungen auf einmal kommen als eine Sammelnachricht. |
| **Klausur-Erinnerungen** | z. B. 7, 3 und 1 Tag vorher, Uhrzeit einstellbar. |
| **Heute** | Zeitleiste der Stunden mit laufender Stunde, Pausen und Freistunden, Kacheln für Beginn, Schluss, Änderungen, Hausaufgaben und nächste Arbeit. Nach Schulschluss springt die Ansicht auf den nächsten Schultag. |
| **Woche** | Mo–Fr mit markierten Änderungen, beliebige Wochen vor und zurück. |
| **Aufgaben** | Hausaufgaben nach Fälligkeit mit Abhaken (nur lokal), Klassenarbeiten mit Countdown. |
| **Noten** | Gesamtschnitt, Schnitt pro Fach, Verlauf als Diagramm, alle Noten als Tabelle. |
| **Post** | Elternbriefe und Nachrichten, Antippen öffnet Schulmanager. |
| **Verlauf** | Jede erkannte Änderung mit Zeitstempel. |
| **Kalender-Abo** | iCal-Link für den Handy-Kalender: Stundenplan inkl. Vertretungen, Arbeiten, Schultermine. |
| **Ferien** | NRW-Ferien und Feiertage (OpenHolidays API) plus schulfreie Tage aus dem Schulkalender. In den Ferien gibt es kein Briefing, und es wird seltener abgefragt. |
| **Offline** | Die zuletzt geladenen Daten bleiben ohne Netz sichtbar. |

Die App **liest nur**. Sie bestätigt keine Briefe und markiert nichts als gelesen.

## Aufbau

```
backend/app/
  schulmanager.py   Login (PBKDF2 + JWT), gebündelte API-Aufrufe, Token-Erneuerung
  normalize.py      Rohdaten -> stabile Strukturen
  diff.py           Vergleich zweier Stände -> Änderungen
  sync.py           Abruf, Vergleich, Verlauf, Push
  briefing.py       Tageszusammenfassung
  scheduler.py      minütlicher Taktgeber (Abruf, Briefing, Erinnerungen)
  holidays.py       Ferien/Feiertage NRW
  push.py           Web Push (VAPID-Schlüssel werden beim ersten Start erzeugt)
  ical.py           Kalender-Feed
  demo.py           Testdaten (DEMO_MODE=1)
  main.py           FastAPI, liefert auch das Frontend aus
frontend/           PWA ohne Build-Schritt (HTML, CSS, ES-Module, Service Worker)
deploy/             Dateien für den Pi: Compose, setup.sh, update.sh, nginx-Vorlage
.github/workflows/  Tests und Image-Build bei jedem Push
```

Alle Daten liegen in `data/` (SQLite, VAPID-Schlüssel, letzte Rohantworten zur Fehlersuche).

## Lokal ausprobieren (ohne Schulmanager-Konto)

```powershell
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
$env:DEMO_MODE="1"; $env:APP_PASSWORD="test"; $env:DATA_DIR="../data-demo"
.venv\Scripts\python -m uvicorn app.main:app --port 8765
```

Dann <http://localhost:8765> öffnen. Im Demo-Modus ändert sich bei jedem Abruf
zufällig etwas, damit Änderungserkennung und Push sichtbar werden.

Tests: `.venv\Scripts\python -m pytest`

Mit dem echten Konto testen: statt `DEMO_MODE` die Variablen `SM_EMAIL` und
`SM_PASSWORD` setzen. Was die API geliefert hat, steht danach in `data/raw/*.json`.

## Installation über GitHub

So läuft es: Du pushst Code nach GitHub. GitHub testet ihn, baut das Docker-Image und
legt es privat in der GitHub Container Registry (`ghcr.io`) ab. Der Pi schaut alle
5 Minuten nach einem neuen Image und aktualisiert sich selbst. Auf dem Pi liegt kein
Code, nur `docker-compose.yml`, `.env`, zwei Skripte und der Ordner `data/`.

Die Schritte 1 bis 6 machst du nur ein einziges Mal.

### Schritt 1: Repo auf GitHub anlegen (Browser)

1. <https://github.com/new> öffnen.
2. *Repository name*: `schul-briefing`
3. **Private** auswählen.
4. Nichts ankreuzen (kein README, keine .gitignore, keine Lizenz).
5. *Create repository* klicken.

### Schritt 2: Code hochladen (am PC, einmalig)

Im Projektordner:

```powershell
git remote add origin https://github.com/dimahdgamer/schul-briefing.git
git push -u origin main
```

Beim ersten Push öffnet sich ein Browserfenster zur GitHub-Anmeldung.

Danach auf GitHub im Repo den Reiter **Actions** öffnen. Der Lauf *Test & Build*
dauert beim ersten Mal ca. 5 bis 10 Minuten und muss am Ende grün sein.

### Schritt 3: Token für den Pi erstellen (Browser)

Der Pi braucht ein Token, um das private Image herunterzuladen. Es darf nur lesen.

1. <https://github.com/settings/tokens/new> öffnen (*Personal access token (classic)*).
2. *Note*: `Pi schul-briefing`
3. *Expiration*: `No expiration` (oder ein Datum, dann musst du es später erneuern).
4. Nur den Haken bei **`read:packages`** setzen, sonst nichts.
5. *Generate token* klicken und das Token (`ghp_…`) kopieren. Es wird nur einmal angezeigt.

### Schritt 4: Pi einrichten (per SSH auf dem Pi)

**4.1 Docker installieren (falls noch nicht vorhanden)**

```bash
uname -m                      # muss "aarch64" ausgeben (64-Bit-System)
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER # danach abmelden und neu per SSH anmelden
docker run --rm hello-world   # muss "Hello from Docker!" zeigen
docker compose version
```

Gibt `uname -m` den Wert `armv7l` aus, läuft auf dem Pi ein 32-Bit-System. Dann
muss im Workflow eine Zeile ergänzt werden.

**4.2 Bei GitHub anmelden.** Bei der Passwortabfrage das
Token aus Schritt 3 einfügen (es wird beim Einfügen nicht angezeigt):

```bash
docker login ghcr.io -u dimahdgamer
```

**4.3 Ordner anlegen und die Pi-Dateien aus dem Image holen:**

```bash
mkdir -p ~/schul-briefing && cd ~/schul-briefing
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/out" --entrypoint sh \
  ghcr.io/dimahdgamer/schul-briefing:latest -c 'cp -r /app/deploy/. /out/'
ls -a
```

Danach liegen dort `docker-compose.yml`, `setup.sh`, `update.sh`, `.env.example` und
`schule.gayjetlag.de.conf` (die nginx-Seite für Schritt 5).

**4.4 Einrichtung starten:**

```bash
./setup.sh
```

Beim ersten Mal wird nur die Datei `.env` angelegt. Öffne sie:

```bash
nano .env
```

Und trage ein:

| Zeile | Was rein muss |
|---|---|
| `IMAGE` | ist schon richtig ausgefüllt |
| `SM_EMAIL`, `SM_PASSWORD` | dein Schulmanager-Login |
| `APP_PASSWORD` | ein neues, langes Passwort für die App |
| `VAPID_SUBJECT` | `mailto:` + deine E-Mail-Adresse |
| `PUBLIC_URL` | bleibt `https://schule.gayjetlag.de` |

Speichern mit `Strg+O`, `Enter`, `Strg+X`. Dann noch einmal:

```bash
./setup.sh
```

Am Ende muss `Die App läuft lokal auf http://127.0.0.1:8470` stehen. Das Skript hat
außerdem einen cron-Eintrag angelegt, der alle 5 Minuten `update.sh` ausführt.

### Schritt 5: Subdomain über Cloudflare Tunnel und nginx freigeben

Weg einer Anfrage: Cloudflare (HTTPS) → `cloudflared` → nginx auf Port 80 → App auf
`127.0.0.1:8470`. Das ist derselbe Weg wie bei den anderen Subdomains. HTTPS macht
Cloudflare, deshalb braucht es weder certbot noch Zertifikate.

**5.1 Tunnel-Eintrag.** Erst eine Sicherung anlegen, dann die Datei öffnen:

```bash
sudo cp /etc/cloudflared/config.yml /etc/cloudflared/config.yml.$(date +%F)
sudo nano /etc/cloudflared/config.yml
```

Unter `ingress:` **direkt über** der letzten Zeile `- service: http_status:404`
einfügen, mit derselben Einrückung wie die anderen Einträge (Leerzeichen, keine Tabs):

```yaml
  - hostname: schule.gayjetlag.de
    service: http://localhost:80
```

**5.2 nginx-Seite anlegen:**

```bash
sudo cp ~/schul-briefing/schule.gayjetlag.de.conf /etc/nginx/sites-available/schule
sudo ln -s /etc/nginx/sites-available/schule /etc/nginx/sites-enabled/schule
sudo nginx -t && sudo systemctl reload nginx
```

`nginx -t` muss „syntax is ok“ und „test is successful“ melden.

**5.3 Tunnel prüfen, DNS-Eintrag anlegen, neu starten:**

```bash
cloudflared tunnel ingress validate --config /etc/cloudflared/config.yml
cloudflared tunnel route dns b90155e8-758d-46a6-a77e-1f030b0fce38 schule.gayjetlag.de
sudo systemctl restart cloudflared
```

Der zweite Befehl legt bei Cloudflare den CNAME-Eintrag an. Er braucht kein `sudo`,
weil die Anmeldung (`cert.pem`) in `~/.cloudflared` liegt.

#### Test

Im Browser <https://schule.gayjetlag.de/healthz> öffnen. Läuft der Container noch
nicht, kommt **502 Bad Gateway**. Das heißt, Tunnel und nginx funktionieren, nur die
App fehlt noch. Läuft sie, steht dort `{"ok":true,…}`. Danach
<https://schule.gayjetlag.de> öffnen und mit dem `APP_PASSWORD` anmelden.

### Schritt 6: Handy einrichten

- **Android (Chrome):** Seite öffnen, anmelden, Menü → *App installieren*.
  Dann *Einstellungen → Benachrichtigungen → Aktivieren*.
- **iPhone (ab iOS 16.4):** In Safari öffnen, *Teilen → Zum Home-Bildschirm*.
  Die App **vom Home-Bildschirm** starten und dort Benachrichtigungen aktivieren.
  Im normalen Safari-Tab geht Web Push auf dem iPhone nicht.
- Danach *Testnachricht senden*. Jedes Gerät muss einmal aktiviert werden.

### Später: Änderungen und Updates

- Änderungen nach `main` pushen, fertig. Nach dem grünen *Test & Build* holt sich der
  Pi die neue Version innerhalb von 5 Minuten. Schlägt ein Test fehl, wird nichts
  ausgeliefert.
- Was der Updater gemacht hat: `cat ~/schul-briefing/update.log`
- Logs der App: `cd ~/schul-briefing && docker compose logs -f --tail 100`
- Sofort aktualisieren statt 5 Minuten warten: `~/schul-briefing/update.sh`
- Zurück auf eine ältere Version: in `.env` bei `IMAGE` statt `latest` den
  Commit-Hash aus GitHub eintragen und `docker compose up -d` ausführen.
- `.env` und `data/` werden bei Updates nie angefasst. `docker-compose.yml` und die
  Skripte dagegen schon, eigene Änderungen gehören deshalb in `.env`.

## Wie oft wird abgefragt?

An Schultagen standardmäßig alle 15 Minuten zwischen 06:00 und 21:30 Uhr, am
Wochenende und in den Ferien stündlich. Jeder Abruf ist **ein** HTTP-Request, alle
Module sind darin gebündelt. Nach einem fehlgeschlagenen Login pausiert die App
6 Stunden, damit das Konto nicht gesperrt wird, und meldet das per Push.

## Fehlersuche

| Problem | Lösung |
|---|---|
| Login schlägt fehl | Zugangsdaten in `.env` prüfen. Hat das Konto mehrere Profile, statt der E-Mail den Benutzernamen verwenden. 2FA wird nicht unterstützt. |
| Ein Modul fehlt (z. B. Noten) | *Einstellungen → Status* zeigt, welches Modul nicht verfügbar ist. Die Rohantwort steht unter `/api/debug/raw/grades` (nach Anmeldung). |
| 502 Bad Gateway | Container läuft nicht: `cd ~/schul-briefing && docker compose ps` und `docker compose logs --tail 50`. |
| Keine Push-Nachrichten | Testnachricht senden. Auf dem iPhone muss die App vom Home-Bildschirm gestartet sein. Bei Android den Akku-Sparmodus für Chrome prüfen. |
| Ferien falsch | `HOLIDAY_SUBDIVISION` in `.env` prüfen (NRW = `DE-NW`). |

## Sicherheit

- Zugangsdaten stehen nur in `.env` auf dem Pi und werden nie ans Frontend geschickt.
- Das Dashboard ist durch `APP_PASSWORD` geschützt, mit Sperre nach 5 Fehlversuchen
  und einem Session-Cookie, das 180 Tage gültig ist.
- Der Kalender-Link enthält ein geheimes Token und lässt sich jederzeit neu erzeugen.
- Der Container läuft als normaler Benutzer und ist nur über Cloudflare bzw. nginx erreichbar.
- Das Token auf dem Pi darf nur Images lesen (`read:packages`).
