"""Fehlzeiten laut Klassenbuch: Abruf, Aufbereitung, Meldungen."""

from __future__ import annotations

import asyncio
import importlib
import time
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app import diff, normalize
from app.config import Config
from app.db import Database
from app.holidays import SchoolCalendar
from app.schulmanager import RpcResult
from app.sync import ABSENCES_REFRESH, ABSENCES_RETRY, SyncService

TODAY = date(2026, 10, 6)


def entry(day="2026-10-05", start=None, end=None, **extra):
    base = {"date": day, "from": start, "until": end, "comment": None, "excused": False,
            "sickNote": None, "exemptionRequest": None}
    return dict(base, **extra)


def build(*entries, statistics=None, unexcused=None):
    return {"list": list(entries), "statistics": statistics, "statistics_unexcused": unexcused}


# ── Entschuldigungsstatus ────────────────────────────────────────────

def test_absence_without_any_reason_is_unexcused():
    (item,) = normalize.absences(build(entry(start="07:55", end="09:25")))["items"]
    assert item["unexcused"] and item["marks"] == ["Unentschuldigt"] and item["info"] == ["Grund unbekannt"]
    assert item["span"] == "07:55–09:25 Uhr" and item["date"] == "2026-10-05"


@pytest.mark.parametrize("certificate, label, unexcused", [
    ("Medical", "Attest", False),
    ("Form", "Entschuldigt", False),
    ("NotRequired", "Nicht erforderlich", False),
    (None, "Unentschuldigt", True),  # krankgemeldet, aber die Bescheinigung fehlt noch
    ("Sonstiges", "Unentschuldigt", True),
])
def test_sick_note_status_follows_the_certificate(certificate, label, unexcused):
    (item,) = normalize.absences(build(entry(sickNote={"certificateType": certificate})))["items"]
    assert item["info"] == ["Krankgemeldet"] and item["marks"] == [label] and item["unexcused"] is unexcused


def test_exemption_and_excused_flag_count_as_excused():
    data = normalize.absences(build(
        entry("2026-10-01", exemptionRequest={"isInternal": False, "comment": "Familienfeier"}),
        entry("2026-10-02", exemptionRequest={"isInternal": True, "comment": ""}),
        entry("2026-10-05", comment="Arzttermin", excused=True),
    ))
    by_date = {i["date"]: i for i in data["items"]}
    assert by_date["2026-10-01"]["info"] == ["Beurlaubt: Familienfeier"] and by_date["2026-10-01"]["marks"] == ["Genehmigt"]
    assert by_date["2026-10-02"]["info"] == ["Intern beurlaubt"]
    assert by_date["2026-10-05"]["info"] == ["Arzttermin"] and by_date["2026-10-05"]["marks"] == ["Entschuldigt"]
    assert data["unexcused_entries"] == 0


def test_time_spans_of_absences():
    spans = {i["date"]: i["span"] for i in normalize.absences(build(
        entry("2026-10-01"), entry("2026-10-02", start="09:45"), entry("2026-10-05", end="09:25"),
        entry("2026-10-06", start="07:55", end="08:40")))["items"]}
    assert spans == {"2026-10-01": "ganztägig", "2026-10-02": "ab 09:45 Uhr", "2026-10-05": "bis 09:25 Uhr",
                     "2026-10-06": "07:55–08:40 Uhr"}


def test_items_are_newest_first_with_stable_ids():
    data = normalize.absences(build(entry("2026-09-01"), entry("2026-10-05", start="08:00", end="09:00")))
    assert [i["date"] for i in data["items"]] == ["2026-10-05", "2026-09-01"]
    again = normalize.absences(build(entry("2026-10-05", start="08:00", end="09:00")))
    assert again["items"][0]["id"] == data["items"][0]["id"]


def test_statistics_per_subject_with_unexcused_share():
    data = normalize.absences(build(
        statistics=[{"subject": {"name": "Deutsch"}, "absentLessons": 3, "totalLessons": 70},
                    {"subject": {"name": "Mathematik"}, "absentLessons": 4, "totalLessons": 80},
                    {"subject": None, "absentLessons": 1, "totalLessons": 5}],
        unexcused=[{"subject": {"name": "Mathematik"}, "absentLessons": 2, "totalLessons": 80}]))
    assert [(s["subject"], s["absent"], s["unexcused"], s["total"]) for s in data["by_subject"]] == [
        ("Mathematik", 4, 2, 80), ("Deutsch", 3, 0, 70), ("Ohne Fach", 1, 0, 5)]
    assert data["totals"] == {"absent": 8, "unexcused": 2, "total": 155}
    assert data["has_statistics"] and data["has_list"]


