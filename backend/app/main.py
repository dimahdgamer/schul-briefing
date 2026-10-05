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

from . import bell, config, ical
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


def _index_html() -> str:
    html = (cfg.static_dir / "index.html").read_text(encoding="utf-8")
    for marker in ('href="/css/', 'src="/js/', 'href="/fonts/'):
        html = html.replace(marker, marker.replace('"/', f'"{ASSET_PREFIX}/'))
    return html


INDEX_HTML = _index_html()


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
    return {"today": service.today().isoformat(), "items": service.snap("exams")}


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


@app.get("/api/ical", dependencies=[Depends(require_auth)])
async def ical_url() -> dict[str, Any]:
    token = _ical_token()
    return {"url": f"{cfg.public_url}/cal/{token}.ics", "webcal": f"{cfg.public_url.replace('https://', 'webcal://').replace('http://', 'webcal://')}/cal/{token}.ics"}


@app.post("/api/ical/regenerate", dependencies=[Depends(require_auth)])
async def ical_regenerate() -> dict[str, Any]:
    _ical_token(regenerate=True)
    return await ical_url()


@app.get("/cal/{token}.ics")
async def ical_feed(token: str, lessons: int = 1) -> Response:
    expected = service.db.get("ical_token")
    if not expected or not hmac.compare_digest(token.encode(), str(expected).encode()):
        raise HTTPException(status_code=404)
    body = ical.build(service.snap("lessons"), service.snap("exams"),
                      [e for e in service.snap("calendar") if not e["is_holiday"]], cfg.tz, bool(lessons))
    return Response(body, media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": 'inline; filename="schule.ics"'})


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
