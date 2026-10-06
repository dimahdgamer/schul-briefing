"""Freunde: Kalender-Link mit eigenem Schulmanager-Login (Einladung, Abruf, Löschen)."""

from __future__ import annotations

import asyncio
import importlib
from datetime import timedelta

import pytest
from cryptography.fernet import InvalidToken

from app.friends import Vault
from app.schulmanager import LoginError, RpcResult

PASSWORD = "geheim-passwort-123"


class FakeSchulmanager:
    """Gespielter Schulmanager: ersetzt den Client, damit kein Test ins Netz geht."""

    logins: list[str] = []
    requested: list[str] = []
    expired_for: set[str] = set()  # Konten, deren Passwort inzwischen nicht mehr stimmt

    def __init__(self, email, password, load_token=None, save_token=None):
        self.email, self.password = email, password
        self._save = save_token
        stored = (load_token() if load_token else None) or {}
        self.token = stored.get("jwt")
        self.user = stored.get("user") or {}

    @property
    def student(self):
        return self.user.get("associatedStudent")

    async def login(self):
        type(self).logins.append(self.email)
        if self.password == "falsch" or self.email in type(self).expired_for:
            raise LoginError("Benutzername oder Passwort falsch.", 401, "auth")
        self.token = "jwt-" + self.email
        self.user = {"firstname": "Max", "lastname": "Muster", "associatedStudent": {"id": 77, "className": "Q1"}}
        if self._save:
            self._save({"jwt": self.token, "user": self.user})

    async def calls(self, calls):
        if self.email in type(self).expired_for:
            raise LoginError("Benutzername oder Passwort falsch.", 401, "auth")
        if not self.user:
            await self.login()
        out = []
        for call in calls:
            type(self).requested.append(call.endpoint)
            if call.endpoint == "get-actual-lessons":
                data = [{"date": "2026-10-12", "classHour": {"number": "1"},
                         "actualLesson": {"subject": {"name": f"Fach-{self.email}", "abbreviation": "X"},
                                          "teachers": [{"lastname": "Muster"}], "room": {"name": "A1"}}}]
            elif call.endpoint == "get-events-for-user":
                data = {"nonRecurringEvents": [{"id": 5, "summary": "Wandertag", "start": "2026-10-14T00:00:00",
                                                 "end": "2026-10-15T00:00:00", "allDay": True, "categoryId": 3}],
                        "recurringEvents": []}
            else:
                data = []
            out.append(RpcResult(200, data))
        return out

    async def close(self):
        pass


@pytest.fixture()
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.setenv("APP_PASSWORD", "geheim")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from fastapi.testclient import TestClient

    import app.friends as friends_module
    import app.main as main
    import app.sync as sync_module
    from app.holidays import SchoolCalendar

    FakeSchulmanager.logins, FakeSchulmanager.requested, FakeSchulmanager.expired_for = [], [], set()
    monkeypatch.setattr(friends_module, "SchulmanagerClient", FakeSchulmanager)
    monkeypatch.setattr(sync_module, "SchulmanagerClient", FakeSchulmanager)

    async def no_refresh(*_args, **_kwargs):
        return None

    monkeypatch.setattr(SchoolCalendar, "refresh", no_refresh)  # keine Ferien aus dem Netz
    main = importlib.reload(main)
    owner = TestClient(main.app)
    owner.post("/api/login", json={"password": "geheim"})
    guest = TestClient(main.app)  # ohne Anmeldung, wie ein Freund
    return owner, guest, main


def invite(owner, label="Max"):
    created = owner.post("/api/friends/invite", json={"label": label})
    assert created.status_code == 200, created.text
    return created.json()["url"].rsplit("/", 1)[1]


def redeem(guest, token, email="max@schule.de", password=PASSWORD):
    return guest.post(f"/api/invite/{token}", json={"email": email, "password": password, "consent": True})


def make_friend(owner, guest, label="Max", email="max@schule.de"):
    response = redeem(guest, invite(owner, label), email)
    assert response.status_code == 200, response.text
    return response.json()["manage"].rsplit("/", 1)[1]


# ── Zugangsdaten ─────────────────────────────────────────────────────