def test_absences_tolerate_garbage():
    for raw in (None, {}, [], "x", {"list": None, "statistics": None}, {"list": [None, 3, {}], "statistics": ["x"]}):
        data = normalize.absences(raw)
        assert isinstance(data["items"], list) and isinstance(data["by_subject"], list)
    assert normalize.absences(None)["has_list"] is False


# ── Meldungen ────────────────────────────────────────────────────────

def snapshot(*entries):
    return normalize.absences(build(*entries))


def test_first_sync_reports_nothing():
    assert diff.diff_absences(None, snapshot(entry()), TODAY) == []


def test_new_unexcused_absence_is_reported():
    before = snapshot(entry("2026-10-01"))
    after = snapshot(entry("2026-10-01"), entry("2026-10-05", start="07:55", end="09:25"))
    (change,) = diff.diff_absences(before, after, TODAY)
    assert change.title == "Neue Fehlzeit (unentschuldigt)"
    assert change.body == "Mo 05.10., 07:55–09:25 Uhr: unentschuldigt"
    assert change.url == "/#/aufgaben?tab=fehlzeiten"
    assert diff.diff_absences(after, after, TODAY) == []


def test_excused_new_absence_names_its_status_and_old_ones_stay_quiet():
    before = snapshot()
    after = snapshot(entry("2026-10-05", sickNote={"certificateType": "Medical"}), entry("2026-08-20"))
    (change,) = diff.diff_absences(before, after, TODAY)  # die vom August ist zu alt
    assert change.title == "Neue Fehlzeit" and change.body == "Mo 05.10., ganztägig: attest"


def test_several_new_absences_are_grouped():
    before = snapshot()
    after = snapshot(entry("2026-10-05"), entry("2026-10-02", sickNote={"certificateType": "Medical"}))
    (change,) = diff.diff_absences(before, after, TODAY)
    assert change.title == "2 neue Fehlzeiten, 1 unentschuldigt"
    assert len(change.body.splitlines()) == 2


# ── Abruf ────────────────────────────────────────────────────────────

class FakeClassbook:
    """Gespielter Schulmanager: antwortet je Aufruf aus einer Tabelle und merkt sich alle Aufrufe."""

    def __init__(self, answers):
        self.answers = answers
        self.calls_made = []
        self.user = {"associatedStudent": {"id": 77}}

    @property
    def student(self):
        return self.user["associatedStudent"]

    async def calls(self, calls):
        out = []
        for call in calls:
            self.calls_made.append(call)
            answer = self.answers[call.endpoint]
            if callable(answer):
                answer = answer(call)
            status, data = answer
            out.append(RpcResult(status, data))
        return out

    async def close(self):
        pass


OK_ANSWERS = {
    "get-statistics": lambda call: (200, [{"subject": {"name": "Mathematik"}, "absentLessons": 2 if call.parameters["unexcusedOnly"] else 4,
                                           "totalLessons": 80}]),
    "get-current-previous-or-next-term": (200, {"id": 9, "start": "2026-08-01", "end": "2027-01-29"}),
    "get-history-absences-list": (200, [entry("2026-10-05", start="07:55", end="09:25")]),
}


@pytest.fixture()
def sync(tmp_path):
    cfg = Config(sm_email="a@b.de", sm_password="x", app_password="y", secret_key="k", public_url="http://x",
                 vapid_subject="mailto:a@b.de", data_dir=tmp_path, static_dir=tmp_path, tz=ZoneInfo("Europe/Berlin"),
                 demo=False, subdivision="DE-NW")
    db = Database(tmp_path / "t.db")
    return SyncService(cfg, db, SchoolCalendar(db, "DE-NW"), pusher=None)  # type: ignore[arg-type]


def fetch(sync, answers):
    sync.client = FakeClassbook(answers)
    return asyncio.run(sync._fetch_absences(77, TODAY)), sync.client


