from __future__ import annotations

import asyncio
import importlib
from datetime import date

import pytest

from app.scheduler import Scheduler


def test_due_window():
    due = Scheduler._due
    assert due(6 * 60 + 30, "06:30", None, "2026-10-05")
    assert due(7 * 60 + 59, "06:30", None, "2026-10-05")  # Nachholen nach Neustart
    assert not due(8 * 60 + 1, "06:30", None, "2026-10-05")  # zu spät, nicht mehr senden
    assert not due(6 * 60 + 29, "06:30", None, "2026-10-05")
    assert not due(6 * 60 + 40, "06:30", "2026-10-05", "2026-10-05")  # heute schon gesendet


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.setenv("APP_PASSWORD", "geheim")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from fastapi.testclient import TestClient

    import app.main as main

    main = importlib.reload(main)
    # Kalender ohne Netz: keine Ferien
    async def no_refresh(*_args, **_kwargs):
        return None

    monkeypatch.setattr(main.service.calendar, "refresh", no_refresh)
    main.service.calendar._periods = []
    asyncio.run(main.service.sync.run("test"))
    return TestClient(main.app), main


def test_auth_required(client):
    http, _ = client
    assert http.get("/api/overview").status_code == 401
    assert http.post("/api/login", json={"password": "falsch"}).status_code == 401
    assert http.post("/api/login", json={"password": "geheim"}).status_code == 200
    assert http.get("/api/me").json()["authenticated"] is True


def test_endpoints_return_data(client):
    http, main = client
    http.post("/api/login", json={"password": "geheim"})
    overview = http.get("/api/overview").json()
    assert overview["status"]["demo"] is True
    assert "lessons" in overview["day"]

    monday = date.today().isoformat()
    week = http.get(f"/api/week?start={monday}").json()
    assert len(week["days"]) == 5

    assert http.get("/api/grades").json()["subjects"]
    assert http.get("/api/homework").json()["items"]
    assert http.get("/api/inbox").json()["letters"]

    settings = http.put("/api/settings", json={"briefing_time": "07:15", "evening_enabled": True}).json()
    assert settings["briefing_time"] == "07:15" and settings["evening_enabled"] is True
    assert http.put("/api/settings", json={"briefing_time": "99:00"}).status_code == 400

    preview = http.get("/api/briefing/morning").json()
    assert preview["push"]["title"]

    ical = http.get("/api/ical").json()
    token = ical["url"].rsplit("/", 1)[1]
    feed = http.get(f"/cal/{token}")
    assert feed.status_code == 200 and feed.text.startswith("BEGIN:VCALENDAR")
    assert http.get("/cal/falsch.ics").status_code == 404


def test_login_rate_limit(client):
    http, _ = client
    for _ in range(5):
        http.post("/api/login", json={"password": "x"})
    assert http.post("/api/login", json={"password": "geheim"}).status_code == 429


def test_static_frontend(client):
    http, main = client
    page = http.get("/")
    assert "<div id=\"root\">" in page.text
    assert page.headers["cache-control"] == "no-cache"
    prefix = f"/a/{main.BUILD_ID}"
    assert f'src="{prefix}/js/app.js"' in page.text
    assert f'href="{prefix}/css/app.css"' in page.text

    asset = http.get(f"{prefix}/js/app.js")
    assert asset.status_code == 200 and "javascript" in asset.headers["content-type"]
    assert "immutable" in asset.headers["cache-control"]
    stale = http.get("/a/altversion/js/app.js")
    assert stale.status_code == 200 and stale.headers["cache-control"] == "no-cache"
    assert http.get(f"{prefix}/../backend/app/config.py").status_code == 404
    assert http.get(f"{prefix}/%2e%2e/%2e%2e/README.md").status_code == 404

    assert http.get("/sw.js").headers["content-type"].startswith("text/javascript")
    assert http.get("/manifest.webmanifest").status_code == 200


def test_disabled_module_is_not_an_error(client, monkeypatch):
    http, main = client
    sync = main.service.sync
    original = sync._fetch_raw

    async def without_grades(today):
        raw = await original(today)
        raw["grades"] = (False, None, 403)
        return raw

    monkeypatch.setattr(sync, "_fetch_raw", without_grades)
    result = asyncio.run(sync.run("test"))
    assert result["ok"] and "grades" not in result["module_errors"]

    http.post("/api/login", json={"password": "geheim"})
    assert http.get("/api/me").json()["features"] == {"grades": False}
    assert http.get("/api/grades").json()["available"] is False
    status = http.get("/api/status").json()
    assert status["disabled_modules"] == ["grades"] and status["module_errors"] == {}