def test_vault_encrypts_and_needs_its_key(tmp_path):
    vault = Vault(tmp_path / "accounts.key")
    sealed = vault.seal({"email": "a@b.de", "password": PASSWORD})
    assert PASSWORD not in sealed and "a@b.de" not in sealed
    assert Vault(tmp_path / "accounts.key").open(sealed) == {"email": "a@b.de", "password": PASSWORD}
    with pytest.raises(InvalidToken):
        Vault(tmp_path / "anderer-schluessel.key").open(sealed)


# ── Einladung und Einrichtung ────────────────────────────────────────

def test_owner_endpoints_need_login(setup):
    _, guest, _ = setup
    assert guest.get("/api/friends").status_code == 401
    assert guest.post("/api/friends/invite", json={"label": "x"}).status_code == 401
    assert guest.delete("/api/friends/f-1").status_code == 401


def test_invite_flow_end_to_end(setup):
    owner, guest, main = setup
    token = invite(owner)
    assert guest.get(f"/api/invite/{token}").json() == {"label": "Max"}

    assert redeem(guest, token, password="falsch").json()["detail"] == "Benutzername oder Passwort falsch."
    assert guest.post(f"/api/invite/{token}", json={"email": "m@s.de", "password": PASSWORD}).status_code == 400  # ohne Zustimmung

    response = redeem(guest, token)
    assert response.status_code == 200
    manage = response.json()["manage"].rsplit("/", 1)[1]

    page = guest.get(f"/api/friend/{manage}").json()
    assert page["label"] == "Max" and page["state"] == "ok" and page["account"]["class"] == "Q1"
    assert page["url"].endswith(".ics") and page["webcal"].startswith("webcal://")

    # Die Einladung ist verbraucht
    assert guest.get(f"/api/invite/{token}").status_code == 404
    assert redeem(guest, token).status_code == 400

    feed = guest.get(f"/cal/{page['url'].rsplit('/', 1)[1]}")
    assert feed.status_code == 200 and "Fach-max@schule.de" in feed.text
    # Die Kalender der Freunde bleiben, wie sie waren: auch mit den Schulterminen im selben Kalender
    assert "SUMMARY:Wandertag" in feed.text and "X-WR-CALNAME:Schule\r\n" in feed.text


def test_password_is_never_exposed_or_stored_readable(setup):
    owner, guest, main = setup
    make_friend(owner, guest)
    listing = owner.get("/api/friends")
    assert PASSWORD not in listing.text and "creds" not in listing.text and "manage_token" not in listing.text
    stored = main.service.friends.entries()[0]["creds"]
    assert PASSWORD not in stored
    # Auch in der Datenbankdatei steht es nicht im Klartext
    files = [p for p in main.cfg.data_dir.rglob("*") if p.is_file() and p.name != "accounts.key"]
    assert any(p.name == "schule.db" and p.parent.name != main.cfg.data_dir.name for p in files)  # Datenbank des Freundes
    assert all(PASSWORD.encode() not in p.read_bytes() for p in files)


def test_friends_only_fetch_what_the_calendar_needs(setup):
    owner, guest, _ = setup
    FakeSchulmanager.requested.clear()
    make_friend(owner, guest)
    assert set(FakeSchulmanager.requested) == {"get-actual-lessons", "get-exams", "get-events-for-user"}
    assert not {"get-letters", "get-subscriptions", "get-homework"} & set(FakeSchulmanager.requested)


def test_first_login_is_reused_for_the_first_sync(setup):
    owner, guest, _ = setup
    FakeSchulmanager.logins.clear()
    make_friend(owner, guest)
    assert FakeSchulmanager.logins == ["max@schule.de"]  # eine Anmeldung für Prüfung und ersten Abruf


def test_friends_do_not_see_each_other(setup):
    owner, guest, _ = setup
    a = guest.get(f"/api/friend/{make_friend(owner, guest, 'A', 'a@schule.de')}").json()
    b = guest.get(f"/api/friend/{make_friend(owner, guest, 'B', 'b@schule.de')}").json()
    feed_a = guest.get(f"/cal/{a['url'].rsplit('/', 1)[1]}").text
    feed_b = guest.get(f"/cal/{b['url'].rsplit('/', 1)[1]}").text
    assert "Fach-a@schule.de" in feed_a and "Fach-b@schule.de" not in feed_a
    assert "Fach-b@schule.de" in feed_b and "Fach-a@schule.de" not in feed_b