def test_absences_fetch_collects_statistics_and_list(sync):
    (ok, data, status), client = fetch(sync, OK_ANSWERS)
    assert ok and status == 200
    assert data["statistics"][0]["absentLessons"] == 4 and data["statistics_unexcused"][0]["absentLessons"] == 2
    assert data["list"][0]["date"] == "2026-10-05"
    called = {c.endpoint: c for c in client.calls_made}
    assert {c.module for c in client.calls_made} == {"classbook"}
    assert called["get-history-absences-list"].parameters == {"term": {"id": 9, "start": "2026-08-01", "end": "2027-01-29"},
                                                              "student": {"id": 77}}
    stats = [c for c in client.calls_made if c.endpoint == "get-statistics"]
    assert {c.parameters["unexcusedOnly"] for c in stats} == {False, True}
    assert all(c.parameters["from"] == "2026-08-01" and c.parameters["until"] == "2026-10-06" for c in stats)  # Schuljahr bis heute
    assert all(c.parameters["student"] == {"id": 77} and c.parameters["by"] == "subject" for c in stats)


def test_absences_are_only_fetched_once_an_hour(sync):
    (_, _, _), client = fetch(sync, OK_ANSWERS)
    made = len(client.calls_made)
    again = asyncio.run(sync._fetch_absences(77, TODAY))
    assert again[0] and again[1]["list"] and len(client.calls_made) == made  # aus dem Speicher, kein Aufruf
    sync.db.set("absences_fetch", {"at": time.time() - ABSENCES_REFRESH - 5, "status": 200})
    asyncio.run(sync._fetch_absences(77, TODAY))
    assert len(client.calls_made) > made


def test_school_that_does_not_release_absences_is_asked_once_a_day(sync):
    denied = {"get-statistics": (403, None), "get-current-previous-or-next-term": (403, None)}
    (ok, data, status), client = fetch(sync, denied)
    assert (ok, data, status) == (False, None, 403)
    made = len(client.calls_made)
    assert asyncio.run(sync._fetch_absences(77, TODAY)) == (False, None, 403) and len(client.calls_made) == made
    sync.db.set("absences_fetch", {"at": time.time() - ABSENCES_RETRY - 5, "status": 403})
    asyncio.run(sync._fetch_absences(77, TODAY))
    assert len(client.calls_made) > made


def test_statistics_retry_with_earlier_end_date_when_recent_days_are_hidden(sync):
    def stats(call):
        return (400, None) if call.parameters["until"] == "2026-10-06" else (200, [])

    answers = dict(OK_ANSWERS, **{"get-statistics": stats})
    (ok, data, _), client = fetch(sync, answers)
    assert ok and data["statistics"] == []
    untils = [c.parameters["until"] for c in client.calls_made if c.endpoint == "get-statistics"]
    assert untils == ["2026-10-06", "2026-10-06", "2026-09-22", "2026-09-22"]  # danach 14 Tage früher


def test_list_is_optional_when_the_term_is_unknown(sync):
    answers = dict(OK_ANSWERS, **{"get-current-previous-or-next-term": (404, None)})
    (ok, data, _), client = fetch(sync, answers)
    assert ok and data["statistics"] and data["list"] is None
    assert "get-history-absences-list" not in {c.endpoint for c in client.calls_made}
    normalized = normalize.absences(data)
    assert normalized["items"] == [] and normalized["has_statistics"] and not normalized["has_list"]


def test_friend_calendar_service_never_asks_for_absences(sync, tmp_path):
    from app.friends import FRIEND_MODULES

    assert "absences" not in FRIEND_MODULES


# ── Schnittstelle ────────────────────────────────────────────────────

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
    return http, main


def test_absences_endpoint_needs_login_and_shows_demo_data(client):
    http, _ = client
    assert http.get("/api/absences").status_code == 401
    http.post("/api/login", json={"password": "geheim"})
    data = http.get("/api/absences").json()
    assert data["available"] and data["reason"] is None
    assert data["totals"] == {"absent": 8, "unexcused": 2, "total": 200}
    assert data["unexcused_entries"] == 2 and len(data["items"]) == 5
    assert http.get("/api/overview").json()["unexcused_absences"] == 2


def test_absences_reason_when_not_available(client):
    http, main = client
    http.post("/api/login", json={"password": "geheim"})
    service = main.service
    service.db.execute("DELETE FROM snapshots WHERE module = 'absences'")
    assert http.get("/api/absences").json()["reason"] == "waiting"
    service.db.set("disabled_modules", ["absences"])
    data = http.get("/api/absences").json()
    assert data["reason"] == "disabled" and not data["available"] and data["items"] == []
    service.db.set("disabled_modules", [])
    service.db.set("status", {"module_errors": {"absences": "nicht verfügbar (Status 400)"}})
    data = http.get("/api/absences").json()
    assert data["reason"] == "error" and "400" in data["error"]
    assert http.get("/api/overview").json()["unexcused_absences"] == 0
