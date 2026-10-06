"""Freunde mit eigener Oberfläche: Angebot, Zustimmung, Anmeldung mit Zugangscode, getrennte Daten."""

from __future__ import annotations

import asyncio
import re
import types
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from test_friends import FakeSchulmanager, PASSWORD, invite, make_friend, redeem, setup  # noqa: F401

CODE = re.compile(r"^[A-Z2-9]{4}(-[A-Z2-9]{4}){3}$")


def friend_id(owner):
    return owner.get("/api/friends").json()["friends"][0]["id"]


def offer(owner, fid, offered=True):
    response = owner.post(f"/api/friends/{fid}/ui", json={"offered": offered})
    assert response.status_code == 200, response.text
    return response.json()


def activate(guest, manage):
    page = guest.post(f"/api/friend/{manage}/ui", json={"action": "activate"})
    assert page.status_code == 200, page.text
    return page.json()


def friend_session(main, code):
    """Eigener Browser des Freundes: meldet sich mit dem Zugangscode an."""
    http = TestClient(main.app)
    return http, http.post("/api/login", json={"password": code})


@pytest.fixture()
def ready(setup):
    """Ein Freund mit aktivierter Oberfläche und sein angemeldeter Browser."""
    owner, guest, main = setup
    manage = make_friend(owner, guest)
    fid = friend_id(owner)
    offer(owner, fid)
    page = activate(guest, manage)
    http, login = friend_session(main, page["code"])
    assert login.status_code == 200
    return owner, guest, main, manage, fid, page["code"], http


# ── Angebot und Zustimmung ───────────────────────────────────────────

def test_ui_is_off_by_default_and_needs_an_offer_and_consent(setup):
    owner, guest, main = setup
    manage = make_friend(owner, guest)
    fid = friend_id(owner)
    assert guest.get(f"/api/friend/{manage}").json()["ui"] == "off"
    # Ohne Angebot kann der Freund nicht zustimmen
    assert guest.post(f"/api/friend/{manage}/ui", json={"action": "activate"}).status_code == 400

    assert offer(owner, fid)["ui"] == "offered"
    page = guest.get(f"/api/friend/{manage}").json()
    assert page["ui"] == "offered" and "code" not in page  # angeboten, noch nicht zugestimmt: kein Code, keine Anmeldung
    assert friend_session(main, "AAAA-BBBB-CCCC-DDDD")[1].status_code == 401

    page = activate(guest, manage)
    assert page["ui"] == "on" and CODE.match(page["code"]) and page["app_url"].endswith("/")
    assert owner.get("/api/friends").json()["friends"][0]["ui"] == "on"


def test_activating_twice_keeps_the_same_code(ready):
    """Ein doppelter Klick auf "Aktivieren" darf den Code nicht ersetzen und keinen Fehler zeigen."""
    owner, guest, main, manage, fid, code, http = ready
    again = guest.post(f"/api/friend/{manage}/ui", json={"action": "activate"})
    assert again.status_code == 200 and again.json()["code"] == code
    assert http.get("/api/me").json()["authenticated"]  # die Sitzung des Freundes bleibt gültig


def test_unknown_action_and_unknown_friend_are_rejected(setup):
    owner, guest, _ = setup
    manage = make_friend(owner, guest)
    assert guest.post(f"/api/friend/{manage}/ui", json={"action": "egal"}).status_code == 400
    assert guest.post("/api/friend/unbekannt/ui", json={"action": "activate"}).status_code == 404
    assert owner.post("/api/friends/f-gibtsnicht/ui", json={"offered": True}).status_code == 400
    assert guest.post(f"/api/friends/{friend_id(owner)}/ui", json={"offered": True}).status_code == 401  # nur der Besitzer


def test_the_code_is_never_in_the_owner_view_or_the_listing(ready):
    owner, guest, main, manage, fid, code, http = ready
    assert code not in owner.get("/api/friends").text
    stored = main.owner.friends.entry(fid)
    assert code not in str(stored) and code.replace("-", "") not in str(stored)  # nur verschlüsselt und als Prüfsumme


