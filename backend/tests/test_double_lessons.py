"""Doppelstunden erscheinen als eine Zeile, und jede Schnittstelle mit Daten verlangt eine Anmeldung."""

from __future__ import annotations

import asyncio
import importlib
from datetime import date

import pytest

from app import briefing, diff, normalize
from app.holidays import SchoolCalendar


def raw(day: str, hour: str, subject: str = "Musik", teacher: str = "Antwerpen", room: str = "M 21", **extra):
    lesson = {"subject": {"name": subject, "abbreviation": subject[:2]}, "teachers": [{"lastname": teacher}],
              "room": {"name": room}, "subjectLabel": f"{subject[:2]} G1"}
    item = {"date": day, "classHour": {"number": hour}, "actualLesson": lesson}
    item.update(extra)
    return item


def cancelled(day: str, hour: str, subject: str = "Musik"):
    original = {"subject": {"name": subject}, "teachers": [{"lastname": "Antwerpen"}], "room": {"name": "M 21"},
                "subjectLabel": f"{subject[:2]} G1"}
    return {"date": day, "classHour": {"number": hour}, "actualLesson": None, "isCancelled": True,
            "type": "cancelledLesson", "originalLessons": [original]}


def merged(*items):
    return normalize.merge_double_lessons(normalize.lessons(list(items)))


DAY = "2026-10-06"


# ── Zusammenfassen ───────────────────────────────────────────────────

def test_two_consecutive_lessons_become_one_row():
    lessons = normalize.lessons([raw(DAY, "3"), raw(DAY, "4")])
    (double,) = normalize.merge_double_lessons(lessons)
    assert (double["hour"], double["start"], double["end"], double["lesson_count"]) == ("3–4", "09:45", "11:15", 2)
    assert double["id"] == lessons[0]["id"]  # stabile Kennung: die der ersten Stunde
    assert len(lessons) == 2 and "lesson_count" not in lessons[0]  # die Eingabe bleibt unverändert


def test_a_break_between_two_lessons_keeps_them_apart():
    assert len(merged(raw(DAY, "2"), raw(DAY, "3"))) == 2  # 09:25 Pause 09:45
    assert len(merged(raw(DAY, "4"), raw(DAY, "5"))) == 2  # 11:15 Pause 11:35
    assert len(merged(raw(DAY, "6"), raw(DAY, "7"))) == 2  # 13:05 Pause 13:30


def test_lessons_that_differ_stay_apart():
    assert len(merged(raw(DAY, "3", "Musik"), raw(DAY, "4", "Mathematik"))) == 2  # anderes Fach
    assert len(merged(raw(DAY, "3"), raw(DAY, "4", room="M 22"))) == 2  # anderer Raum
    assert len(merged(raw(DAY, "3"), raw(DAY, "4", teacher="Pohl"))) == 2  # andere Lehrkraft
    assert len(merged(raw(DAY, "3"), cancelled(DAY, "4"))) == 2  # nur die zweite fällt aus
    assert len(merged(raw("2026-10-06", "4"), raw("2026-10-07", "5"))) == 2  # anderer Tag


def test_leave_for_only_one_hour_of_a_double_splits_it():
    """Eine Beurlaubung nur für die 4. Stunde ändert nur den Zustand, alles andere bleibt gleich."""
    first, second = normalize.lessons([raw(DAY, "3"), raw(DAY, "4")])
    assert len(normalize.merge_double_lessons([first, second])) == 1
    rows = normalize.merge_double_lessons([first, dict(second, state="leave")])
    assert [(r["hour"], r["state"]) for r in rows] == [("3", "regular"), ("4", "leave")]
    both = normalize.merge_double_lessons([dict(first, state="leave"), dict(second, state="leave")])
    assert [(r["hour"], r["state"]) for r in both] == [("3–4", "leave")]  # ganz beurlaubt: eine Zeile


def test_three_lessons_merge_up_to_the_next_break():
    rows = merged(raw(DAY, "1", "Mathematik"), raw(DAY, "2", "Mathematik"), raw(DAY, "3", "Mathematik"))
    assert [(r["hour"], r["start"], r["end"]) for r in rows] == [("1–2", "07:55", "09:25"), ("3", "09:45", "10:30")]


