"""HTTP-Schnittstelle und Auslieferung der PWA."""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import date
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import bell, config, ical, own
from .friends import FriendError
from .scheduler import Scheduler
from .service import AppService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("schule")

cfg = config.load()
service = AppService(cfg)
scheduler = Scheduler(service)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    scheduler.start()
    log.info("Gestartet%s, Zeitzone %s", " im DEMO-Modus" if cfg.demo else "", cfg.tz.key)
    yield
    await scheduler.stop()
    await service.sync.close()
    await service.friends.close()


app = FastAPI(title="Schul-Briefing", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=cfg.secret_key,
    session_cookie="schule_session",
    max_age=180 * 24 * 3600,
    same_site="lax",
    https_only=cfg.https,
)


NO_CACHE_PATHS = {"/", "/index.html", "/sw.js", "/manifest.webmanifest"}


def _build_id() -> str:
    """Prüfsumme über alle Frontend-Dateien: ändert sich mit jedem Update."""
    digest = hashlib.sha1()
    for path in sorted(cfg.static_dir.rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(cfg.static_dir).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:10]


# CSS, JS und Schriften werden unter /a/<build>/… ausgeliefert. Cloudflare und der
# Browser dürfen sie dann beliebig lange cachen, weil jedes Update neue URLs erzeugt.
# Relative Imports in den JS-Modulen erben das Präfix automatisch.
BUILD_ID = _build_id()
ASSET_PREFIX = f"/a/{BUILD_ID}"
APP_COMMIT = os.environ.get("APP_COMMIT", "")[:7]


def _page_html(name: str) -> str:
    html = (cfg.static_dir / name).read_text(encoding="utf-8")
    for marker in ('href="/css/', 'src="/js/', 'href="/fonts/'):
        html = html.replace(marker, marker.replace('"/', f'"{ASSET_PREFIX}/'))
    return html


INDEX_HTML = _page_html("index.html")
GUEST_HTML = _page_html("gast.html")  # Einladung und Seite der Freunde, ohne Anmeldung


@app.middleware("http")
async def cache_headers(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/a/"):
        pass  # Versionierte Dateien setzen ihren Cache-Header selbst
    elif path.startswith(("/api/", "/js/", "/css/")) or path in NO_CACHE_PATHS:
        response.headers["Cache-Control"] = "no-cache"
    elif path.startswith(("/fonts/", "/icons/")):
        response.headers["Cache-Control"] = "public, max-age=2592000"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


# ── Anmeldung am Dashboard ───────────────────────────────────────────

_failed_logins: dict[str, list[float]] = defaultdict(list)


def require_auth(request: Request) -> None:
    if not request.session.get("auth"):
        raise HTTPException(status_code=401, detail="Nicht angemeldet")


def _client_ip(request: Request) -> str:
    # Cloudflare setzt CF-Connecting-IP selbst und überschreibt jeden Wert des Besuchers. X-Forwarded-For
    # dagegen beginnt mit dem, was der Besucher mitschickt, und taugt nicht für die Sperre nach Fehlversuchen.
    cloudflare = request.headers.get("cf-connecting-ip")
    if cloudflare:
        return cloudflare.strip()
    forwarded = request.headers.get("x-forwarded-for")
    return forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "?")