def test_friend_notifications_stay_off(setup):
    owner, guest, main = setup
    make_friend(owner, guest)
    friend = next(iter(main.service.friends._services.values()))
    settings = friend.db.settings()
    assert not any(value for key, value in settings.items() if key.startswith("notify_"))
    assert not (main.cfg.data_dir / "accounts" / friend.id / "raw").exists()  # keine Rohdaten auf der Platte


def test_invite_dies_after_too_many_wrong_passwords(setup):
    owner, guest, _ = setup
    token = invite(owner)
    for _ in range(5):
        assert redeem(guest, token, password="falsch").status_code == 400
    assert guest.get(f"/api/invite/{token}").status_code == 404  # verfallen, auch mit richtigem Passwort
    assert redeem(guest, token).status_code == 400


def test_expired_and_revoked_invites_do_not_work(setup):
    owner, guest, main = setup
    old = invite(owner, "Alt")
    items = main.service.friends.invites()
    items[0]["expires"] = "2020-01-01T00:00:00+00:00"
    main.service.db.set("friend_invites", items)
    assert guest.get(f"/api/invite/{old}").status_code == 404

    fresh = invite(owner, "Neu")
    assert owner.delete(f"/api/friends/invite/{fresh}").json() == {"ok": True}
    assert guest.get(f"/api/invite/{fresh}").status_code == 404
    assert owner.delete(f"/api/friends/invite/{fresh}").status_code == 404


def test_invite_needs_a_name_and_respects_limit(setup):
    owner, _, _ = setup
    assert owner.post("/api/friends/invite", json={"label": "  "}).status_code == 400
    assert owner.post("/api/friends/invite", json={"label": "x" * 41}).status_code == 400
    for i in range(10):
        assert owner.post("/api/friends/invite", json={"label": f"F{i}"}).status_code == 200
    assert owner.post("/api/friends/invite", json={"label": "zu viel"}).status_code == 400


def test_account_without_student_is_rejected(setup, monkeypatch):
    owner, guest, _ = setup

    async def teacher_login(self):
        self.token, self.user = "jwt", {"firstname": "Lehrer"}

    monkeypatch.setattr(FakeSchulmanager, "login", teacher_login)
    response = redeem(guest, invite(owner))
    assert response.status_code == 400 and "kein Schüler" in response.json()["detail"]


# ── Betrieb ──────────────────────────────────────────────────────────

def test_wrong_password_stops_polling_until_renewed(setup):
    owner, guest, main = setup
    manage = make_friend(owner, guest)
    friends = main.service.friends
    entry = friends.entries()[0]

    FakeSchulmanager.expired_for.add("max@schule.de")  # Passwort wurde inzwischen geändert
    result = asyncio.run(friends._poll(entry, "schedule"))
    assert result.get("auth") and friends.entries()[0]["needs_login"]
    assert guest.get(f"/api/friend/{manage}").json()["state"] == "needs_login"
    assert owner.get("/api/friends").json()["friends"][0]["state"] == "needs_login"

    # Es wird nicht weiter probiert: sonst sperrt Schulmanager das Konto
    FakeSchulmanager.logins.clear()
    friends._next_poll[entry["id"]] = 0
    asyncio.run(friends.poll_due(True))
    assert FakeSchulmanager.logins == []
    assert owner.post(f"/api/friends/{entry['id']}/sync").status_code == 400

    # Der Freund erneuert sein Login auf seiner Seite
    assert guest.post(f"/api/friend/{manage}/login", json={"email": "max@schule.de", "password": "falsch"}).status_code == 400
    FakeSchulmanager.expired_for.clear()
    renewed = guest.post(f"/api/friend/{manage}/login", json={"email": "max@schule.de", "password": "neues-passwort"})
    assert renewed.status_code == 200 and renewed.json()["state"] == "ok"
    assert not friends.entries()[0]["needs_login"]


def test_polls_are_staggered_one_friend_at_a_time(setup):
    owner, guest, main = setup
    for i in range(3):
        make_friend(owner, guest, f"F{i}", f"f{i}@schule.de")
    friends = main.service.friends
    for entry in friends.entries():
        friends._next_poll[entry["id"]] = 0  # alle gleichzeitig fällig

    FakeSchulmanager.requested.clear()
    asyncio.run(friends.poll_due(True))
    assert FakeSchulmanager.requested.count("get-actual-lessons") == 1  # nur einer
    asyncio.run(friends.poll_due(True))
    asyncio.run(friends.poll_due(True))
    assert FakeSchulmanager.requested.count("get-actual-lessons") == 3
    asyncio.run(friends.poll_due(True))
    assert FakeSchulmanager.requested.count("get-actual-lessons") == 3  # jetzt wieder in der Zukunft