# ── Anmeldung ────────────────────────────────────────────────────────

def test_friend_logs_in_with_code_and_is_not_the_owner(ready):
    owner, guest, main, manage, fid, code, http = ready
    me = http.get("/api/me").json()
    assert me["authenticated"] and me["role"] == "friend" and me["label"] == "Max"
    assert owner.get("/api/me").json()["role"] == "owner"
    # Der Code lässt sich auch klein und ohne Striche eingeben
    other, login = friend_session(main, code.lower().replace("-", " "))
    assert login.status_code == 200 and other.get("/api/me").json()["role"] == "friend"


def test_wrong_codes_are_rate_limited_like_the_password(setup):
    _, _, main = setup
    http = TestClient(main.app)
    for _ in range(5):
        assert http.post("/api/login", json={"password": "FALS-CHER-CODE-1234"}).status_code == 401
    assert http.post("/api/login", json={"password": "FALS-CHER-CODE-1234"}).status_code == 429


def test_old_owner_sessions_without_a_who_still_work(setup):
    _, _, main = setup
    request = types.SimpleNamespace(session={"auth": True})  # so sahen Sitzungen vor den Freunden aus
    role, account = main._identity(request)
    assert role == "owner" and account is main.owner
    assert main._identity(types.SimpleNamespace(session={})) is None
    assert main._identity(types.SimpleNamespace(session={"auth": True, "who": "f-geloescht"})) is None


# ── Eigene Daten ─────────────────────────────────────────────────────

def test_friend_sees_own_data_and_the_owner_does_not(ready):
    owner, guest, main, manage, fid, code, http = ready
    week = http.get("/api/week?start=2026-10-12").json()
    subjects = {l["subject"] for d in week["days"] for l in d["lessons"]}
    assert "Fach-max@schule.de" in subjects
    owner_week = owner.get("/api/week?start=2026-10-12").json()
    assert "Fach-max@schule.de" not in {l["subject"] for d in owner_week["days"] for l in d["lessons"]}
    assert http.get("/api/overview").json()["status"]["account"]["class"] == "Q1"


def test_own_entries_are_separate_per_account(ready):
    owner, guest, main, manage, fid, code, http = ready
    exam = {"subject": "Mathe", "date": "2026-10-20", "start": "08:00", "end": "09:30"}
    assert http.post("/api/own/exams", json=exam).status_code == 200
    assert [e["subject"] for e in http.get("/api/own").json()["exams"]] == ["Mathe"]
    assert owner.get("/api/own").json()["exams"] == []
    owner.post("/api/own/leaves", json={"from": "2026-10-21"})
    assert http.get("/api/own").json()["leaves"] == []


def test_friend_has_no_owner_functions(ready):
    owner, guest, main, manage, fid, code, http = ready
    assert http.get("/api/friends").status_code == 403
    assert http.post("/api/friends/invite", json={"label": "x"}).status_code == 403
    assert http.delete(f"/api/friends/{fid}").status_code == 403
    assert http.post(f"/api/friends/{fid}/ui", json={"offered": False}).status_code == 403
    assert http.get("/api/debug/raw/lessons").status_code == 403
    assert owner.get("/api/friends").status_code == 200  # der Besitzer darf es weiterhin


def test_friend_calendar_endpoint_returns_the_existing_link(ready):
    owner, guest, main, manage, fid, code, http = ready
    link = http.get("/api/ical").json()
    assert link["url"] == guest.get(f"/api/friend/{manage}").json()["url"]
    assert "events_url" not in link  # die Freunde-Kalender bleiben, wie sie waren
    old = link["url"].rsplit("/", 1)[1]
    new = http.post("/api/ical/regenerate").json()["url"].rsplit("/", 1)[1]
    assert new != old and guest.get(f"/cal/{old}").status_code == 404 and guest.get(f"/cal/{new}").status_code == 200