def test_a_cancelled_double_is_one_change():
    rows = merged(cancelled(DAY, "3"), cancelled(DAY, "4"))
    assert [(r["state"], r["hour"]) for r in rows] == [("cancelled", "3–4")]
    calendar = SchoolCalendar.__new__(SchoolCalendar)
    calendar._periods, calendar._calendar_periods = [], []
    summary = briefing.day_summary(date(2026, 10, 6), rows, [], [], [], set(), calendar)
    assert len(summary["changes"]) == 1
    push = briefing.build_push("morning", summary, date(2026, 10, 6), 0, 0)
    assert "3–4. Std: Musik entfällt" in push["body"]


def changes(before, after, today=date(2026, 10, 6)):
    return diff.diff_lessons(normalize.lessons(before), normalize.lessons(after), today)


def test_a_cancelled_double_lesson_is_one_notification():
    (change,) = changes([raw(DAY, "3"), raw(DAY, "4")], [cancelled(DAY, "3"), cancelled(DAY, "4")])
    assert change.title == "Heute: Musik fällt aus"
    assert change.body == "3–4. Stunde: Musik fällt aus"
    assert len(change.data["lessons"]) == 2  # beide Stunden bleiben vermerkt, damit nichts doppelt gemeldet wird


def test_a_notification_for_lessons_with_a_break_in_between_stays_separate():
    (change,) = changes([raw(DAY, "2"), raw(DAY, "3")], [cancelled(DAY, "2"), cancelled(DAY, "3")])
    assert change.title == "Heute: 2 Änderungen im Stundenplan"
    assert change.body.splitlines() == ["2. Stunde: Musik fällt aus", "3. Stunde: Musik fällt aus"]


def test_a_double_lesson_and_another_change_on_the_same_day():
    (change,) = changes(
        [raw(DAY, "3"), raw(DAY, "4"), raw(DAY, "7", "Mathematik", "Herold", "N 16")],
        [cancelled(DAY, "3"), cancelled(DAY, "4"), cancelled(DAY, "7", "Mathematik")],
    )
    assert change.title == "Heute: 2 Änderungen im Stundenplan"
    assert change.body.splitlines() == ["3–4. Stunde: Musik fällt aus", "7. Stunde: Mathematik fällt aus"]


def test_a_double_lesson_with_different_changes_is_not_merged():
    (change,) = changes([raw(DAY, "3"), raw(DAY, "4")], [cancelled(DAY, "3"), raw(DAY, "4", room="B112")])
    assert change.body.splitlines()[0] == "3. Stunde: Musik fällt aus"
    assert len(change.body.splitlines()) == 2


def test_exams_and_external_lessons_are_never_merged():
    one = {"date": DAY, "hour": "", "start": "08:00", "end": "09:00", "state": "exam", "subject": "Mathematik"}
    two = dict(one, start="09:00", end="10:00")
    assert len(normalize.merge_double_lessons([one, two])) == 2
    ext = {"date": DAY, "hour": "9", "start": "15:00", "end": "15:45", "state": "external", "subject": "Russisch"}
    assert len(normalize.merge_double_lessons([ext, dict(ext, hour="10", start="15:45", end="16:30")])) == 2


def test_double_lessons_with_a_comment_keep_both_notes_once():
    rows = merged(raw(DAY, "3", comment="Bitte Instrument mitbringen"), raw(DAY, "4", comment="Bitte Instrument mitbringen"))
    assert rows[0]["comment"] == "Bitte Instrument mitbringen"
    rows = merged(raw(DAY, "3", comment="A"), raw(DAY, "4", comment="B"))
    assert rows[0]["comment"] == "A · B"


def test_garbage_and_minimal_lessons_do_not_break_merging():
    assert normalize.merge_double_lessons([]) == []
    minimal = [{"date": DAY, "hour": "1", "state": "regular"}, {"date": DAY, "hour": "2", "state": "regular"}]
    assert normalize.merge_double_lessons(minimal) == minimal  # ohne Uhrzeiten wird nichts geraten


# ── In den Ansichten ─────────────────────────────────────────────────

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.setenv("APP_PASSWORD", "geheim")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from fastapi.testclient import TestClient

    import app.main as main

    main = importlib.reload(main)

    async def no_refresh(*_args, **_kwargs):
        return None

    monkeypatch.setattr(main.service.calendar, "refresh", no_refresh)
    main.service.calendar._periods = []
    asyncio.run(main.service.sync.run("test"))
    http = TestClient(main.app)
    http.post("/api/login", json={"password": "geheim"})
    return http, main


