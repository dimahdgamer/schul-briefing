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


def _first_thread(http):
    http.post("/api/login", json={"password": "geheim"})
    return http.get("/api/inbox").json()["threads"][0]["subscription_id"]


def test_thread_messages_in_demo(client):
    http, _ = client
    assert http.get("/api/thread/501").status_code == 401  # ohne Anmeldung
    sub = _first_thread(http)
    body = http.get(f"/api/thread/{sub}").json()
    assert body["thread"]["subscription_id"] == sub and body["thread"]["subject"] and body["thread"]["url"]
    assert [m["mine"] for m in body["items"]] == [True, False] and body["more"] is True  # älteste zuerst

    older = http.get(f"/api/thread/{sub}?before={body['items'][0]['id']}").json()  # Nachladen
    assert len(older["items"]) == 1 and older["more"] is False
    assert int(older["items"][0]["id"]) < int(body["items"][0]["id"])

    assert http.get("/api/thread/999999").status_code == 404  # nur Unterhaltungen aus dem letzten Abruf
    assert http.get(f"/api/thread/{sub}?before=abc").status_code == 400


def test_thread_messages_only_call_the_read_endpoint(client, monkeypatch):
    from app.schulmanager import RpcResult, SchulmanagerError

    http, main = client
    sub = _first_thread(http)
    sent = []
    reply = {"hasMoreMessages": False, "messages": [
        {"id": "20", "text": "Hallo", "createdAt": "2026-10-15T09:00:00.000Z", "senderId": 77,
         "sender": {"firstname": "Ich", "lastname": "Selbst"}, "attachments": []},
        {"id": "10", "text": "Früher", "createdAt": "2026-10-14T09:00:00.000Z", "senderId": 5,
         "sender": {"firstname": "", "lastname": "Krüger"}, "attachments": []},
    ]}
    outcome = {"result": RpcResult(200, reply)}

    class FakeClient:
        user = {"id": 77}

        async def calls(self, requests):
            sent.extend(requests)
            if isinstance(outcome["result"], Exception):
                raise outcome["result"]
            return [outcome["result"]]

    monkeypatch.setattr(main.owner.sync, "demo", None)
    monkeypatch.setattr(main.owner.sync, "client", FakeClient())

    body = http.get(f"/api/thread/{sub}").json()
    assert [(m["id"], m["mine"]) for m in body["items"]] == [("10", False), ("20", True)]
    http.get(f"/api/thread/{sub}?before=10")
    assert [(c.module, c.endpoint) for c in sent] == [("messenger", "get-messages-by-subscription")] * 2
    assert sent[0].parameters == {"subscriptionId": sub, "before": None, "loadAll": False}
    assert sent[1].parameters["before"] == 10  # Zahl, so wie die Website sie schickt

    outcome["result"] = RpcResult(403, None)
    assert http.get(f"/api/thread/{sub}").status_code == 502
    outcome["result"] = SchulmanagerError("Rate-Limit erreicht", 429, "rate-limit")
    failed = http.get(f"/api/thread/{sub}")
    assert failed.status_code == 502 and "Rate-Limit" in failed.json()["detail"]


def _unread_thread(http):
    threads = http.get("/api/inbox").json()["threads"]
    return next(t["subscription_id"] for t in threads if t["unread"])


def _unread_of(http, sub):
    return next(t for t in http.get("/api/inbox").json()["threads"] if t["subscription_id"] == sub)["unread"]


def test_mark_thread_read_in_demo(client):
    http, _ = client
    assert http.post("/api/thread/501/read", json={"message_id": "10"}).status_code == 401  # ohne Anmeldung
    http.post("/api/login", json={"password": "geheim"})
    sub = _unread_thread(http)
    assert http.post(f"/api/thread/{sub}/read", json={"message_id": "10"}).json() == {"unread": 0, "changed": True}
    assert _unread_of(http, sub) == 0  # Liste und Zähler stimmen sofort
    assert http.post(f"/api/thread/{sub}/read", json={"message_id": "10"}).json() == {"unread": 0, "changed": False}
    assert http.post(f"/api/thread/{sub}/read", json={"message_id": "abc"}).status_code == 400
    assert http.post(f"/api/thread/{sub}/read", json={}).status_code == 400
    assert http.post("/api/thread/999999/read", json={"message_id": "10"}).status_code == 404


