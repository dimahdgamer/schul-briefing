"""Bündelt alle Bausteine und bereitet Daten für API und Push auf."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from . import briefing, fmt
from .config import Config
from .db import Database
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
        cal_events = self.db.snapshot("calendar")
        if cal_events:
            self.calendar.set_calendar_holidays(cal_events)

    def now(self) -> datetime:
        return datetime.now(self.cfg.tz)

    def today(self) -> date:
        return self.now().date()

    def snap(self, module: str) -> list[dict[str, Any]]:
        return self.db.snapshot(module) or []

    # ── Tagesansicht ─────────────────────────────────────────────────

    def day(self, day: date) -> dict[str, Any]:
        summary = briefing.day_summary(
            day,
            self.snap("lessons"),
            self.snap("homework"),
            self.snap("exams"),
            self.snap("calendar"),
            self.db.homework_done_ids(),
            self.calendar,
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
        exams = [e for e in self.snap("exams") if e["date"] >= today.isoformat()]
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
            lessons = [l for l in self.snap("lessons") if monday.isoformat() <= l["date"] <= friday.isoformat()]
        else:
            lessons = await self.sync.lessons_for(monday, friday)
            source = "live"
        days = []
        events = self.snap("calendar")
        exams = self.snap("exams")
        for offset in range(5):
            d = monday + timedelta(days=offset)
            iso = d.isoformat()
            holiday = self.calendar.holiday(d)
            days.append({
                "date": iso,
                "label": fmt.long_date(d),
                "short": fmt.short_date(d),
                "holiday": holiday.name if holiday else None,
                "lessons": [l for l in lessons if l["date"] == iso],
                "exams": [e for e in exams if e["date"] == iso],
                "events": [e for e in events if not e["is_holiday"] and e["start"][:10] <= iso <= (e["end"] or e["start"])[:10]],
            })
        return {"monday": monday.isoformat(), "days": days, "source": source, "today": self.today().isoformat()}

    # ── Briefing ─────────────────────────────────────────────────────

    def briefing_target(self, kind: str) -> date:
        today = self.today()
        return today if kind == "morning" else self.calendar.next_school_day(today)

    def briefing(self, kind: str) -> dict[str, Any]:
        today = self.today()
        target = self.briefing_target(kind)
        summary = self.day(target)
        letters, messages = self.unread_counts()
        push = briefing.build_push(kind, summary, today, letters, messages)
        return {"kind": kind, "target": target.isoformat(), "push": push, "summary": summary}

    async def send_briefing(self, kind: str) -> dict[str, Any]:
        result = self.briefing(kind)
        push = result["push"]
        self.db.add_event("briefing", kind, push["title"], push["body"], push["url"], result["target"])
        delivered = await self.pusher.send(push["title"], push["body"], push["url"], tag=f"briefing-{kind}")
        return {**result, "delivered": delivered}

    async def send_exam_reminders(self) -> int:
        settings = self.db.settings()
        today = self.today()
        reminded: list[str] = self.db.get("reminded", []) or []
        sent = 0
        for exam in self.snap("exams"):
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
                details.append(f"{exam['start']} Uhr")
            body = ", ".join(details) + (f"\n{exam['comment']}" if exam.get("comment") else "")
            self.db.add_event("reminder", "exam", title, body, "/#/aufgaben?tab=klausuren", exam["date"])
            await self.pusher.send(title, body, "/#/aufgaben?tab=klausuren", tag=f"reminder-{exam['id']}")
            sent += 1
        # Erinnerungen an vergangene Klausuren vergessen
        valid = {e["id"] for e in self.snap("exams") if e["date"] >= today.isoformat()}
        self.db.set("reminded", [k for k in reminded if k.split(":")[0] in valid])
        return sent

    # ── Status ───────────────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        status = self.db.get("status", {}) or {}
        return {
            "last_success": status.get("last_success"),
            "last_attempt": status.get("last_attempt"),
            "last_error": status.get("last_error"),
            "module_errors": status.get("module_errors") or {},
            "account": status.get("account") or self.sync.account(),
            "running": self.sync.running,
            "demo": self.cfg.demo,
            "devices": len(self.pusher.devices()),
        }
