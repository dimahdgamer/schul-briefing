"""Abruf -> Normalisierung -> Vergleich -> Verlauf + Push."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import date, datetime, timedelta
from typing import Any

from . import diff, normalize
from .config import Config
from .db import Database
from .demo import DemoSource
from .holidays import SchoolCalendar
from .push import Pusher
from .schulmanager import LoginError, RpcCall, SchulmanagerClient, SchulmanagerError

log = logging.getLogger(__name__)

MODULES = ("lessons", "homework", "exams", "letters", "threads", "calendar", "absences")
NOTIFY_SETTING = {
    "lessons": "notify_lessons",
    "homework": "notify_homework",
    "exams": "notify_exams",
    "letters": "notify_letters",
    "messages": "notify_messages",
    "calendar": "notify_calendar",
    "absences": "notify_absences",
}
MAX_SINGLE_PUSHES = 4
DISABLED_STATUS = {403, 404}  # "Modul nicht gebucht / für diese Rolle nicht freigegeben"
# Erhöhen, wenn sich die IDs normalisierter Einträge ändern. Der erste Abruf danach
# legt nur einen neuen Ausgangsstand an, statt alles als Änderung zu melden.
SNAPSHOT_VERSION = 3  # 3: Zustand "eva" im Stundenplan, Hausaufgaben mit EVA-Markierung
LOGIN_BACKOFF_SECONDS = 6 * 3600
ABSENCES_REFRESH = 3600  # Fehlzeiten höchstens stündlich abrufen
ABSENCES_RETRY = 24 * 3600  # nach einem Fehler (z. B. nicht freigegeben) nur einmal am Tag wieder probieren


class SyncService:
    def __init__(
        self,
        cfg: Config,
        db: Database,
        calendar: SchoolCalendar,
        pusher: Pusher,
        modules: tuple[str, ...] = MODULES,
        keep_raw: bool = True,
    ) -> None:
        self.cfg = cfg
        self.db = db
        self.calendar = calendar
        self.pusher = pusher
        # Welche Module abgerufen werden. Für Freunde nur das, was der Kalender braucht
        self.modules = modules
        # Rohantworten zur Fehlersuche auf die Platte schreiben (bei Freunden nicht)
        self.keep_raw = keep_raw
        self._lock = asyncio.Lock()
        self._lesson_cache: dict[tuple[str, str], tuple[float, list[dict[str, Any]]]] = {}
        if cfg.demo:
            self.demo: DemoSource | None = DemoSource(db, calendar.is_school_day)
            self.client: SchulmanagerClient | None = None
        else:
            self.demo = None
            self.client = SchulmanagerClient(
                cfg.sm_email,
                cfg.sm_password,
                load_token=lambda: db.get("sm_session"),
                save_token=lambda value: db.set("sm_session", value) if value else db.delete("sm_session"),
            )

    # ── Hilfen ───────────────────────────────────────────────────────

    def today(self) -> date:
        return datetime.now(self.cfg.tz).date()

    @property
    def running(self) -> bool:
        return self._lock.locked()

    def lesson_window(self, today: date) -> tuple[date, date]:
        """Letzte Woche (für Hausaufgaben-Fälligkeit) bis vier Wochen voraus."""
        monday = today - timedelta(days=today.weekday())
        return monday - timedelta(days=7), monday + timedelta(days=27)

    def account(self) -> dict[str, Any]:
        if self.demo:
            user = self.demo.user
        else:
            user = self.client.user if self.client else {}
        student = (user or {}).get("associatedStudent") or {}
        return {
            "name": f"{user.get('firstname', '')} {user.get('lastname', '')}".strip() if user else "",
            "class": student.get("className") or "",
            "demo": self.cfg.demo,
        }

    async def close(self) -> None:
        if self.client:
            await self.client.close()

    # ── Abruf ────────────────────────────────────────────────────────

    async def _fetch_raw(self, today: date) -> dict[str, tuple[bool, Any, int]]:
        """Rohdaten je Modul als (erfolgreich, Daten, Status)."""
        start, end = self.lesson_window(today)
        if self.demo:
            raw = self.demo.fetch(today, start, end)
            return {k: (True, v, 200) for k, v in raw.items()}

        assert self.client is not None
        if not self.client.user:
            await self.client.login()
        student = self.client.student
        if not student:
            raise SchulmanagerError("Kein Schüler mit diesem Konto verknüpft", 0, "account")
        sid = student["id"]

        calls = {
            "lessons": RpcCall("schedules", "get-actual-lessons",
                               {"student": {"id": sid}, "start": start.isoformat(), "end": end.isoformat()}),
            "homework": RpcCall("classbook", "get-homework", {"student": {"id": sid}}),
            "exams": RpcCall("exams", "get-exams", {"student": {"id": sid},
                                                     "start": (today - timedelta(days=7)).isoformat(),
                                                     "end": (today + timedelta(days=120)).isoformat()}),
            "letters": RpcCall("letters", "get-letters", {}),
            "threads": RpcCall("messenger", "get-subscriptions", {"all": True, "includeArchived": False}),
            "calendar": RpcCall("calendar", "get-events-for-user", {"start": (today - timedelta(days=7)).isoformat(),
                                                                     "end": (today + timedelta(days=90)).isoformat(),
                                                                     "includeHolidays": True}),
        }
        names = [name for name in calls if name in self.modules]
        results = await self.client.calls([calls[n] for n in names])
        out = {}
        for name, result in zip(names, results):
            out[name] = (result.ok, result.data, result.status)
            if result.status in DISABLED_STATUS:
                log.debug("Modul %s ist nicht freigeschaltet (Status %s)", name, result.status)
            elif not result.ok:
                log.warning("Modul %s lieferte Status %s", name, result.status)
        if "absences" in self.modules:
            out["absences"] = await self._fetch_absences(sid, today)
        return out

    async def _fetch_absences(self, sid: Any, today: date) -> tuple[bool, Any, int]:
        """Fehlzeiten laut Klassenbuch (Statistik je Fach und Liste mit Entschuldigungsstatus).

        Schulen geben das Schülern nur teilweise frei, und die Aufrufe stammen aus dem Web-Client, nicht
        aus einer Beschreibung. Deshalb wird sparsam abgerufen (stündlich, nach einem Fehler täglich),
        und jeder Fehler ist nur ein fehlendes Modul, nie ein Absturz."""
        assert self.client is not None
        state = self.db.get("absences_fetch") or {}
        status = int(state.get("status") or 0)
        wait = ABSENCES_REFRESH if status == 200 else ABSENCES_RETRY
        if state and time.time() - float(state.get("at") or 0) < wait:
            cached = self.db.get("absences_raw")
            return (True, cached, 200) if status == 200 and cached is not None else (False, None, status)

        year = today.year if today.month >= 8 else today.year - 1
        since = date(year, 8, 1).isoformat()

        def statistics(until: date, unexcused: bool) -> RpcCall:
            return RpcCall("classbook", "get-statistics", {
                "from": since, "until": until.isoformat(), "student": {"id": sid}, "type": "sum-all",
                "unexcusedOnly": unexcused, "includeInternalExemptions": False, "by": "subject"})

        until = today
        everything, unexcused, term = await self.client.calls([
            statistics(until, False), statistics(until, True),
            RpcCall("classbook", "get-current-previous-or-next-term", {}),
        ])
        if everything.status == 400:
            # Manche Schulen blenden die letzten Tage für Schüler aus: dann etwas früher aufhören
            until = today - timedelta(days=14)
            everything, unexcused = await self.client.calls([statistics(until, False), statistics(until, True)])

        listing = None
        if term.ok and term.data:
            (listing,) = await self.client.calls([
                RpcCall("classbook", "get-history-absences-list", {"term": term.data, "student": {"id": sid}})])

        ok = everything.ok or bool(listing and listing.ok)
        data = {
            "statistics": everything.data if everything.ok else None,
            "statistics_unexcused": unexcused.data if unexcused.ok else None,
            "list": listing.data if listing and listing.ok else None,
        }
        failed = [r.status for r in (everything, listing) if r is not None and not r.ok]
        result_status = 200 if ok else (failed[0] if failed else 0)
        self.db.set("absences_fetch", {"at": time.time(), "status": result_status})
        if ok:
            self.db.set("absences_raw", data)
        return ok, data if ok else None, result_status

    async def lessons_for(self, start: date, end: date) -> list[dict[str, Any]]:
        """Stundenplan für beliebige Wochen (Wochenansicht), 10 Minuten gecacht."""
        key = (start.isoformat(), end.isoformat())
        cached = self._lesson_cache.get(key)
        if cached and time.monotonic() - cached[0] < 600:
            return cached[1]
        if self.demo:
            raw = self.demo.lessons(None, start, end)
        else:
            assert self.client is not None
            if not self.client.user:
                await self.client.login()
            student = self.client.student or {}
            result = (await self.client.calls([RpcCall("schedules", "get-actual-lessons", {
                "student": {"id": student.get("id")}, "start": start.isoformat(), "end": end.isoformat()})]))[0]
            if not result.ok:
                raise SchulmanagerError(f"Stundenplan nicht verfügbar (Status {result.status})", result.status)
            raw = result.data
        lessons = normalize.lessons(raw)
        self._lesson_cache[key] = (time.monotonic(), lessons)
        return lessons

    # ── Sync ─────────────────────────────────────────────────────────

    async def run(self, trigger: str = "schedule") -> dict[str, Any]:
        if self._lock.locked():
            return {"ok": False, "error": "Synchronisierung läuft bereits", "changes": 0}
        async with self._lock:
            return await self._run(trigger)

    async def _run(self, trigger: str) -> dict[str, Any]:
        blocked_until = self.db.get("login_blocked_until", 0) or 0
        if trigger == "schedule" and blocked_until > time.time():
            return {"ok": False, "error": "Login gesperrt nach Fehlversuch", "changes": 0}

        sync_id = self.db.sync_started(trigger)
        today = self.today()
        await self.calendar.refresh(today)

        try:
            raw = await self._fetch_raw(today)
        except LoginError as exc:
            log.error("Login fehlgeschlagen: %s", exc)
            self.db.set("login_blocked_until", time.time() + LOGIN_BACKOFF_SECONDS)
            self.db.sync_finished(sync_id, False, error=str(exc))
            self._set_status(error=str(exc))
            await self._notify_failure(f"Schulmanager-Login fehlgeschlagen: {exc}")
            return {"ok": False, "error": str(exc), "changes": 0, "auth": True}
        except Exception as exc:
            log.exception("Abruf fehlgeschlagen")
            self.db.sync_finished(sync_id, False, error=str(exc))
            self._set_status(error=str(exc))
            await self._maybe_notify_outage()
            return {"ok": False, "error": str(exc), "changes": 0}

        self.db.delete("login_blocked_until")
        settings = self.db.settings()
        student_id = (self.client.student or {}).get("id") if self.client else 4711
        module_errors: dict[str, str] = {}
        disabled: list[str] = []
        changes: list[diff.Change] = []

        fresh_start = self.db.get("snapshot_version") != SNAPSHOT_VERSION
        normalized: dict[str, Any] = {}
        for module in self.modules:
            ok, data, status = raw.get(module, (False, None, 0))
            if status in DISABLED_STATUS:
                # Modul ist für dieses Konto nicht freigeschaltet (z. B. Noten): kein Fehler
                disabled.append(module)
                continue
            self._write_raw(module, data)
            if not ok:
                module_errors[module] = f"nicht verfügbar (Status {status})"
                continue
            try:
                normalized[module] = self._normalize(module, data, student_id, normalized)
            except Exception as exc:  # Format geändert: Modul überspringen, Rest weiter
                log.exception("Normalisierung von %s fehlgeschlagen", module)
                module_errors[module] = f"Format unbekannt: {exc}"

        if "calendar" in normalized:
            self.calendar.set_calendar_holidays(normalized["calendar"])

        for module, items in normalized.items():
            previous = None if fresh_start else self.db.snapshot(module)
            changes.extend(self._diff(module, previous, items, today))
            self.db.save_snapshot(module, items)
        self._lesson_cache.clear()

        await self._record_and_push(changes, settings)
        self.db.sync_finished(sync_id, True, changes=len(changes),
                              error="; ".join(f"{k}: {v}" for k, v in module_errors.items()) or None)
        self.db.set("disabled_modules", disabled)
        self.db.set("snapshot_version", SNAPSHOT_VERSION)
        self._set_status(error=None, module_errors=module_errors)
        self.db.prune_events()
        log.info("Sync fertig (%s): %d Änderungen", trigger, len(changes))
        return {"ok": True, "changes": len(changes), "module_errors": module_errors}

    def _normalize(self, module: str, data: Any, student_id: Any, done: dict[str, Any]) -> Any:
        if module == "lessons":
            return normalize.lessons(data)
        if module == "homework":
            # Fälligkeit = nächste Stunde im Fach, dafür den Stundenplan dieses Abrufs nutzen
            lesson_list = done.get("lessons")
            if lesson_list is None:
                lesson_list = self.db.snapshot("lessons") or []
            return normalize.homework(data, lesson_list, self.calendar.next_school_day)
        if module == "exams":
            return normalize.exams(data)
        if module == "letters":
            return normalize.letters(data, student_id)
        if module == "threads":
            return normalize.threads(data)
        if module == "calendar":
            return normalize.calendar(data)
        if module == "absences":
            return normalize.absences(data)
        raise ValueError(module)

    def _diff(self, module: str, previous: Any, items: Any, today: date) -> list[diff.Change]:
        if module == "lessons":
            return diff.diff_lessons(previous, items, today)
        if module == "homework":
            return diff.diff_homework(previous, items, today)
        if module == "exams":
            return diff.diff_exams(previous, items, today)
        if module == "letters":
            return diff.diff_letters(previous, items)
        if module == "threads":
            return diff.diff_threads(previous, items)
        if module == "calendar":
            return diff.diff_calendar(previous, items, today)
        if module == "absences":
            return diff.diff_absences(previous, items, today)
        return []

    def _write_raw(self, module: str, data: Any) -> None:
        if not self.keep_raw:
            return
        try:
            path = self.cfg.data_dir / "raw" / f"{module}.json"
            path.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        except OSError:
            pass

    async def _record_and_push(self, changes: list[diff.Change], settings: dict[str, Any]) -> None:
        to_push: list[tuple[int, diff.Change]] = []
        for change in changes:
            event_id = self.db.add_event(change.category, change.kind, change.title, change.body,
                                         change.url, change.ref_date, change.data)
            if settings.get(NOTIFY_SETTING.get(change.category, ""), True):
                to_push.append((event_id, change))
        if not to_push:
            return

        if len(to_push) > MAX_SINGLE_PUSHES:
            # Viele Änderungen auf einmal: eine Sammelnachricht statt Push-Flut
            title = f"{len(to_push)} Neuigkeiten im Schulmanager"
            body = "\n".join(c.title for _, c in to_push[:6])
            await self.pusher.send(title, body, "/#/verlauf", tag="sammel")
        else:
            for _, change in to_push:
                await self.pusher.send(change.title, change.body, change.url or "/", tag=f"{change.category}-{change.ref_date or ''}")
        for event_id, _ in to_push:
            self.db.mark_pushed(event_id)

    def _set_status(self, error: str | None, module_errors: dict[str, str] | None = None) -> None:
        status = self.db.get("status", {}) or {}
        status["last_attempt"] = datetime.now(self.cfg.tz).isoformat(timespec="seconds")
        status["last_error"] = error
        if error is None:
            status["last_success"] = status["last_attempt"]
            status["failures"] = 0
            status["module_errors"] = module_errors or {}
        else:
            status["failures"] = int(status.get("failures") or 0) + 1
        status["account"] = self.account()
        self.db.set("status", status)

    async def _maybe_notify_outage(self) -> None:
        status = self.db.get("status", {}) or {}
        if int(status.get("failures") or 0) == 8:  # ca. 2 Stunden ohne Erfolg
            await self._notify_failure("Schulmanager ist seit einer Weile nicht erreichbar. Die App versucht es weiter.")

    async def _notify_failure(self, message: str) -> None:
        today = self.today().isoformat()
        if self.db.get("failure_notified") == today:
            return
        self.db.set("failure_notified", today)
        self.db.add_event("system", "error", "Problem beim Abruf", message, "/#/einstellungen")
        await self.pusher.send("Problem beim Abruf", message, "/#/einstellungen", tag="system")
