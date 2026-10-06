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
    me = http.get("/api/me").json()
    assert me["authenticated"] is True
    assert me["build"] and "commit" in me


def test_endpoints_return_data(client):
    http, main = client
    http.post("/api/login", json={"password": "geheim"})
    overview = http.get("/api/overview").json()
    assert overview["status"]["demo"] is True
    assert "lessons" in overview["day"]

    monday = date.today().isoformat()
    week = http.get(f"/api/week?start={monday}").json()
    assert len(week["days"]) == 5

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

    async def without_messenger(today):
        raw = await original(today)
        raw["threads"] = (False, None, 403)
        return raw

    monkeypatch.setattr(sync, "_fetch_raw", without_messenger)
    result = asyncio.run(sync.run("test"))
    assert result["ok"] and "threads" not in result["module_errors"]

    http.post("/api/login", json={"password": "geheim"})
    status = http.get("/api/status").json()
    assert status["disabled_modules"] == ["threads"] and status["module_errors"] == {}


def test_briefing_time_follows_first_planned_lesson(client):
    _, main = client
    service = main.service
    day = date(2026, 10, 6)

    def plan(*hours, cancelled=()):
        service.db.save_snapshot("lessons", [
            {"date": day.isoformat(), "hour": h, "state": "cancelled" if h in cancelled else "regular"}
            for h in hours
        ])

    plan("1", "2", "3")
    assert service.briefing_time_for(day)["time"] == "07:00"
    plan("2", "3")
    assert service.briefing_time_for(day)["time"] == "07:20"
    plan("3", "4")
    assert service.briefing_time_for(day)["time"] == "08:00"
    plan("1", "3", cancelled=("1",))  # planmäßig erste Stunde, auch wenn sie ausfällt
    assert service.briefing_time_for(day)["time"] == "07:00"
    plan()  # kein Unterricht eingetragen: feste Uhrzeit
    assert service.briefing_time_for(day)["time"] == "06:30"

    service.db.update_settings({"briefing_by_hour": {"2": "07:10"}})
    plan("2")
    assert service.briefing_time_for(day)["time"] == "07:10"
    service.db.update_settings({"briefing_mode": "fixed", "briefing_time": "06:45"})
    assert service.briefing_time_for(day)["time"] == "06:45"


def test_settings_reject_bad_briefing_values(client):
    http, _ = client
    http.post("/api/login", json={"password": "geheim"})
    assert http.put("/api/settings", json={"briefing_mode": "egal"}).status_code == 400
    assert http.put("/api/settings", json={"briefing_by_hour": {"1": "26:00"}}).status_code == 400


# ── Eigene Klausuren und Beurlaubungen ───────────────────────────────

EXAM = {"subject": "Mathematik", "date": "2026-10-12", "start": "08:00", "end": "10:15"}


def test_own_entries_need_login(client):
    http, _ = client
    assert http.get("/api/own").status_code == 401
    assert http.post("/api/own/exams", json=EXAM).status_code == 401
    assert http.delete("/api/own/exams/own-1").status_code == 401


def test_own_exam_crud_and_shows_up_as_exam(client):
    http, _ = client
    http.post("/api/login", json={"password": "geheim"})
    created = http.post("/api/own/exams", json=EXAM)
    assert created.status_code == 200
    item = created.json()
    assert item["id"].startswith("own-") and item["type"] == "Klausur"

    listed = {e["id"]: e for e in http.get("/api/exams").json()["items"]}
    assert listed[item["id"]]["manual"] is True and listed[item["id"]]["end"] == "10:15"
    assert http.get("/api/own").json()["exams"] == [item]

    changed = http.put(f"/api/own/exams/{item['id']}", json=dict(EXAM, end="10:30", type="Test")).json()
    assert changed["end"] == "10:30" and changed["id"] == item["id"] and changed["type"] == "Test"
    assert http.put("/api/own/exams/gibtsnicht", json=EXAM).status_code == 404
    assert http.put(f"/api/own/exams/{item['id']}", json=dict(EXAM, end="07:00")).status_code == 400

    assert http.delete(f"/api/own/exams/{item['id']}").json() == {"ok": True}
    assert http.delete(f"/api/own/exams/{item['id']}").status_code == 404
    assert http.get("/api/own").json()["exams"] == []