def test_mark_thread_read_sends_the_call_of_the_website(client, monkeypatch):
    from app.schulmanager import RpcResult

    http, main = client
    http.post("/api/login", json={"password": "geheim"})
    sub = _unread_thread(http)
    sent = []
    outcome = {"result": RpcResult(200, {"ok": True})}

    class FakeClient:
        user = {"id": 77}

        async def calls(self, requests):
            sent.extend(requests)
            return [outcome["result"]]

    monkeypatch.setattr(main.owner.sync, "demo", None)
    monkeypatch.setattr(main.owner.sync, "client", FakeClient())

    # Fehler von Schulmanager: nichts wird lokal als gelesen gemerkt
    outcome["result"] = RpcResult(403, None)
    assert http.post(f"/api/thread/{sub}/read", json={"message_id": "20"}).status_code == 502
    assert _unread_of(http, sub) > 0

    outcome["result"] = RpcResult(200, {"ok": True})
    assert http.post(f"/api/thread/{sub}/read", json={"message_id": "20"}).json()["changed"] is True
    assert _unread_of(http, sub) == 0
    assert [(c.module, c.endpoint) for c in sent] == [("messenger", "set-subscription-read")] * 2
    # genau so ruft es die Website beim Öffnen auf
    assert sent[-1].parameters == {"subscription": {"id": sub}, "message": {"id": "20"}, "scope": "multiple"}

    # Ist nichts ungelesen, wird nichts gesendet
    count = len(sent)
    assert http.post(f"/api/thread/{sub}/read", json={"message_id": "20"}).json()["changed"] is False
    assert len(sent) == count


def test_attachments_in_demo(client):
    http, main = client
    assert http.get("/api/thread/501/file/1/2").status_code == 401  # ohne Anmeldung
    sub = _first_thread(http)
    newest = http.get(f"/api/thread/{sub}").json()["items"][-1]
    image, pdf, other = newest["attachments"]
    assert (image["type"], image["name"]) == ("image/png", "Treffpunkt.png") and image["size"] > 0
    assert all(set(a) == {"id", "name", "type", "size"} for a in newest["attachments"])  # kein Schlüssel nach außen

    base = f"/api/thread/{sub}/file/{newest['id']}/"
    shown = http.get(base + image["id"])
    assert shown.status_code == 200 and shown.headers["content-type"] == "image/png" and shown.content.startswith(b"\x89PNG")
    assert shown.headers["content-disposition"].startswith("inline; filename*=UTF-8''Treffpunkt.png")
    assert "sandbox" in shown.headers["content-security-policy"]
    assert shown.headers["cache-control"] == "private, no-store"  # nicht im Browser-Cache

    viewed = http.get(base + pdf["id"])
    assert viewed.headers["content-type"] == "application/pdf" and viewed.headers["content-disposition"].startswith("inline")

    saved = http.get(base + other["id"])  # ZIP: nie direkt anzeigen
    assert saved.headers["content-type"] == "application/octet-stream" and saved.headers["content-disposition"].startswith("attachment")
    forced = http.get(base + image["id"] + "?download=1")
    assert forced.headers["content-disposition"].startswith("attachment") and forced.headers["content-type"] == "application/octet-stream"

    main.owner._files.clear()  # wie nach einem Neustart: die Nachricht wird dafür kurz neu geladen
    assert http.get(base + image["id"]).status_code == 200

    assert http.get(base + "99999").status_code == 404
    assert http.get(f"/api/thread/999999/file/{newest['id']}/{image['id']}").status_code == 404
    assert http.get(f"/api/thread/{sub}/file/abc/{image['id']}").status_code == 400
    assert http.get(base + "a$b").status_code == 400


