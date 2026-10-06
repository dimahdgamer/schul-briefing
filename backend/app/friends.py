"""Freunde: Kalender-Abo und, wenn der Besitzer es anbietet und der Freund zustimmt, eine eigene Oberfläche.

Jeder Freund bekommt eine eigene Datenbank und einen eigenen Abruf unter `data/accounts/<id>/`. Ohne
Oberfläche werden nur Stundenplan, Klassenarbeiten und Schultermine abgerufen, nie Nachrichten oder
Elternbriefe. Mit Oberfläche kommen Hausaufgaben und Fehlzeiten dazu. Das Passwort liegt verschlüsselt in
der Datenbank des Besitzers, der Schlüssel in einer eigenen Datei daneben. Freunde tragen ihr Login selbst
über einen Einladungslink ein, der Besitzer sieht es nie.

Die Oberfläche hat drei Zustände je Freund:
  off      nur Kalender (so haben alle begonnen, die Kalender-Links ändern sich dadurch nie)
  offered  der Besitzer bietet die Oberfläche an, der Freund hat noch nicht zugestimmt, es wird nichts Neues abgerufen
  on       der Freund hat zugestimmt: eigener Zugangscode, eigene App mit Briefing, Push und Einträgen
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import random
import secrets
import shutil
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from . import ical
from .config import Config
from .db import DEFAULT_SETTINGS, Database, now_iso
from .holidays import SchoolCalendar
from .push import Pusher
from .schulmanager import LoginError, SchulmanagerClient, SchulmanagerError
from .sync import SyncService

log = logging.getLogger(__name__)

FRIEND_MODULES = ("lessons", "exams", "calendar")
FRIEND_UI_MODULES = ("lessons", "homework", "exams", "calendar", "absences")
MAX_FRIENDS = 10
INVITE_DAYS = 7
MAX_INVITE_ATTEMPTS = 5
POLL_MINUTES = 60  # an Schultagen
POLL_MINUTES_OFF = 240  # Wochenende und Ferien
UI_POLL_MINUTES = 30  # Freunde mit Oberfläche: wie ein eigenes Konto, nur etwas seltener als der Besitzer
FIRST_POLL_DELAY = 30  # Sekunden nach dem Start, danach je Freund 2 Minuten später
MANUAL_SYNC_PAUSE = 60  # Sekunden zwischen zwei manuellen Abrufen
# Ohne 0, O, 1, I, damit sich der Zugangscode am Handy leicht abtippen lässt
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
FETCHED_BY_UI = ("homework", "absences")  # was nur mit Oberfläche abgerufen wird, geht beim Abschalten wieder weg


class FriendError(ValueError):
    """Fehler mit einer Meldung, die der Freund oder der Besitzer lesen darf."""


def _same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


class Vault:
    """Verschlüsselt die Zugangsdaten. Der Schlüssel liegt getrennt von der Datenbank."""

    def __init__(self, path: Path) -> None:
        if not path.exists():
            path.write_bytes(Fernet.generate_key())
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        self._fernet = Fernet(path.read_bytes().strip())

    def seal(self, data: dict[str, str]) -> str:
        return self._fernet.encrypt(json.dumps(data).encode("utf-8")).decode("ascii")

    def open(self, token: str) -> dict[str, str]:
        return json.loads(self._fernet.decrypt(token.encode("ascii")))


class ForwardPusher:
    """Leitet Fehlermeldungen eines Freundes an den Besitzer weiter, mit dem Namen davor."""

    def __init__(self, pusher: Pusher, label: str) -> None:
        self.pusher = pusher
        self.label = label

    async def send(self, title: str, body: str, url: str = "/", tag: str | None = None) -> int:
        return await self.pusher.send(f"{self.label}: {title}", body, "/#/einstellungen", tag=f"friend-{tag}")


def _notify_keys() -> list[str]:
    return [key for key in DEFAULT_SETTINGS if key.startswith("notify_")]


class FriendService:
    """Konto eines Freundes: eigene Datenbank, eigener Abruf, bei Oberfläche eine vollständige App."""

    def __init__(self, cfg: Config, entry: dict[str, Any], vault: Vault, pusher: Pusher,
                 session: dict[str, Any] | None = None) -> None:
        from .service import AppService  # erst hier: service.py importiert dieses Modul

        try:
            creds = vault.open(entry["creds"])
            email, password = creds["email"], creds["password"]
        except (InvalidToken, ValueError, KeyError) as exc:
            raise FriendError("Die gespeicherten Zugangsdaten sind nicht lesbar.") from exc
        self.id = entry["id"]
        self.tz = cfg.tz
        directory = cfg.data_dir / "accounts" / entry["id"]
        directory.mkdir(parents=True, exist_ok=True)
        if session:  # die Anmeldung der Prüfung weiterverwenden, bevor der Abruf seinen Client baut
            seed = Database(directory / "schule.db")
            seed.set("sm_session", session)
            seed.close()
        self.ui_on = entry.get("ui") == "on"
        self.app = AppService(
            replace(cfg, sm_email=email, sm_password=password, data_dir=directory, demo=False),
            modules=FRIEND_UI_MODULES if self.ui_on else FRIEND_MODULES,
            standalone=False,
            # Ohne Oberfläche gehen Fehlermeldungen an den Besitzer, mit Oberfläche an den Freund selbst
            sync_pusher=None if self.ui_on else ForwardPusher(pusher, entry["label"]),
        )
        self.db = self.app.db
        self.sync = self.app.sync
        if not self.ui_on:
            # Der Abruf dient nur dem Kalender: keine eigenen Meldungen
            self.db.update_settings({key: False for key in _notify_keys()})

    def status(self) -> dict[str, Any]:
        status = self.db.get("status", {}) or {}
        return {
            "last_success": status.get("last_success"),
            "last_error": status.get("last_error"),
            "account": status.get("account") or {},
        }

    def ical(self, lessons: bool = True) -> str:
        """Der Kalender-Link der Freunde bleibt, wie er war: Stunden, Klassenarbeiten und Schultermine."""
        events = [e for e in self.db.snapshot("calendar") or [] if not e["is_holiday"]]
        return ical.build(self.db.snapshot("lessons") or [], self.db.snapshot("exams") or [], events, self.tz, lessons)


class Friends:
    def __init__(self, cfg: Config, db: Database, pusher: Pusher) -> None:
        self.cfg = cfg
        self.db = db
        self.pusher = pusher
        self.vault = Vault(cfg.data_dir / "accounts.key")
        self._services: dict[str, FriendService] = {}
        self._next_poll: dict[str, float] = {}
        self._manual: dict[str, float] = {}
        self._lock = asyncio.Lock()

    # ── Verzeichnis der Freunde ──────────────────────────────────────

    def entries(self) -> list[dict[str, Any]]:
        return self.db.get("friends", []) or []

    def _save(self, entries: list[dict[str, Any]]) -> None:
        self.db.set("friends", entries)

    def entry(self, friend_id: str) -> dict[str, Any] | None:
        return next((e for e in self.entries() if e["id"] == friend_id), None)

    def by_ical_token(self, token: str) -> dict[str, Any] | None:
        return next((e for e in self.entries() if _same(e["ical_token"], token)), None)

    def by_manage_token(self, token: str) -> dict[str, Any] | None:
        return next((e for e in self.entries() if _same(e["manage_token"], token)), None)

    def _update(self, friend_id: str, **changes: Any) -> None:
        entries = self.entries()
        for entry in entries:
            if entry["id"] == friend_id:
                entry.update(changes)
        self._save(entries)

    def mark_needs_login(self, friend_id: str) -> None:
        self._update(friend_id, needs_login=True)

    def _service(self, entry: dict[str, Any], session: dict[str, Any] | None = None) -> FriendService:
        service = self._services.get(entry["id"])
        if service is None:
            service = FriendService(self.cfg, entry, self.vault, self.pusher, session)
            self._services[entry["id"]] = service
        return service

    async def _forget(self, friend_id: str) -> None:
        service = self._services.pop(friend_id, None)
        if service:
            await service.sync.close()
            service.db.close()

    def view(self, entry: dict[str, Any]) -> dict[str, Any]:
        """Was Besitzer und Freund über den Zustand sehen dürfen (keine Zugangsdaten, keine Tokens)."""
        needs_login = bool(entry.get("needs_login"))
        status: dict[str, Any] = {}
        try:
            status = self._service(entry).status()
        except FriendError:
            needs_login = True
        if needs_login:
            state = "needs_login"
        elif status.get("last_success"):
            state = "ok"
        else:
            state = "waiting"
        return {
            "id": entry["id"],
            "label": entry["label"],
            "created": entry["created"],
            "state": state,
            "ui": entry.get("ui", "off"),
            "last_success": status.get("last_success"),
            "last_error": status.get("last_error"),
            "account": status.get("account") or {},
        }

    # ── Einladungen ──────────────────────────────────────────────────

    def invites(self) -> list[dict[str, Any]]:
        now = now_iso()
        return [i for i in self.db.get("friend_invites", []) or [] if i["expires"] > now]

    def invite(self, token: str) -> dict[str, Any] | None:
        return next((i for i in self.invites() if _same(i["token"], token)), None)

    def create_invite(self, label: str) -> dict[str, Any]:
        label = " ".join(str(label or "").split())
        if not label:
            raise FriendError("Bitte einen Namen eingeben.")
        if len(label) > 40:
            raise FriendError("Der Name ist zu lang (höchstens 40 Zeichen).")
        if len(self.entries()) + len(self.invites()) >= MAX_FRIENDS:
            raise FriendError(f"Mehr als {MAX_FRIENDS} Freunde (mit offenen Einladungen) sind nicht vorgesehen.")
        expires = (datetime.now(timezone.utc) + timedelta(days=INVITE_DAYS)).isoformat(timespec="seconds")
        invite = {"token": secrets.token_urlsafe(24), "label": label, "created": now_iso(),
                  "expires": expires, "attempts": 0}
        self.db.set("friend_invites", self.invites() + [invite])
        return invite

    def revoke_invite(self, token: str) -> bool:
        before = self.invites()
        after = [i for i in before if not _same(i["token"], token)]
        self.db.set("friend_invites", after)
        return len(after) != len(before)

    def _count_attempt(self, token: str) -> None:
        """Nach zu vielen Fehlversuchen verfällt die Einladung."""
        invites = self.invites()
        for invite in invites:
            if _same(invite["token"], token):
                invite["attempts"] = int(invite.get("attempts", 0)) + 1
        self.db.set("friend_invites", [i for i in invites if int(i.get("attempts", 0)) < MAX_INVITE_ATTEMPTS])

    # ── Anmelden und Zugangsdaten prüfen ─────────────────────────────

    async def _verify(self, email: str, password: str) -> SchulmanagerClient:
        email = email.strip()
        if not email or not password:
            raise FriendError("Bitte Benutzername und Passwort eingeben.")
        client = SchulmanagerClient(email, password)
        try:
            await client.login()
        except LoginError as exc:
            await client.close()
            wrong = exc.status in (400, 401, 404)
            raise FriendError("Benutzername oder Passwort falsch." if wrong else str(exc)) from exc
        except SchulmanagerError as exc:
            await client.close()
            raise FriendError("Schulmanager ist gerade nicht erreichbar. Bitte später noch einmal versuchen.") from exc
        if not client.student:
            await client.close()
            raise FriendError("Mit diesem Konto ist kein Schüler verknüpft. Bitte das Schüler-Login verwenden.")
        return client

    async def redeem(self, token: str, email: str, password: str) -> dict[str, Any]:
        """Einladung einlösen: Login prüfen, Freund anlegen, ersten Abruf machen."""
        async with self._lock:
            invite = self.invite(token)
            if invite is None:
                raise FriendError("Der Einladungslink ist ungültig oder abgelaufen.")
            try:
                client = await self._verify(email, password)
            except FriendError:
                self._count_attempt(token)
                raise
            entry = {
                "id": "f-" + secrets.token_hex(4),
                "label": invite["label"],
                "created": now_iso(),
                "ical_token": secrets.token_urlsafe(24),
                "manage_token": secrets.token_urlsafe(24),
                "creds": self.vault.seal({"email": email.strip(), "password": password}),
                "needs_login": False,
                "ui": "off",
            }
            self._save(self.entries() + [entry])
            self.revoke_invite(token)
        await self._start(entry, client)
        return entry

    async def renew(self, entry: dict[str, Any], email: str, password: str) -> dict[str, Any]:
        """Freund gibt ein neues Login an (z. B. nach Passwortwechsel)."""
        client = await self._verify(email, password)
        await self._forget(entry["id"])
        self._update(entry["id"], creds=self.vault.seal({"email": email.strip(), "password": password}),
                     needs_login=False)
        fresh = self.entry(entry["id"]) or entry
        await self._start(fresh, client)
        return fresh

    async def _start(self, entry: dict[str, Any], client: SchulmanagerClient) -> None:
        """Dienst anlegen, die geprüfte Anmeldung weiterverwenden und sofort einmal abrufen."""
        session = {"jwt": client.token, "user": client.user}
        await client.close()
        service = self._service(entry, session)
        service.db.delete("login_blocked_until")
        self._next_poll[entry["id"]] = time.monotonic() + POLL_MINUTES * 60
        try:
            await service.sync.run("setup")
        except Exception:  # der Freund ist angelegt, der nächste Abruf holt es nach
            log.exception("Erster Abruf für %s fehlgeschlagen", entry["id"])

    # ── Oberfläche für Freunde ───────────────────────────────────────

    @staticmethod
    def _normalize_code(code: str) -> str:
        return "".join(ch for ch in str(code).upper() if ch.isalnum())

    @classmethod
    def _hash_code(cls, code: str) -> str:
        return hashlib.sha256(cls._normalize_code(code).encode("utf-8")).hexdigest()

    @staticmethod
    def _new_code() -> str:
        raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(16))
        return "-".join(raw[i:i + 4] for i in range(0, 16, 4))

    def by_code(self, code: str) -> dict[str, Any] | None:
        """Der Freund mit dieser Oberfläche, wenn der Zugangscode stimmt."""
        digest = self._hash_code(code)
        return next((e for e in self.entries()
                     if e.get("ui") == "on" and e.get("ui_code_hash") and _same(e["ui_code_hash"], digest)), None)

    def code_of(self, entry: dict[str, Any]) -> str | None:
        try:
            return self.vault.open(entry["ui_code"])["code"] if entry.get("ui_code") else None
        except (InvalidToken, ValueError, KeyError):
            return None

    def app_for(self, entry: dict[str, Any]) -> Any:
        return self._service(entry).app

    def ui_apps(self) -> list[tuple[str, Any]]:
        """Die Konten der Freunde mit Oberfläche, für den Taktgeber (Briefing, Erinnerungen, Abruf)."""
        out = []
        for entry in self.entries():
            if entry.get("ui") != "on" or entry.get("needs_login"):
                continue
            try:
                out.append((entry["id"], self._service(entry).app))
            except FriendError:
                self.mark_needs_login(entry["id"])
        return out

    async def offer_ui(self, friend_id: str, offered: bool) -> None:
        """Besitzer bietet die Oberfläche an oder nimmt das Angebot (und eine schon erteilte Zusage) zurück."""
        entry = self.entry(friend_id)
        if entry is None:
            raise FriendError("Freund nicht gefunden.")
        state = entry.get("ui", "off")
        if offered and state == "off":
            self._update(friend_id, ui="offered")
        elif not offered and state != "off":
            await self.disable_ui(entry)

    async def activate_ui(self, entry: dict[str, Any]) -> str:
        """Der Freund stimmt zu. Gibt den Zugangscode zurück (bei einem doppelten Klick den schon erzeugten)."""
        if entry.get("ui") == "on":
            return self.code_of(entry) or ""
        if entry.get("ui") != "offered":
            raise FriendError("Die Oberfläche ist für dich nicht freigegeben.")
        await self._forget(entry["id"])
        code = self._new_code()
        self._update(entry["id"], ui="on", ui_code=self.vault.seal({"code": code}),
                     ui_code_hash=self._hash_code(code), ui_since=now_iso())
        fresh = self.entry(entry["id"]) or entry
        service = self._service(fresh)
        # Mit Oberfläche gelten die normalen Meldungen, nur der Abruf etwas seltener als beim Besitzer
        service.db.update_settings({**{key: True for key in _notify_keys()}, "poll_interval": UI_POLL_MINUTES})
        try:
            await service.sync.run("setup")
        except Exception:
            log.exception("Erster Abruf mit Oberfläche für %s fehlgeschlagen", entry["id"])
        return code

    async def disable_ui(self, entry: dict[str, Any]) -> None:
        """Oberfläche abschalten (durch den Freund oder den Besitzer): Code ungültig, Zusatzdaten und Geräte weg."""
        await self._forget(entry["id"])
        self._update(entry["id"], ui="off", ui_code=None, ui_code_hash=None, ui_since=None)
        fresh = self.entry(entry["id"]) or entry
        try:
            service = self._service(fresh)
        except FriendError:
            return
        marks = ",".join("?" for _ in FETCHED_BY_UI)
        service.db.execute(f"DELETE FROM snapshots WHERE module IN ({marks})", FETCHED_BY_UI)
        service.db.execute("DELETE FROM push_subscriptions")
        for key in ("absences_raw", "absences_fetch", "disabled_modules"):
            service.db.delete(key)
        service.db.update_settings({key: False for key in _notify_keys()})

    def regenerate_code(self, entry: dict[str, Any]) -> str:
        if entry.get("ui") != "on":
            raise FriendError("Die Oberfläche ist nicht aktiv.")
        code = self._new_code()
        self._update(entry["id"], ui_code=self.vault.seal({"code": code}), ui_code_hash=self._hash_code(code))
        return code

    # ── Verwalten ────────────────────────────────────────────────────

    async def delete(self, friend_id: str) -> bool:
        if self.entry(friend_id) is None:
            return False
        await self._forget(friend_id)
        self._save([e for e in self.entries() if e["id"] != friend_id])
        self._next_poll.pop(friend_id, None)
        shutil.rmtree(self.cfg.data_dir / "accounts" / friend_id, ignore_errors=True)
        return True

    def regenerate_ical(self, friend_id: str) -> str:
        token = secrets.token_urlsafe(24)
        self._update(friend_id, ical_token=token)
        return token

    def ical(self, entry: dict[str, Any], lessons: bool = True) -> str:
        return self._service(entry).ical(lessons)

    # ── Abruf ────────────────────────────────────────────────────────

    async def _poll(self, entry: dict[str, Any], trigger: str) -> dict[str, Any]:
        try:
            service = self._service(entry)
        except FriendError:
            self.mark_needs_login(entry["id"])
            return {"ok": False, "error": "Zugangsdaten nicht lesbar"}
        result = await service.sync.run(trigger)
        if result.get("auth"):
            # Falsches Passwort: nicht weiter probieren, sonst sperrt Schulmanager das Konto
            self.mark_needs_login(entry["id"])
        return result

    async def poll_due(self, school_day: bool) -> None:
        """Ruft höchstens einen fälligen Freund ab (gestaffelt, nie alle auf einmal).

        Freunde mit Oberfläche fehlen hier: Sie laufen im Taktgeber wie ein eigenes Konto."""
        now = time.monotonic()
        minutes = POLL_MINUTES if school_day else POLL_MINUTES_OFF
        for index, entry in enumerate(self.entries()):
            if entry.get("needs_login") or entry.get("ui") == "on":
                continue
            due = self._next_poll.setdefault(entry["id"], now + FIRST_POLL_DELAY + index * 120)
            if now < due:
                continue
            self._next_poll[entry["id"]] = now + minutes * 60 + random.randint(0, 300)
            await self._poll(entry, "schedule")
            return

    async def sync_now(self, friend_id: str) -> dict[str, Any]:
        entry = self.entry(friend_id)
        if entry is None:
            raise FriendError("Freund nicht gefunden.")
        if entry.get("needs_login"):
            raise FriendError("Das Login stimmt nicht mehr. Der Freund muss es auf seiner Seite erneuern.")
        if time.monotonic() - self._manual.get(friend_id, -MANUAL_SYNC_PAUSE) < MANUAL_SYNC_PAUSE:
            raise FriendError("Bitte eine Minute warten, bevor du erneut abrufst.")
        self._manual[friend_id] = time.monotonic()
        return await self._poll(entry, "manual")

    async def close(self) -> None:
        for friend_id in list(self._services):
            await self._forget(friend_id)