def test_own_entries_reject_bad_input(client):
    http, _ = client
    http.post("/api/login", json={"password": "geheim"})
    assert http.post("/api/own/exams", json=dict(EXAM, subject="")).status_code == 400
    assert http.post("/api/own/leaves", json={"from": "2026-10-12", "to": "2026-10-14", "hour_from": "3"}).status_code == 400
    assert http.post("/api/own/unbekannt", json={}).status_code == 404
    assert http.get("/api/own").json()["leaves"] == []


def test_own_lists_offer_subjects_and_hours(client):
    http, _ = client
    http.post("/api/login", json={"password": "geheim"})
    data = http.get("/api/own").json()
    assert data["subjects"] and data["hours"][0] == {"hour": "1", "start": "07:55", "end": "08:40"}


def test_leave_shows_in_overview_and_week(client):
    http, _ = client
    http.post("/api/login", json={"password": "geheim"})
    leave = http.post("/api/own/leaves", json={"from": "2026-10-12", "to": "2026-10-14", "reason": "Praktikum"}).json()
    day = http.get("/api/overview?date=2026-10-13").json()["day"]
    assert day["full_leave"] and day["leaves"][0]["id"] == leave["id"] and day["leaves"][0]["reason"] == "Praktikum"
    assert not http.get("/api/overview?date=2026-10-15").json()["day"]["full_leave"]
    week = http.get("/api/week?start=2026-10-12").json()
    assert [bool(d["leaves"]) for d in week["days"]] == [True, True, True, False, False]


def test_exam_moves_briefing_to_exam_hour(client):
    _, main = client
    service = main.service
    day = date(2026, 10, 6)
    service.db.save_snapshot("lessons", [
        {"date": "2026-10-06", "hour": "3", "state": "regular", "start": "09:45", "end": "10:30", "subject": "Deutsch"},
    ])
    assert service.briefing_time_for(day)["time"] == "08:00"  # 3. Stunde
    service.db.set("own_exams", [{"id": "own-1", "subject": "Mathematik", "date": "2026-10-06", "start": "08:00",
                                  "end": "10:15", "type": "Klausur", "comment": ""}])
    # Die Klausur ab 08:00 liegt in der 1. Stunde, das Briefing kommt entsprechend früh
    assert service.briefing_time_for(day)["time"] == "07:00"


def test_no_morning_push_when_fully_on_leave(client):
    from datetime import datetime

    _, main = client
    service, scheduler = main.service, main.scheduler
    day = date(2026, 10, 13)  # Dienstag
    # Erste Stunde = 1., Briefing um 07:00, hier fällig um 07:05
    service.db.save_snapshot("lessons", [
        {"id": "a", "date": "2026-10-13", "hour": "1", "state": "regular", "start": "07:55", "end": "08:40",
         "subject": "Mathematik"},
    ])
    assert service.briefing_time_for(day)["time"] == "07:00"
    now = datetime(2026, 10, 13, 7, 5, tzinfo=main.cfg.tz)

    service.db.set("own_leaves", [{"id": "own-9", "from": "2026-10-13", "to": "2026-10-13", "hour_from": "",
                                   "hour_to": "", "reason": ""}])
    assert service.briefing_skipped(day)
    asyncio.run(scheduler.tick(now))
    assert service.db.get("sent_briefing") is None  # beurlaubt: kein Morgen-Push

    service.db.set("own_leaves", [])
    assert not service.briefing_skipped(day)
    asyncio.run(scheduler.tick(now))
    assert service.db.get("sent_briefing") == "2026-10-13"  # ohne Beurlaubung kommt er


def test_briefing_skipped_only_when_everything_is_free(client):
    _, main = client
    service = main.service
    day = date(2026, 10, 13)
    service.db.save_snapshot("lessons", [
        {"id": f"{h}", "date": "2026-10-13", "hour": h, "state": "regular", "start": s, "end": e, "subject": "Mathematik"}
        for h, (s, e) in (("1", ("07:55", "08:40")), ("2", ("08:40", "09:25")), ("3", ("09:45", "10:30")))
    ])
    set_leave = lambda **kw: service.db.set("own_leaves", [dict(
        {"id": "own-9", "from": "2026-10-13", "to": "2026-10-13", "hour_from": "", "hour_to": "", "reason": ""}, **kw)])
    set_leave(hour_from="1", hour_to="2")
    assert not service.briefing_skipped(day)  # die 3. Stunde bleibt
    set_leave(hour_from="1", hour_to="3")
    assert service.briefing_skipped(day)  # alle Stunden beurlaubt