def test_attachment_download_only_serves_safe_types_inline(client, monkeypatch):
    import base64
    import json

    import httpx

    import app.service as service_module
    from app.schulmanager import RpcResult

    http, main = client
    sub = _first_thread(http)
    jpeg = b"\xff\xd8\xff\xe0" + b"0" * 20
    page = b"<html><script>alert(1)</script></html>"
    downloads = {}  # Adresse -> Antwort des Datei-Servers

    def attachment(number, mime, size, name, content):
        text = json.dumps([1, "messenger", str(number), f"SCHLUESSEL-{number}", mime, size, name])
        downloads["https://login.schulmanager-online.de/storage/download-file/" + base64.b64encode(text.encode()).decode()] = content
        return {"id": str(number), "file": text}

    files = [
        attachment(1, "image/jpeg", len(jpeg), "Ärger Foto.jpeg", jpeg),
        attachment(2, "image/jpeg", len(page), "falsch.jpeg", page),  # behauptet Bild, ist eine Seite
        attachment(3, "image/svg+xml", len(page), "bild.svg", page),  # SVG kann Skripte enthalten
        attachment(4, "application/pdf", 30 * 1024 * 1024, "riesig.pdf", b"%PDF"),  # über dem Limit, wird nicht geladen
        attachment(5, "application/pdf", 10, "luegt.pdf", b"%PDF" + b"x" * service_module.FILE_LIMIT),  # größer als angegeben
        attachment(6, "application/pdf", 10, "weg.pdf", None),  # Datei-Server antwortet mit 404
    ]
    reply = {"hasMoreMessages": False, "messages": [
        {"id": "20", "text": "Anhänge", "createdAt": "2026-10-15T09:00:00.000Z", "senderId": 5,
         "sender": {"firstname": "", "lastname": "Krüger"}, "attachments": files}]}
    sent, seen = [], []
    storage = {"url": "https://login.schulmanager-online.de/storage"}

    class FakeClient:
        user = {"id": 77}

        async def calls(self, requests):
            sent.extend(requests)
            return [RpcResult(200, reply if r.endpoint == "get-messages-by-subscription" else storage["url"]) for r in requests]

    def handler(request):
        seen.append(request)
        content = downloads.get(str(request.url))
        return httpx.Response(200 if content is not None else 404, content=content or b"")

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(main.owner.sync, "demo", None)
    monkeypatch.setattr(main.owner.sync, "client", FakeClient())

    base = f"/api/thread/{sub}/file/20/"
    http.get(f"/api/thread/{sub}")  # Ansicht geöffnet: die Anhänge sind bekannt

    ok = http.get(base + "1")
    assert ok.status_code == 200 and ok.content == jpeg and ok.headers["content-type"] == "image/jpeg"
    assert ok.headers["content-disposition"] == "inline; filename*=UTF-8''%C3%84rger%20Foto.jpeg"
    assert "authorization" not in seen[-1].headers  # die Anmeldung geht nie an den Datei-Server
    assert [c.endpoint for c in sent].count("get-messages-by-subscription") == 1  # Anhang kam aus dem Speicher

    for number in ("2", "3"):  # Typ passt nicht zum Inhalt, bzw. nicht anzeigbar: nur als Download
        risky = http.get(base + number)
        assert risky.headers["content-disposition"].startswith("attachment") and risky.headers["content-type"] == "application/octet-stream"
        assert "content-security-policy" not in risky.headers

    assert http.get(base + "4").status_code == 413 and len(seen) == 3  # laut Angabe zu groß: nicht mal geladen
    assert http.get(base + "5").status_code == 413  # größer als angegeben: Abbruch beim Laden
    assert http.get(base + "6").status_code == 502  # Datei-Server sagt 404

    # Nach einem Neustart: Nachricht wird neu geladen (`before` ist exklusiv, also ID + 1) und die Adresse erneut geholt
    main.owner._files.clear()
    sent.clear()
    assert http.get(base + "1").status_code == 200
    (reload,) = [c for c in sent if c.endpoint == "get-messages-by-subscription"]
    assert reload.parameters["before"] == 21

    # Eine unerwartete Adresse für Dateien wird nicht benutzt
    main.owner._storage_url = None
    storage["url"] = "https://evil.example/storage"
    failed = http.get(base + "1")
    assert failed.status_code == 502 and "Adresse" in failed.json()["detail"]
    storage["url"] = "http://login.schulmanager-online.de/storage"  # auch kein Klartext-http
    assert http.get(base + "1").status_code == 502