def test_next_poll_is_hours_away_not_minutes(setup):
    owner, guest, main = setup
    make_friend(owner, guest)
    friends = main.service.friends
    entry = friends.entries()[0]
    friends._next_poll[entry["id"]] = 0
    import time

    before = time.monotonic()
    asyncio.run(friends.poll_due(True))
    assert friends._next_poll[entry["id"]] - before >= 60 * 60  # an Schultagen mindestens eine Stunde
    friends._next_poll[entry["id"]] = 0
    before = time.monotonic()
    asyncio.run(friends.poll_due(False))
    assert friends._next_poll[entry["id"]] - before >= 4 * 60 * 60  # am Wochenende seltener


def test_regenerating_the_link_invalidates_the_old_one(setup):
    owner, guest, _ = setup
    manage = make_friend(owner, guest)
    old = guest.get(f"/api/friend/{manage}").json()["url"].rsplit("/", 1)[1]
    new = guest.post(f"/api/friend/{manage}/regenerate").json()["url"].rsplit("/", 1)[1]
    assert new != old
    assert guest.get(f"/cal/{old}").status_code == 404
    assert guest.get(f"/cal/{new}").status_code == 200


def test_friend_can_delete_everything(setup):
    owner, guest, main = setup
    manage = make_friend(owner, guest)
    friend_id = main.service.friends.entries()[0]["id"]
    ical_token = guest.get(f"/api/friend/{manage}").json()["url"].rsplit("/", 1)[1]
    directory = main.cfg.data_dir / "accounts" / friend_id
    assert directory.exists()

    assert guest.delete(f"/api/friend/{manage}").json() == {"ok": True}
    assert not directory.exists() and main.service.friends.entries() == []
    assert guest.get(f"/api/friend/{manage}").status_code == 404
    assert guest.get(f"/cal/{ical_token}").status_code == 404
    assert owner.get("/api/friends").json()["friends"] == []


def test_owner_can_remove_a_friend(setup):
    owner, guest, main = setup
    make_friend(owner, guest)
    friend_id = owner.get("/api/friends").json()["friends"][0]["id"]
    assert owner.delete(f"/api/friends/{friend_id}").json() == {"ok": True}
    assert not (main.cfg.data_dir / "accounts" / friend_id).exists()
    assert owner.delete(f"/api/friends/{friend_id}").status_code == 404


def test_owner_calendar_is_unchanged(setup):
    owner, _, main = setup
    token = owner.get("/api/ical").json()["url"].rsplit("/", 1)[1]
    assert owner.get(f"/cal/{token}").status_code == 200
    assert owner.get("/cal/unbekannt.ics").status_code == 404


# ── Seiten ───────────────────────────────────────────────────────────

def test_guest_pages_are_public_and_not_cached(setup):
    _, guest, main = setup
    for path in ("/einladung/irgendwas", "/freund/irgendwas"):
        page = guest.get(path)
        assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
        assert "noindex" in page.headers["x-robots-tag"]
        assert f'src="/a/{main.BUILD_ID}/js/gast.js"' in page.text
    assert guest.get("/api/invite/irgendwas").status_code == 404
    assert guest.get("/api/friend/irgendwas").status_code == 404


def test_guessing_links_is_rate_limited(setup):
    _, guest, _ = setup
    codes = [guest.get(f"/api/friend/raten{i}").status_code for i in range(15)]
    assert codes[:12] == [404] * 12 and codes[12:] == [429] * 3


def test_rate_limit_cannot_be_dodged_with_a_fake_forwarded_header(setup):
    _, guest, _ = setup
    codes = [
        guest.get(f"/api/friend/raten{i}", headers={"CF-Connecting-IP": "9.9.9.9", "X-Forwarded-For": f"10.0.0.{i}"}).status_code
        for i in range(15)
    ]
    assert codes[:12] == [404] * 12 and codes[12:] == [429] * 3


def test_service_worker_ignores_guest_pages():
    from pathlib import Path

    sw = (Path(__file__).resolve().parents[2] / "frontend" / "sw.js").read_text(encoding="utf-8")
    for path in ("/cal/", "/einladung/", "/freund/", "/api/invite/", "/api/friend/"):
        assert f'"{path}"' in sw
