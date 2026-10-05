"""Minütlicher Taktgeber: Abruf-Intervalle, Briefing, Abend-Vorschau, Erinnerungen.

Statt eines Cron-Frameworks prüft eine Schleife jede Minute, was fällig ist.
Dadurch greifen geänderte Uhrzeiten aus den Einstellungen sofort, und nach
einem Neustart wird ein verpasstes Briefing (bis 90 Minuten) nachgeholt.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta

from .service import AppService
from .sync import SNAPSHOT_VERSION

log = logging.getLogger(__name__)

CATCH_UP_MINUTES = 90


def _minutes(hhmm: str) -> int:
    hour, minute = hhmm.split(":")
    return int(hour) * 60 + int(minute)


class Scheduler:
    def __init__(self, app: AppService) -> None:
        self.app = app
        self._task: asyncio.Task | None = None
        self._last_poll = 0.0

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="scheduler")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        await asyncio.sleep(2)
        # Erster Start oder neue Version mit anderem Datenformat: sofort neu abrufen,
        # sonst zeigt die App bis zum nächsten Abruf alte, anders aufbereitete Daten
        if not self.app.db.last_successful_sync() or self.app.db.get("snapshot_version") != SNAPSHOT_VERSION:
            await self._poll("startup")
        while True:
            try:
                await self.tick(datetime.now(self.app.cfg.tz))
            except Exception:
                log.exception("Fehler im Taktgeber")
            now = datetime.now(self.app.cfg.tz)
            await asyncio.sleep(60 - now.second + 0.5)

    async def _poll(self, trigger: str) -> None:
        self._last_poll = time.monotonic()
        await self.app.sync.run(trigger)

    @staticmethod
    def _due(now_min: int, at: str, sent: str | None, today: str) -> bool:
        target = _minutes(at)
        return sent != today and target <= now_min <= target + CATCH_UP_MINUTES

    async def tick(self, now: datetime) -> None:
        db = self.app.db
        cal = self.app.calendar
        settings = db.settings()
        today = now.date()
        today_iso = today.isoformat()
        now_min = now.hour * 60 + now.minute
        school_day = cal.is_school_day(today)

        # Morgen-Briefing (nur an Schultagen)
        briefing_at = self.app.briefing_time_for(today)["time"]
        if settings["briefing_enabled"] and school_day and self._due(
            now_min, briefing_at, db.get("sent_briefing"), today_iso
        ):
            db.set("sent_briefing", today_iso)
            await self._poll("briefing")
            await self.app.send_briefing("morning", today)
            return

        # Abend-Vorschau (wenn morgen Schule ist)
        if settings["evening_enabled"] and cal.next_school_day(today) == today + timedelta(days=1) and self._due(
            now_min, settings["evening_time"], db.get("sent_evening"), today_iso
        ):
            db.set("sent_evening", today_iso)
            await self._poll("evening")
            await self.app.send_briefing("evening")
            return

        # Klausur-Erinnerungen
        if settings["reminder_enabled"] and self._due(
            now_min, settings["reminder_time"], db.get("sent_reminders"), today_iso
        ):
            db.set("sent_reminders", today_iso)
            await self.app.send_exam_reminders()

        # Regelmäßiger Abruf im Zeitfenster
        if not (_minutes(settings["poll_start"]) <= now_min <= _minutes(settings["poll_end"])):
            return
        interval = settings["poll_interval"] if school_day else settings["weekend_poll_interval"]
        if time.monotonic() - self._last_poll >= interval * 60 - 5:
            await self._poll("schedule")