def _threads(http):
    return http.get("/api/inbox").json()["threads"]


def _send(http, sub, text="Hallo", request_id="req-00000001"):
    return http.post(f"/api/thread/{sub}/send", json={"text": text, "request_id": request_id})


def test_send_message_in_demo(client):
    http, _ = client
    assert _send(http, "501").status_code == 401  # ohne Anmeldung
    http.post("/api/login", json={"password": "geheim"})
    threads = _threads(http)
    allowed = next(t for t in threads if t["can_answer"])["subscription_id"]
    forbidden = next(t for t in threads if not t["can_answer"])["subscription_id"]

    text = "Hallo Frau Vogt,\r\n\r\nbis morgen!   \n"
    sent = _send(http, allowed, text).json()
    assert sent["duplicate"] is False
    message = sent["message"]
    assert message["mine"] is True and message["text"] == "Hallo Frau Vogt,\n\nbis morgen!"  # Zeilenumbrüche bleiben, Ränder weg

    again = _send(http, allowed, text).json()  # derselbe Versuch (Doppeltipp): nicht noch einmal
    assert again["duplicate"] is True and again["message"]["id"] == message["id"]
    assert [m["id"] for m in http.get(f"/api/thread/{allowed}").json()["items"]].count(message["id"]) == 1

    assert _send(http, allowed, "", "req-00000002").status_code == 400
    assert _send(http, allowed, " \n ", "req-00000002").status_code == 400
    assert _send(http, allowed, "x" * 3001, "req-00000002").status_code == 400
    assert _send(http, allowed, "x" * 3000, "req-00000003").status_code == 200  # genau das Limit geht
    assert _send(http, allowed, "Hallo", "kurz").status_code == 400  # ungültige request_id
    assert http.post(f"/api/thread/{allowed}/send", json={"text": 5, "request_id": "req-00000004"}).status_code == 400
    assert _send(http, forbidden, "Hallo", "req-00000005").status_code == 403  # hier sind keine Antworten erlaubt
    assert _send(http, "999999", "Hallo", "req-00000006").status_code == 404


class _Sender:
    """Stand-in für den Schulmanager-Client: merkt sich die Aufrufe und liefert vorbereitete Antworten."""

    def __init__(self, sub, thread, admin=False):
        from app.schulmanager import RpcResult

        self.user = {"id": 77}
        self.sent = []
        self.thread = thread
        self.subscription = {"id": sub, "threadId": thread["id"], "isAdministrator": admin, "unreadCount": 0, "thread": thread}
        self.listing = [self.subscription]
        self.reply = RpcResult(200, {"id": "30", "text": "x", "createdAt": "2026-10-15T09:00:00.000Z", "senderId": 77,
                                     "sender": {"firstname": "Ich", "lastname": "Selbst"}, "attachments": []})

    async def calls(self, requests):
        from app.schulmanager import RpcResult

        out = []
        for request in requests:
            self.sent.append(request)
            if request.endpoint == "get-subscriptions":
                out.append(RpcResult(200, self.listing))
            elif isinstance(self.reply, Exception):
                raise self.reply
            else:
                out.append(self.reply)
        return out

    def endpoints(self):
        return [c.endpoint for c in self.sent]


def _real_sender(client, monkeypatch, allow=True, admin=False):
    http, main = client
    http.post("/api/login", json={"password": "geheim"})
    sub = next(t for t in _threads(http) if t["can_answer"])["subscription_id"]
    thread = {"id": "9001", "subject": "Material", "senderString": "Vogt", "recipientString": "9b", "isPrivateChat": False,
              "allowAnswers": allow, "lastMessageTimestamp": "2026-10-15T08:00:00.000Z"}
    fake = _Sender(sub, thread, admin)
    monkeypatch.setattr(main.owner.sync, "demo", None)
    monkeypatch.setattr(main.owner.sync, "client", fake)
    return http, sub, fake