def test_friends_push_key_is_their_own(ready):
    owner, guest, main, manage, fid, code, http = ready
    assert http.get("/api/push/key").json()["key"] != owner.get("/api/push/key").json()["key"]
    assert guest.get("/api/push/key").json()["key"] == owner.get("/api/push/key").json()["key"]  # ohne Anmeldung: der des Besitzers
    # Geräte sind getrennt: ein Gerät des Freundes taucht beim Besitzer nicht auf
    sub = {"endpoint": "https://push.example/abc", "keys": {"p256dh": "k", "auth": "a"}}
    assert http.post("/api/push/subscribe", json=sub).status_code == 200
    assert http.get("/api/push/devices").json()["devices"] and owner.get("/api/push/devices").json()["devices"] == []


# ── Kalender-Links ändern sich nicht ─────────────────────────────────

def test_calendar_link_and_content_stay_the_same_through_every_step(setup):
    owner, guest, main = setup
    manage = make_friend(owner, guest)
    fid = friend_id(owner)
    page = guest.get(f"/api/friend/{manage}").json()
    token = page["url"].rsplit("/", 1)[1]
    before = guest.get(f"/cal/{token}").text.replace("\r\n", "\n")
    stable = re.sub(r"DTSTAMP:\S+", "", before)

    offer(owner, fid)
    activate(guest, manage)
    for_ui = guest.get(f"/api/friend/{manage}").json()
    assert for_ui["url"] == page["url"]
    during = re.sub(r"DTSTAMP:\S+", "", guest.get(f"/cal/{token}").text.replace("\r\n", "\n"))
    assert during == stable  # gleicher Inhalt: Stunden, Klassenarbeiten und Schultermine

    guest.post(f"/api/friend/{manage}/ui", json={"action": "deactivate"})
    after = re.sub(r"DTSTAMP:\S+", "", guest.get(f"/cal/{token}").text.replace("\r\n", "\n"))
    assert after == stable and "SUMMARY:Wandertag" in after


def test_friend_exams_do_not_leak_into_the_old_calendar_link(ready):
    """Eigene Einträge eines Freundes mit Oberfläche gehören zu seiner App, der alte Kalender-Link bleibt unverändert."""
    owner, guest, main, manage, fid, code, http = ready
    http.post("/api/own/exams", json={"subject": "Mathe", "date": "2026-10-20", "start": "08:00", "end": "09:30"})
    token = guest.get(f"/api/friend/{manage}").json()["url"].rsplit("/", 1)[1]
    assert "Mathe" not in guest.get(f"/cal/{token}").text


# ── Abruf: nur das, wozu er zugestimmt hat ───────────────────────────

def test_homework_is_only_fetched_after_consent(setup):
    owner, guest, main = setup
    FakeSchulmanager.requested.clear()
    manage = make_friend(owner, guest)
    fid = friend_id(owner)
    assert "get-homework" not in FakeSchulmanager.requested
    offer(owner, fid)
    assert "get-homework" not in FakeSchulmanager.requested  # angeboten ist noch keine Zustimmung
    activate(guest, manage)
    assert "get-homework" in FakeSchulmanager.requested
    assert not {"get-letters", "get-subscriptions"} & set(FakeSchulmanager.requested)  # Post nie


def test_switching_off_removes_what_only_the_interface_fetched(ready):
    owner, guest, main, manage, fid, code, http = ready
    account = main.owner.friends.app_for(main.owner.friends.entry(fid))
    account.db.save_snapshot("homework", [{"id": "h", "subject": "Mathe", "text": "x", "due": "2026-10-20",
                                           "assigned": "2026-10-19", "due_estimated": False, "eva": False, "teacher": ""}])
    account.db.save_snapshot("absences", {"items": []})
    sub = {"endpoint": "https://push.example/abc", "keys": {"p256dh": "k", "auth": "a"}}
    http.post("/api/push/subscribe", json=sub)
    http.post("/api/own/exams", json={"subject": "Mathe", "date": "2026-10-20", "start": "08:00", "end": "09:30"})

    assert guest.post(f"/api/friend/{manage}/ui", json={"action": "deactivate"}).json()["ui"] == "off"
    fresh = main.owner.friends.app_for(main.owner.friends.entry(fid))
    assert fresh.db.snapshot("homework") is None and fresh.db.snapshot("absences") is None
    assert fresh.pusher.devices() == []
    assert fresh.own("exams")  # was der Freund selbst eingetragen hat, bleibt sein Eigentum
    assert not any(v for k, v in fresh.db.settings().items() if k.startswith("notify_"))


