"""Bündelt alle Bausteine und bereitet Daten für API und Push auf."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from . import bell, briefing, fmt, own
from .config import Config
from .db import Database
from .friends import Friends
from .holidays import SchoolCalendar
from .push import Pusher
from .sync import SyncService

log = logging.getLogger(__name__)


class AppService:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.db = Database(cfg.data_dir / "schule.db")
        self.calendar = SchoolCalendar(self.db, cfg.subdivision)
        self.pusher = Pusher(self.db, cfg.data_dir, cfg.vapid_subject)
        self.sync = SyncService(cfg, self.db, self.calendar, self.pusher)
        self.friends = Friends(cfg, self.db, self.pusher)
        cal_events = self.db.snapshot("calendar")
        if cal_events:
            self.calendar.set_calendar_holidays(cal_events)

    def now(self) -> datetime:
        return datetime.now(self.cfg.tz)

    def today(self) -> date:
        return self.now().date()

    def snap(self, module: str) -> list[dict[str, Any]]:
        return self.db.snapshot(module) or []

    # ── Eigene Einträge: Klausuren und Beurlaubungen ─────────────────

    def own(self, kind: str) -> list[dict[str, Any]]:
        return self.db.get(f"own_{kind}", []) or []

    def exams(self) -> list[dict[str, Any]]:
        """Klassenarbeiten aus Schulmanager plus selbst eingetragene Klausuren."""
        mine = [own.exam_item(e) for e in self.own("exams")]
        return sorted(self.snap("exams") + mine, key=lambda e: (e["date"], e.get("start") or ""))

    def with_own(
        self, lessons: list[dict[str, Any]], first: date | None = None, last: date | None = None
    ) -> list[dict[str, Any]]:
        """Stundenplan mit Klausuren und Beurlaubungen darüber, dazu eigener wiederkehrender Unterricht.

        Der Zeitraum (first bis last) bestimmt, für welche Tage der wiederkehrende Unterricht
        erzeugt wird. Ohne Angabe gilt das Fenster des Stundenplan-Abrufs."""
        shown = own.overlay(lessons, [own.exam_item(e) for e in self.own("exams")], self.own("leaves"))
        courses = self.own("courses")
        if courses:
            if first is None or last is None:
                first, last = self.sync.lesson_window(self.today())
            shown = shown + own.course_lessons(courses, first, last, self.calendar.in_official_holiday)
            shown.sort(key=lambda l: (l["date"], l.get("start") or "99:99"))
        return shown

    def lessons(self, first: date | None = None, last: date | None = None) -> list[dict[str, Any]]:
        return self.with_own(self.snap("lessons"), first, last)

    def subjects(self) -> list[str]:
        """Fächer aus dem Stundenplan, als Vorschläge für die Eingabe."""
        return sorted({l["subject"] for l in self.snap("lessons") if l.get("subject") and l["state"] != "event"})

    # ── Tagesansicht ─────────────────────────────────────────────────

    def day(self, day: date) -> dict[str, Any]:
        summary = briefing.day_summary(
            day,
            self.lessons(day, day),
            self.snap("homework"),
            self.exams(),
            self.snap("calendar"),
            self.db.homework_done_ids(),
            self.calendar,
            self.own("leaves"),
        )
        start, end = self.sync.lesson_window(self.today())
        summary["in_window"] = start <= day <= end
        return summary

    def default_day(self) -> date:
        """Nach Schulschluss (bzw. ab 16 Uhr) springt die Startansicht auf den nächsten Schultag."""
        now = self.now()
        today = now.date()
        if not self.calendar.is_school_day(today):
            return self.calendar.next_school_day(today)
        summary = self.day(today)
        end = summary["end"] or "16:00"
        if now.strftime("%H:%M") >= max(end, "16:00"):
            return self.calendar.next_school_day(today)
        return today

    def unread_counts(self) -> tuple[int, int]:
        letters = sum(1 for l in self.snap("letters") if l.get("unread"))
        messages = sum(int(t.get("unread") or 0) for t in self.snap("threads"))
        return letters, messages

    def overview(self, day: date | None = None) -> dict[str, Any]:
        today = self.today()
        target = day or self.default_day()
        letters, messages = self.unread_counts()
        exams = [e for e in self.exams() if e["date"] >= today.isoformat()]
        done = self.db.homework_done_ids()
        open_hw = [h for h in self.snap("homework") if h["due"] >= today.isoformat() and h["id"] not in done]
        return {
            "today": today.isoformat(),
            "now": self.now().strftime("%H:%M"),
            "day": self.day(target),
            "next_school_day": self.calendar.next_school_day(today).isoformat(),
            "prev_school_day": self._prev_school_day(target).isoformat(),
            "following_school_day": self.calendar.next_school_day(target).isoformat(),
            "next_exam": exams[0] if exams else None,
            "open_homework": len(open_hw),
            "unread_letters": letters,
            "unread_messages": messages,
            "unseen_events": self.db.unseen_count(),
            "holidays": self.calendar.upcoming(today),
            "status": self.status(),
        }

    def _prev_school_day(self, day: date) -> date:
        current = day - timedelta(days=1)
        for _ in range(60):
            if self.calendar.is_school_day(current):
                return current
            current -= timedelta(days=1)
        return day - timedelta(days=1)

    async def week(self, monday: date) -> dict[str, Any]:
        monday = monday - timedelta(days=monday.weekday())
        friday = monday + timedelta(days=4)
        start, end = self.sync.lesson_window(self.today())
        source = "snapshot"
        if start <= monday and friday <= end:
            lessons = self.lessons(monday, friday)
        else:
            lessons = self.with_own(await self.sync.lessons_for(monday, friday), monday, friday)
            source = "live"
        days = []
        events = self.snap("calendar")
        exams = self.exams()
        leaves = self.own("leaves")
        for offset in range(5):
            d = monday + timedelta(days=offset)
            iso = d.isoformat()
            holiday = self.calendar.holiday(d)
            days.append({
                "date": iso,
                "label": fmt.long_date(d),
                "short": fmt.short_date(d),
                "holiday": holiday.name if holiday else None,
                # Die Klausur steht als eigene Zeile bei den Arbeiten, nicht noch einmal bei den Stunden
                "lessons": [l for l in lessons if l["date"] == iso and l["state"] != "exam"],
                "leaves": own.leaves_on(leaves, iso),
                "exams": [e for e in exams if e["date"] == iso],
                "events": [e for e in events if not e["is_holiday"] and e["start"][:10] <= iso <= (e["end"] or e["start"])[:10]],
            })
        return {"monday": monday.isoformat(), "days": days, "source": source, "today": self.today().isoformat()}

    # ── Briefing ─────────────────────────────────────────────────────

    def briefing_time_for(self, day: date) -> dict[str, Any]:
        """Uhrzeit des Morgen-Briefings an einem Tag.

        Im Modus "auto" zählt die früheste Stunde, die an dem Tag im Plan steht,
        auch wenn sie ausfällt: Gerade dann soll das Briefing rechtzeitig kommen."""
        settings = self.db.settings()
        fixed = {"time": settings["briefing_time"], "first_hour": None, "mode": "fixed"}
        if settings["briefing_mode"] != "auto":
            return fixed
        iso = day.isoformat()
        # Eine eigene Klausur hat keine Stundennummer: die Stunde kommt aus ihrer Uhrzeit
        hours = [
            bell.first_number(l["hour"]) or bell.hour_at(l.get("start") or "")
            for l in self.lessons(day, day) if l["date"] == iso
        ]
        hours = sorted({int(h) for h in hours if h})
        if not hours:
            return {**fixed, "mode": "auto-fallback"}
        first = str(hours[0])
        by_hour = settings["briefing_by_hour"]
        at = by_hour.get(first) or by_hour[max(by_hour, key=int)]
        return {"time": at, "first_hour": first, "mode": "auto"}

    def briefing_target(self, kind: str) -> date:
        """Morgens: der Tag des nächsten Briefings. Abends: der nächste Schultag."""
        if kind == "morning":
            return date.fromisoformat(self.next_briefing()["date"])
        return self.calendar.next_school_day(self.today())

    def briefing(self, kind: str, target: date | None = None) -> dict[str, Any]:
        today = self.today()
        target = target or self.briefing_target(kind)
        summary = self.day(target)
        letters, messages = self.unread_counts()
        push = briefing.build_push(kind, summary, today, letters, messages)
        return {"kind": kind, "target": target.isoformat(), "push": push, "summary": summary,
                "scheduled": self.briefing_time_for(target) if kind == "morning" else None}

    def briefing_skipped(self, day: date) -> bool:
        """An Tagen, an denen man komplett beurlaubt ist, gibt es kein Morgen-Briefing."""
        summary = self.day(day)
        return summary["full_leave"] or (
            summary["all_cancelled"] and any(l["state"] == "leave" for l in summary["lessons"])
        )

    def next_briefing(self) -> dict[str, Any]:
        """Wann das nächste Morgen-Briefing kommt (für die Anzeige in den Einstellungen)."""
        now = self.now()
        today = now.date()
        day = today if self.calendar.is_school_day(today) else self.calendar.next_school_day(today)
        if day == today:
            planned = self.briefing_time_for(today)
            if self.db.get("sent_briefing") == today.isoformat() or now.strftime("%H:%M") > planned["time"]:
                day = self.calendar.next_school_day(today)
        for _ in range(60):  # beurlaubte Tage überspringen
            if not self.briefing_skipped(day):
                break
            day = self.calendar.next_school_day(day)
        return {"date": day.isoformat(), **self.briefing_time_for(day)}

    async def send_briefing(self, kind: str, target: date | None = None) -> dict[str, Any]:
        result = self.briefing(kind, target)
        push = result["push"]
        self.db.add_event("briefing", kind, push["title"], push["body"], push["url"], result["target"])
        delivered = await self.pusher.send(push["title"], push["body"], push["url"], tag=f"briefing-{kind}")
        return {**result, "delivered": delivered}

    async def send_exam_reminders(self) -> int:
        settings = self.db.settings()
        today = self.today()
        reminded: list[str] = self.db.get("reminded", []) or []
        sent = 0
        exams = self.exams()
        for exam in exams:
            days = (fmt.parse(exam["date"]) - today).days
            if days not in settings["reminder_days"]:
                continue
            key = f"{exam['id']}:{days}"
            if key in reminded:
                continue
            reminded.append(key)
            title = f"{exam['subject']}: {exam['type']} {fmt.in_days(exam['date'], today)}"
            details = [fmt.short_date(exam["date"])]
            if exam.get("hour"):
                details.append(f"{exam['hour']}. Stunde")
            elif exam.get("start"):
                details.append(f"{exam['start']}–{exam['end']} Uhr" if exam.get("end") else f"{exam['start']} Uhr")
            body = ", ".join(details) + (f"\n{exam['comment']}" if exam.get("comment") else "")
            self.db.add_event("reminder", "exam", title, body, "/#/aufgaben?tab=klausuren", exam["date"])
            await self.pusher.send(title, body, "/#/aufgaben?tab=klausuren", tag=f"reminder-{exam['id']}")
            sent += 1
        # Erinnerungen an vergangene Klausuren vergessen
        valid = {e["id"] for e in exams if e["date"] >= today.isoformat()}
        self.db.set("reminded", [k for k in reminded if k.split(":")[0] in valid])
        return sent

    # ── Status ───────────────────────────────────────────────────────

    def disabled_modules(self) -> list[str]:
        return list(self.db.get("disabled_modules", []) or [])

    def status(self) -> dict[str, Any]:
        status = self.db.get("status", {}) or {}
        return {
            "last_success": status.get("last_success"),
            "last_attempt": status.get("last_attempt"),
            "last_error": status.get("last_error"),
            "module_errors": status.get("module_errors") or {},
            "disabled_modules": self.disabled_modules(),
            "account": status.get("account") or self.sync.account(),
            "running": self.sync.running,
            "demo": self.cfg.demo,
            "devices": len(self.pusher.devices()),
            "next_briefing": self.next_briefing(),
        }