def test_send_message_sends_what_the_website_sends(client, monkeypatch):
    http, sub, fake = _real_sender(client, monkeypatch)
    body = _send(http, sub, "Hallo\n\nZweite Zeile").json()
    assert fake.endpoints() == ["get-subscriptions", "send-message"]
    send = fake.sent[-1]
    assert (send.module, send.endpoint) == ("messenger", "send-message")
    # die Unterhaltung genau so, wie sie Schulmanager geliefert hat, dazu Text mit Zeilenumbrüchen, keine Anhänge
    assert send.parameters == {"thread": fake.thread, "text": "Hallo\n\nZweite Zeile", "attachments": []}
    assert "urgent" not in send.parameters  # nie als dringlich
    assert body["message"]["id"] == "30" and body["message"]["mine"] is True and body["duplicate"] is False

    assert _send(http, sub, "Hallo\n\nZweite Zeile").json()["duplicate"] is True  # dieselbe request_id
    assert fake.endpoints().count("send-message") == 1  # und wurde nicht noch einmal gesendet


def test_send_message_checks_the_current_rights_and_reports_refusals(client, monkeypatch):
    from app.schulmanager import RpcResult, SchulmanagerError

    # Antworten sind inzwischen nicht mehr erlaubt (der lokale Stand ist älter): es wird nichts gesendet
    http, sub, fake = _real_sender(client, monkeypatch, allow=False)
    assert _send(http, sub).status_code == 403 and "send-message" not in fake.endpoints()
    fake.subscription["isAdministrator"] = True  # Administrator der Unterhaltung darf trotzdem
    assert _send(http, sub, "Hallo", "req-00000002").status_code == 200
    fake.listing = []  # die Unterhaltung gibt es bei Schulmanager nicht mehr
    assert _send(http, sub, "Hallo", "req-00000003").status_code == 404

    fake.listing = [fake.subscription]
    fake.reply = RpcResult(400, None, {"errorType": "quiet_hours_active", "germanErrorMessage": "Ruhezeit"})
    refused = _send(http, sub, "Hallo", "req-00000004")
    assert refused.status_code == 502 and "Ruhezeit" in refused.json()["detail"] and "nicht gesendet" in refused.json()["detail"]
    fake.reply = RpcResult(400, None, {"errorType": "x", "germanErrorMessage": "Sie dürfen hier nicht schreiben."})
    assert "Sie dürfen hier nicht schreiben." in _send(http, sub, "Hallo", "req-00000005").json()["detail"]
    fake.reply = RpcResult(500, None)
    assert "HTTP 500" in _send(http, sub, "Hallo", "req-00000006").json()["detail"]

    # Verbindung bricht nach dem Senden ab: Ob es ankam, ist unklar. Das sagt die App, statt blind zu wiederholen.
    fake.reply = SchulmanagerError("Netzwerkfehler", 0, "network")
    unclear = _send(http, sub, "Hallo", "req-00000007")
    assert unclear.status_code == 502 and "unklar" in unclear.json()["detail"]
    fake.reply = SchulmanagerError("Rate-Limit erreicht", 429, "rate-limit")  # da ging nichts raus
    assert "nicht gesendet" in _send(http, sub, "Hallo", "req-00000008").json()["detail"]
    # Ein Fehlversuch zählt nicht als gesendet: derselbe Text mit derselben ID darf danach klappen
    fake.reply = RpcResult(200, {"id": "31", "text": "Hallo", "createdAt": "2026-10-15T09:01:00.000Z", "senderId": 77,
                                 "sender": {}, "attachments": []})
    assert _send(http, sub, "Hallo", "req-00000008").json()["message"]["id"] == "31"

    # Unerwartete Antwort: die Nachricht kam an, die Ansicht lädt dann neu (message ist leer)
    fake.reply = RpcResult(200, None)
    assert _send(http, sub, "Noch eine", "req-00000009").json()["message"] is None