def test_owner_can_withdraw_the_interface_and_sessions_end_at_once(ready):
    owner, guest, main, manage, fid, code, http = ready
    assert http.get("/api/overview").status_code == 200
    assert offer(owner, fid, offered=False)["ui"] == "off"
    assert http.get("/api/overview").status_code == 401  # sofort, ohne Abmelden
    assert http.get("/api/me").json()["authenticated"] is False
    assert friend_session(main, code)[1].status_code == 401  # der Code gilt nicht mehr
    assert guest.get(f"/api/friend/{manage}").json()["ui"] == "off"
    # Angebot erneuern: wieder zustimmen nötig, der alte Code bleibt tot
    offer(owner, fid)
    assert friend_session(main, code)[1].status_code == 401
    new_code = activate(guest, manage)["code"]
    assert new_code != code and friend_session(main, new_code)[1].status_code == 200


def test_new_code_replaces_the_old_one(ready):
    owner, guest, main, manage, fid, code, http = ready
    page = guest.post(f"/api/friend/{manage}/ui", json={"action": "new-code"}).json()
    assert page["code"] != code and CODE.match(page["code"])
    assert friend_session(main, code)[1].status_code == 401
    assert friend_session(main, page["code"])[1].status_code == 200


def test_deleting_the_friend_ends_the_session_and_removes_the_data(ready):
    owner, guest, main, manage, fid, code, http = ready
    assert owner.delete(f"/api/friends/{fid}").json() == {"ok": True}
    assert http.get("/api/overview").status_code == 401
    assert friend_session(main, code)[1].status_code == 401
    assert not (main.cfg.data_dir / "accounts" / fid).exists()


# ── Taktgeber ────────────────────────────────────────────────────────

def test_friends_with_interface_run_in_the_scheduler_not_in_the_calendar_poll(ready):
    owner, guest, main, manage, fid, code, http = ready
    friends = main.owner.friends
    assert [key for key, _ in friends.ui_apps()] == [fid]

    FakeSchulmanager.requested.clear()
    friends._next_poll[fid] = 0
    asyncio.run(friends.poll_due(True))
    assert FakeSchulmanager.requested == []  # Freunde mit Oberfläche ruft nur der Taktgeber ab

    main.scheduler._last_poll[fid] = 0.0
    now = datetime(2026, 10, 13, 12, 0, tzinfo=main.cfg.tz)
    asyncio.run(main.scheduler.tick(now))
    assert FakeSchulmanager.requested.count("get-actual-lessons") == 1
    assert "get-homework" in FakeSchulmanager.requested


def test_wrong_password_of_a_friend_with_interface_stops_polling(ready):
    owner, guest, main, manage, fid, code, http = ready
    friends = main.owner.friends
    FakeSchulmanager.expired_for.add("max@schule.de")
    friend_app = dict(friends.ui_apps())[fid]
    asyncio.run(main.scheduler._poll(friend_app, fid, "schedule"))
    assert friends.entry(fid)["needs_login"] and friends.ui_apps() == []
    # Anmelden in seiner App geht weiter, er sieht dort den Fehler und kann auf seiner Seite das Login erneuern
    assert http.get("/api/overview").status_code == 200
    assert guest.get(f"/api/friend/{manage}").json()["state"] == "needs_login"


def test_each_account_has_its_own_briefing_settings(ready):
    owner, guest, main, manage, fid, code, http = ready
    assert http.put("/api/settings", json={"briefing_time": "05:55", "briefing_mode": "fixed"}).status_code == 200
    assert http.get("/api/settings").json()["briefing_time"] == "05:55"
    assert owner.get("/api/settings").json()["briefing_time"] != "05:55"
    assert http.get("/api/settings").json()["poll_interval"] == 30  # Freunde mit Oberfläche rufen etwas seltener ab