def test_day_week_and_calendar_show_the_double_lesson_once(client):
    http, main = client
    main.service.db.save_snapshot("lessons", normalize.lessons([
        raw(DAY, "3"), raw(DAY, "4"), raw(DAY, "7", "Mathematik", "Herold", "N 16")]))

    day = http.get(f"/api/overview?date={DAY}").json()["day"]
    assert [(l["hour"], l["subject"]) for l in day["lessons"]] == [("3–4", "Musik"), ("7", "Mathematik")]
    assert (day["start"], day["end"]) == ("09:45", "14:15")

    week = http.get("/api/week?start=2026-10-05").json()
    tuesday = [l for l in week["days"][1]["lessons"] if l["subject"] in ("Musik", "Mathematik")]
    assert any(l["hour"] == "3–4" for l in tuesday)

    token = http.get("/api/ical").json()["url"].rsplit("/", 1)[1]
    feed = http.get(f"/cal/{token}").text.replace("\r\n", "\n")
    assert feed.count("SUMMARY:Musik\n") == 1
    assert "DTSTART:20261006T074500Z" in feed and "DTEND:20261006T091500Z" in feed  # 09:45–11:15 MESZ


def test_the_briefing_still_counts_every_hour(client):
    """Die erste Stunde für die Briefing-Uhrzeit bleibt die Nummer der ersten Stunde einer Doppelstunde."""
    http, main = client
    main.service.db.save_snapshot("lessons", normalize.lessons([raw(DAY, "2", "Deutsch"), raw(DAY, "3", "Deutsch")]))
    assert main.service.briefing_time_for(date(2026, 10, 6))["first_hour"] == "2"


# ── Die Kalender der Freunde bleiben, wie sie waren ──────────────────

def test_friend_calendar_keeps_single_lessons(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.setenv("APP_PASSWORD", "geheim")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from test_friends import FakeSchulmanager, invite, redeem
    from fastapi.testclient import TestClient

    import app.friends as friends_module
    import app.main as main
    import app.sync as sync_module

    monkeypatch.setattr(friends_module, "SchulmanagerClient", FakeSchulmanager)
    monkeypatch.setattr(sync_module, "SchulmanagerClient", FakeSchulmanager)

    async def no_refresh(*_args, **_kwargs):
        return None

    monkeypatch.setattr(SchoolCalendar, "refresh", no_refresh)
    main = importlib.reload(main)
    owner, guest = TestClient(main.app), TestClient(main.app)
    owner.post("/api/login", json={"password": "geheim"})
    manage = redeem(guest, invite(owner), "max@schule.de").json()["manage"].rsplit("/", 1)[1]
    friend = main.owner.friends.entries()[0]
    friend_db = main.owner.friends.app_for(friend).db
    friend_db.save_snapshot("lessons", normalize.lessons([raw(DAY, "3"), raw(DAY, "4")]))

    token = guest.get(f"/api/friend/{manage}").json()["url"].rsplit("/", 1)[1]
    feed = guest.get(f"/cal/{token}").text
    assert feed.count("SUMMARY:Musik") == 2  # unverändert: zwei Stunden, zwei Einträge


# ── Anmeldung an jeder Schnittstelle ─────────────────────────────────

# Das sind alle Schnittstellen, die ohne Anmeldung erreichbar sein dürfen: Anmeldung selbst, der öffentliche
# Push-Schlüssel und die Seiten für Freunde (sie schützt der geheime Link in der Adresse).
PUBLIC_API = {
    "/api/login", "/api/logout", "/api/me", "/api/push/key",
    "/api/invite/{token}", "/api/friend/{token}", "/api/friend/{token}/login",
    "/api/friend/{token}/regenerate", "/api/friend/{token}/ui",
}


def test_every_data_route_requires_a_login(client):
    """Fällt dieser Test, hat eine neue Schnittstelle keine Anmeldung. Entweder `Depends(require_auth)` ergänzen
    oder sie bewusst in PUBLIC_API aufnehmen."""
    _, main = client
    open_routes = {
        route.path for route in main.app.routes
        if getattr(route, "path", "").startswith("/api/") and not route.dependant.dependencies
    }
    assert open_routes == PUBLIC_API


def test_data_routes_answer_401_without_a_session(client):
    _, main = client
    from fastapi.testclient import TestClient

    anonymous = TestClient(main.app)
    for path in ("/api/overview", "/api/week", "/api/homework", "/api/exams", "/api/absences", "/api/own",
                 "/api/settings", "/api/status", "/api/inbox", "/api/events", "/api/ical", "/api/push/devices"):
        assert anonymous.get(path).status_code == 401, path