def test_send_message_is_rate_limited(client, monkeypatch):
    http, sub, fake = _real_sender(client, monkeypatch)
    for number in range(6):
        assert _send(http, sub, f"Nachricht {number}", f"req-1000000{number}").status_code == 200
    blocked = _send(http, sub, "Nachricht 7", "req-10000007")
    assert blocked.status_code == 429 and fake.endpoints().count("send-message") == 6  # die siebte geht nicht raus


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


# ── Eigener wiederkehrender Unterricht ───────────────────────────────

COURSE = {"subject": "Russisch", "weekdays": [3], "start": "15:00", "end": "17:15", "place": "Andere Schule"}


def test_course_crud(client):
    http, _ = client
    assert http.post("/api/own/courses", json=COURSE).status_code == 401
    http.post("/api/login", json={"password": "geheim"})
    created = http.post("/api/own/courses", json=COURSE)
    assert created.status_code == 200
    item = created.json()
    listed = http.get("/api/own").json()["courses"]
    assert listed[0]["id"] == item["id"] and listed[0]["label"] == "Do · 15:00–17:15 · 9–11. Std"
    # Der nächste Termin ist ein Donnerstag in den nächsten Tagen (zeigt in den Einstellungen, dass der Eintrag greift)
    assert date.fromisoformat(listed[0]["next"]).weekday() == 3
    assert 0 <= (date.fromisoformat(listed[0]["next"]) - date.today()).days <= 7 * 20

    changed = http.put(f"/api/own/courses/{item['id']}", json=dict(COURSE, weekdays=[1, 3])).json()
    assert changed["weekdays"] == [1, 3]
    assert http.post("/api/own/courses", json=dict(COURSE, weekdays=[])).status_code == 400
    assert http.delete(f"/api/own/courses/{item['id']}").json() == {"ok": True}
    assert http.get("/api/own").json()["courses"] == []


def test_overview_and_week_say_whether_own_lessons_exist(client):
    """Heute und Woche bieten das Eintragen an, solange noch kein eigener Unterricht da ist."""
    http, _ = client
    http.post("/api/login", json={"password": "geheim"})
    assert http.get("/api/overview").json()["has_courses"] is False
    assert http.get("/api/week").json()["has_courses"] is False
    http.post("/api/own/courses", json=COURSE)
    assert http.get("/api/overview").json()["has_courses"] is True
    assert http.get("/api/week").json()["has_courses"] is True


def test_course_shows_in_day_and_week_and_follows_leave(client):
    http, main = client
    http.post("/api/login", json={"password": "geheim"})
    http.post("/api/own/courses", json=COURSE)
    main.service.db.save_snapshot("lessons", [
        {"id": "a", "date": "2026-10-08", "hour": "1", "state": "regular", "start": "07:55", "end": "08:40", "subject": "Mathematik"},
    ])
    day = http.get("/api/overview?date=2026-10-08").json()["day"]
    assert [(l["state"], l["subject"]) for l in day["lessons"]] == [("regular", "Mathematik"), ("external", "Russisch")]
    assert (day["start"], day["end"]) == ("07:55", "17:15")

    week = http.get("/api/week?start=2026-10-05").json()
    thursday = week["days"][3]["lessons"]
    assert [l["subject"] for l in thursday if l["state"] == "external"] == ["Russisch"]
    assert not [l for d in week["days"][:3] + week["days"][4:] for l in d["lessons"] if l["state"] == "external"]

    # Ganztägig beurlaubt: auch Russisch entfällt für dich
    http.post("/api/own/leaves", json={"from": "2026-10-08", "to": "2026-10-08"})
    day = http.get("/api/overview?date=2026-10-08").json()["day"]
    assert [(l["state"], l["subject"]) for l in day["lessons"]] == [("leave", "Mathematik"), ("leave", "Russisch")]
    assert day["all_cancelled"] and day["start"] == ""