@app.post("/api/login")
async def login(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    ip = _client_ip(request)
    attempts = [t for t in _failed_logins[ip] if time.time() - t < 900]
    _failed_logins[ip] = attempts
    if len(attempts) >= 5:
        raise HTTPException(status_code=429, detail="Zu viele Versuche. Bitte 15 Minuten warten.")
    password = str(body.get("password", ""))
    if not hmac.compare_digest(password.encode(), cfg.app_password.encode()):
        attempts.append(time.time())
        raise HTTPException(status_code=401, detail="Falsches Passwort")
    request.session.clear()
    request.session["auth"] = True
    request.session["since"] = int(time.time())
    return {"ok": True}


@app.post("/api/logout")
async def logout(request: Request) -> dict[str, Any]:
    request.session.clear()
    return {"ok": True}


@app.get("/api/me")
async def me(request: Request) -> dict[str, Any]:
    return {
        "authenticated": bool(request.session.get("auth")),
        "demo": cfg.demo,
        "build": BUILD_ID,
        "commit": APP_COMMIT,
    }


# ── Daten ────────────────────────────────────────────────────────────

def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Ungültiges Datum") from exc


@app.get("/api/overview", dependencies=[Depends(require_auth)])
async def overview(date: str | None = None) -> dict[str, Any]:
    return service.overview(_parse_date(date))


@app.get("/api/week", dependencies=[Depends(require_auth)])
async def week(start: str | None = None) -> dict[str, Any]:
    monday = _parse_date(start) or service.today()
    try:
        return await service.week(monday)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Stundenplan konnte nicht geladen werden: {exc}") from exc


@app.get("/api/homework", dependencies=[Depends(require_auth)])
async def homework() -> dict[str, Any]:
    done = service.db.homework_done_ids()
    items = [dict(h, done=h["id"] in done) for h in service.snap("homework")]
    return {"today": service.today().isoformat(), "items": items}


@app.post("/api/homework/{hw_id}", dependencies=[Depends(require_auth)])
async def homework_done(hw_id: str, body: dict[str, Any]) -> dict[str, Any]:
    service.db.set_homework_done(hw_id, bool(body.get("done")))
    return {"ok": True}


@app.get("/api/exams", dependencies=[Depends(require_auth)])
async def exams() -> dict[str, Any]:
    return {"today": service.today().isoformat(), "items": service.exams()}


# ── Eigene Einträge: Klausuren und Beurlaubungen ─────────────────────

OWN_CLEANERS = {"exams": own.clean_exam, "leaves": own.clean_leave}


def _own_cleaner(kind: str):
    if kind not in OWN_CLEANERS:
        raise HTTPException(status_code=404)
    return OWN_CLEANERS[kind]


def _own_clean(kind: str, body: dict[str, Any]) -> dict[str, Any]:
    try:
        return _own_cleaner(kind)(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/own", dependencies=[Depends(require_auth)])
async def own_entries() -> dict[str, Any]:
    return {
        "today": service.today().isoformat(),
        "exams": service.own("exams"),
        "leaves": service.own("leaves"),
        "subjects": service.subjects(),
        "hours": [{"hour": h, "start": s, "end": e} for h, (s, e) in bell.CLASS_HOURS.items()],
    }


@app.post("/api/own/{kind}", dependencies=[Depends(require_auth)])
async def own_create(kind: str, body: dict[str, Any]) -> dict[str, Any]:
    item = {"id": own.new_id(), **_own_clean(kind, body)}
    items = service.own(kind)
    if len(items) >= own.MAX_ENTRIES:
        raise HTTPException(status_code=400, detail="Zu viele Einträge. Bitte alte löschen.")
    service.db.set(f"own_{kind}", items + [item])
    return item


@app.put("/api/own/{kind}/{item_id}", dependencies=[Depends(require_auth)])
async def own_update(kind: str, item_id: str, body: dict[str, Any]) -> dict[str, Any]:
    cleaned = _own_clean(kind, body)
    items = service.own(kind)
    if not any(i["id"] == item_id for i in items):
        raise HTTPException(status_code=404, detail="Eintrag nicht gefunden")
    updated = {"id": item_id, **cleaned}
    service.db.set(f"own_{kind}", [updated if i["id"] == item_id else i for i in items])
    return updated


@app.delete("/api/own/{kind}/{item_id}", dependencies=[Depends(require_auth)])
async def own_delete(kind: str, item_id: str) -> dict[str, Any]:
    _own_cleaner(kind)
    items = service.own(kind)
    if not any(i["id"] == item_id for i in items):
        raise HTTPException(status_code=404, detail="Eintrag nicht gefunden")
    service.db.set(f"own_{kind}", [i for i in items if i["id"] != item_id])
    return {"ok": True}


@app.get("/api/inbox", dependencies=[Depends(require_auth)])
async def inbox() -> dict[str, Any]:
    return {"letters": service.snap("letters"), "threads": service.snap("threads")}


@app.get("/api/events", dependencies=[Depends(require_auth)])
async def events(before: int | None = None, limit: int = 40) -> dict[str, Any]:
    return {"items": service.db.events(min(limit, 100), before), "unseen": service.db.unseen_count()}


@app.post("/api/events/seen", dependencies=[Depends(require_auth)])
async def events_seen() -> dict[str, Any]:
    service.db.mark_all_seen()
    return {"ok": True}


@app.get("/api/status", dependencies=[Depends(require_auth)])
async def status() -> dict[str, Any]:
    return {**service.status(), "syncs": service.db.last_syncs(8)}


@app.post("/api/sync", dependencies=[Depends(require_auth)])
async def sync_now() -> dict[str, Any]:
    last = service.db.get("manual_sync_at", 0) or 0
    if time.time() - last < 60:
        raise HTTPException(status_code=429, detail="Bitte eine Minute warten, bevor du erneut abrufst.")
    service.db.set("manual_sync_at", time.time())
    return await service.sync.run("manual")


# ── Einstellungen ────────────────────────────────────────────────────

@app.get("/api/settings", dependencies=[Depends(require_auth)])
async def get_settings() -> dict[str, Any]:
    return service.db.settings()


@app.get("/api/bell", dependencies=[Depends(require_auth)])
async def bell_schedule() -> dict[str, Any]:
    return {"hours": [{"hour": h, "start": s, "end": e} for h, (s, e) in bell.CLASS_HOURS.items()]}


@app.put("/api/settings", dependencies=[Depends(require_auth)])
async def put_settings(body: dict[str, Any]) -> dict[str, Any]:
    try:
        return service.db.update_settings(body)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ── Briefing ─────────────────────────────────────────────────────────

@app.get("/api/briefing/{kind}", dependencies=[Depends(require_auth)])
async def briefing_preview(kind: str) -> dict[str, Any]:
    if kind not in ("morning", "evening"):
        raise HTTPException(status_code=404)
    return service.briefing(kind)


@app.post("/api/briefing/{kind}/send", dependencies=[Depends(require_auth)])
async def briefing_send(kind: str) -> dict[str, Any]:
    if kind not in ("morning", "evening"):
        raise HTTPException(status_code=404)
    result = await service.send_briefing(kind)
    return {"delivered": result["delivered"], "push": result["push"]}


# ── Push ─────────────────────────────────────────────────────────────

@app.get("/api/push/key")
async def push_key() -> dict[str, Any]:
    return {"key": service.pusher.public_key}


@app.post("/api/push/subscribe", dependencies=[Depends(require_auth)])
async def push_subscribe(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    try:
        service.pusher.subscribe(body, request.headers.get("user-agent"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@app.post("/api/push/unsubscribe", dependencies=[Depends(require_auth)])
async def push_unsubscribe(body: dict[str, Any]) -> dict[str, Any]:
    service.pusher.unsubscribe(str(body.get("endpoint", "")))
    return {"ok": True}


@app.post("/api/push/test", dependencies=[Depends(require_auth)])
async def push_test() -> dict[str, Any]:
    delivered = await service.pusher.send(
        "Testnachricht", "Wenn du das liest, funktionieren die Benachrichtigungen.", "/#/einstellungen", tag="test"
    )
    return {"delivered": delivered}


@app.get("/api/push/devices", dependencies=[Depends(require_auth)])
async def push_devices() -> dict[str, Any]:
    return {"devices": service.pusher.devices()}


# ── Kalender-Abo ─────────────────────────────────────────────────────

def _ical_token(regenerate: bool = False) -> str:
    token = service.db.get("ical_token")
    if regenerate or not token:
        token = secrets.token_urlsafe(24)
        service.db.set("ical_token", token)
    return token


def _ical_links(token: str) -> dict[str, str]:
    url = f"{cfg.public_url}/cal/{token}.ics"
    return {"url": url, "webcal": url.replace("https://", "webcal://", 1).replace("http://", "webcal://", 1)}


@app.get("/api/ical", dependencies=[Depends(require_auth)])
async def ical_url() -> dict[str, Any]:
    return _ical_links(_ical_token())


@app.post("/api/ical/regenerate", dependencies=[Depends(require_auth)])
async def ical_regenerate() -> dict[str, Any]:
    _ical_token(regenerate=True)
    return await ical_url()


@app.get("/cal/{token}.ics")
async def ical_feed(token: str, lessons: int = 1) -> Response:
    expected = service.db.get("ical_token")
    if expected and hmac.compare_digest(token.encode(), str(expected).encode()):
        body = ical.build(service.lessons(), service.exams(),
                          [e for e in service.snap("calendar") if not e["is_holiday"]], cfg.tz, bool(lessons),
                          service.own("leaves"))
    else:
        friend = service.friends.by_ical_token(token)
        if friend is None:
            raise HTTPException(status_code=404)
        try:
            body = service.friends.ical(friend, bool(lessons))
        except FriendError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(body, media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": 'inline; filename="schule.ics"'})


# ── Freunde: Kalender mit eigenem Login ──────────────────────────────

def _friend_error(exc: FriendError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@app.get("/api/friends", dependencies=[Depends(require_auth)])
async def friends_list() -> dict[str, Any]:
    friends = service.friends
    return {
        "friends": [friends.view(e) for e in friends.entries()],
        "invites": [
            {"token": i["token"], "label": i["label"], "expires": i["expires"],
             "url": f"{cfg.public_url}/einladung/{i['token']}"}
            for i in friends.invites()
        ],
    }


@app.post("/api/friends/invite", dependencies=[Depends(require_auth)])
async def friends_invite(body: dict[str, Any]) -> dict[str, Any]:
    try:
        invite = service.friends.create_invite(body.get("label", ""))
    except FriendError as exc:
        raise _friend_error(exc) from exc
    return {"token": invite["token"], "label": invite["label"], "expires": invite["expires"],
            "url": f"{cfg.public_url}/einladung/{invite['token']}"}


@app.delete("/api/friends/invite/{token}", dependencies=[Depends(require_auth)])
async def friends_revoke_invite(token: str) -> dict[str, Any]:
    if not service.friends.revoke_invite(token):
        raise HTTPException(status_code=404, detail="Einladung nicht gefunden")
    return {"ok": True}


@app.post("/api/friends/{friend_id}/sync", dependencies=[Depends(require_auth)])
async def friends_sync(friend_id: str) -> dict[str, Any]:
    try:
        result = await service.friends.sync_now(friend_id)
    except FriendError as exc:
        raise _friend_error(exc) from exc
    return {"ok": bool(result.get("ok")), "error": result.get("error")}


@app.delete("/api/friends/{friend_id}", dependencies=[Depends(require_auth)])
async def friends_delete(friend_id: str) -> dict[str, Any]:
    if not await service.friends.delete(friend_id):
        raise HTTPException(status_code=404, detail="Freund nicht gefunden")
    return {"ok": True}


# Öffentliche Seiten für die Freunde. Der geheime Link in der Adresse ist die einzige Zugangsbeschränkung,
# deshalb zählen Fehlversuche je Adresse, damit niemand Links durchprobieren oder Passwörter raten kann.

_guest_failures: dict[str, list[float]] = defaultdict(list)


def _guest_guard(request: Request) -> str:
    ip = _client_ip(request)
    recent = [t for t in _guest_failures[ip] if time.time() - t < 900]
    _guest_failures[ip] = recent
    if len(recent) >= 12:
        raise HTTPException(status_code=429, detail="Zu viele Versuche. Bitte 15 Minuten warten.")
    return ip


def _guest_failed(ip: str) -> None:
    _guest_failures[ip].append(time.time())


def _friend_for(request: Request, token: str) -> dict[str, Any]:
    ip = _guest_guard(request)
    entry = service.friends.by_manage_token(token)
    if entry is None:
        _guest_failed(ip)
        raise HTTPException(status_code=404, detail="Dieser Link ist ungültig.")
    return entry


def _friend_page(entry: dict[str, Any]) -> dict[str, Any]:
    return {**service.friends.view(entry), **_ical_links(entry["ical_token"])}


GUEST_HEADERS = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow"}


@app.get("/einladung/{token}")
@app.get("/freund/{token}")
async def guest_page(token: str) -> HTMLResponse:
    return HTMLResponse(GUEST_HTML, headers=GUEST_HEADERS)


@app.get("/api/invite/{token}")
async def invite_info(request: Request, token: str) -> dict[str, Any]:
    ip = _guest_guard(request)
    invite = service.friends.invite(token)
    if invite is None:
        _guest_failed(ip)
        raise HTTPException(status_code=404, detail="Der Einladungslink ist ungültig oder abgelaufen.")
    return {"label": invite["label"]}


@app.post("/api/invite/{token}")
async def invite_redeem(request: Request, token: str, body: dict[str, Any]) -> dict[str, Any]:
    ip = _guest_guard(request)
    if body.get("consent") is not True:
        raise HTTPException(status_code=400, detail="Bitte stimme der Speicherung zu.")
    try:
        entry = await service.friends.redeem(token, str(body.get("email", "")), str(body.get("password", "")))
    except FriendError as exc:
        _guest_failed(ip)
        raise _friend_error(exc) from exc
    return {"manage": f"/freund/{entry['manage_token']}"}


@app.get("/api/friend/{token}")
async def friend_info(request: Request, token: str) -> dict[str, Any]:
    return _friend_page(_friend_for(request, token))


@app.post("/api/friend/{token}/login")
async def friend_login(request: Request, token: str, body: dict[str, Any]) -> dict[str, Any]:
    entry = _friend_for(request, token)
    try:
        fresh = await service.friends.renew(entry, str(body.get("email", "")), str(body.get("password", "")))
    except FriendError as exc:
        _guest_failed(_client_ip(request))
        raise _friend_error(exc) from exc
    return _friend_page(fresh)


@app.post("/api/friend/{token}/regenerate")
async def friend_regenerate(request: Request, token: str) -> dict[str, Any]:
    entry = _friend_for(request, token)
    service.friends.regenerate_ical(entry["id"])
    return _friend_page(service.friends.entry(entry["id"]) or entry)


@app.delete("/api/friend/{token}")
async def friend_delete(request: Request, token: str) -> dict[str, Any]:
    entry = _friend_for(request, token)
    await service.friends.delete(entry["id"])
    return {"ok": True}


# ── Fehlersuche ──────────────────────────────────────────────────────

@app.get("/api/debug/raw/{module}", dependencies=[Depends(require_auth)])
async def debug_raw(module: str) -> Response:
    if not module.isalpha():
        raise HTTPException(status_code=400)
    path = cfg.data_dir / "raw" / f"{module}.json"
    if not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type="application/json")


@app.get("/healthz")
async def health() -> JSONResponse:
    return JSONResponse({"ok": True, "last_success": service.status()["last_success"]})


# ── Frontend ─────────────────────────────────────────────────────────

@app.get("/")
@app.get("/index.html")
async def index() -> HTMLResponse:
    return HTMLResponse(INDEX_HTML)


@app.get("/a/{build}/{path:path}")
async def asset(build: str, path: str) -> FileResponse:
    target = (cfg.static_dir / path).resolve()
    if not target.is_relative_to(cfg.static_dir) or not target.is_file():
        raise HTTPException(status_code=404)
    # Fragt eine alte Seite nach einer alten Version, gibt es die aktuelle Datei, aber ungecacht
    cache = "public, max-age=31536000, immutable" if build == BUILD_ID else "no-cache"
    return FileResponse(target, headers={"Cache-Control": cache})


@app.get("/sw.js")
async def service_worker() -> FileResponse:
    return FileResponse(cfg.static_dir / "sw.js", media_type="text/javascript",
                        headers={"Service-Worker-Allowed": "/"})


app.mount("/", StaticFiles(directory=cfg.static_dir, html=True), name="static")