def test_course_is_not_touched_by_a_school_free_day(client):
    """Ein Lehrertag oder Studientag deiner Schule betrifft nur die Schule, Russisch findet statt."""
    http, main = client
    http.post("/api/login", json={"password": "geheim"})
    http.post("/api/own/courses", json=COURSE)
    main.service.calendar.set_calendar_holidays([{"is_holiday": True, "start": "2026-10-08T00:00:00",
                                                  "end": "2026-10-09T00:00:00", "all_day": True, "title": "Lehrertag"}])
    assert not main.service.calendar.is_school_day(date(2026, 10, 8))  # für deine Schule ist frei
    day = http.get("/api/overview?date=2026-10-08").json()["day"]
    assert [(l["state"], l["subject"]) for l in day["lessons"] if l["subject"] == "Russisch"] == [("external", "Russisch")]


def test_leave_by_hours_only_hits_courses_in_that_time(client):
    http, main = client
    http.post("/api/login", json={"password": "geheim"})
    http.post("/api/own/courses", json=COURSE)
    http.post("/api/own/leaves", json={"from": "2026-10-08", "hour_from": "3", "hour_to": "4"})  # Vormittag
    day = http.get("/api/overview?date=2026-10-08").json()["day"]
    assert [l["state"] for l in day["lessons"] if l["subject"] == "Russisch"] == ["external"]
    http.post("/api/own/leaves", json={"from": "2026-10-08", "hour_from": "10", "hour_to": "11"})  # Nachmittag
    day = http.get("/api/overview?date=2026-10-08").json()["day"]
    assert [l["state"] for l in day["lessons"] if l["subject"] == "Russisch"] == ["leave"]


def test_day_stays_on_thursday_until_russian_is_over(client, monkeypatch):
    from datetime import datetime

    http, main = client
    service = main.service
    http.post("/api/login", json={"password": "geheim"})
    http.post("/api/own/courses", json=COURSE)
    main.service.db.save_snapshot("lessons", [
        {"id": "a", "date": "2026-10-08", "hour": "1", "state": "regular", "start": "07:55", "end": "13:05", "subject": "Mathematik"},
    ])
    monkeypatch.setattr(service, "now", lambda: datetime(2026, 10, 8, 16, 30, tzinfo=main.cfg.tz))
    assert service.default_day() == date(2026, 10, 8)  # Russisch läuft noch bis 17:15
    monkeypatch.setattr(service, "now", lambda: datetime(2026, 10, 8, 17, 20, tzinfo=main.cfg.tz))
    assert service.default_day() == date(2026, 10, 9)


def test_course_in_calendar_feed_via_api(client):
    http, _ = client
    http.post("/api/login", json={"password": "geheim"})
    http.post("/api/own/courses", json=COURSE)
    token = http.get("/api/ical").json()["url"].rsplit("/", 1)[1]
    feed = http.get(f"/cal/{token}").text
    assert "SUMMARY:Russisch" in feed and "LOCATION:Andere Schule" in feed


# ── Kalender: Stundenplan und Schultermine getrennt ──────────────────

def test_school_events_have_their_own_calendar(client):
    http, main = client
    http.post("/api/login", json={"password": "geheim"})
    main.service.db.save_snapshot("calendar", [{
        "id": "1@2026-10-14", "title": "Elternsprechtag", "start": "2026-10-14T15:00:00", "end": "2026-10-14T19:00:00",
        "all_day": False, "location": "Aula", "description": "", "is_holiday": False}])
    main.service.db.save_snapshot("exams", [{
        "id": "e1", "subject": "Physik", "type": "Test", "date": "2026-10-20", "start": "", "end": "", "hour": "",
        "comment": ""}])
    links = http.get("/api/ical").json()
    token = links["url"].rsplit("/", 1)[1]
    assert links["events_url"].endswith("/cal/" + token + "?termine=1") and links["events_webcal"].startswith("webcal://")

    timetable = http.get(f"/cal/{token}").text
    assert "Elternsprechtag" not in timetable and "SUMMARY:Test: Physik" in timetable
    assert "X-WR-CALNAME:Schule\r\n" in timetable

    events = http.get(f"/cal/{token}?termine=1").text
    assert "SUMMARY:Elternsprechtag" in events and "Physik" not in events and "X-WR-CALNAME:Schultermine" in events
    # Vom Besucher gesendeter Parameter ersetzt das Geheimnis nicht
    assert http.get("/cal/falsch.ics?termine=1").status_code == 404
