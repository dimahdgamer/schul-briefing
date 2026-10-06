"""Selbst eingetragene Klausuren und Beurlaubungen.

Sie liegen getrennt von den Schulmanager-Daten, die jeder Abruf ersetzt, in der App-Datenbank
und werden erst bei der Anzeige über den Stundenplan gelegt (`overlay`). Der Abruf und der
Vergleich der Schulmanager-Daten bekommen sie nie zu sehen.
"""

from __future__ import annotations

import re
import secrets
from datetime import date, timedelta
from typing import Any, Callable

from . import bell, fmt

MAX_ENTRIES = 300
MAX_LEAVE_DAYS = 120


def new_id() -> str:
    return "own-" + secrets.token_hex(4)


# ── Eingaben prüfen ──────────────────────────────────────────────────


def _text(value: Any, label: str, limit: int, required: bool = False) -> str:
    text = " ".join(str(value or "").split())
    if required and not text:
        raise ValueError(f"{label} fehlt.")
    if len(text) > limit:
        raise ValueError(f"{label} ist zu lang (höchstens {limit} Zeichen).")
    return text


def _day(value: Any, label: str) -> str:
    try:
        return date.fromisoformat(str(value or "")[:10]).isoformat()
    except ValueError:
        raise ValueError(f"{label}: ungültiges Datum.") from None


def _time(value: Any, label: str) -> str:
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(value or ""))
    if not match or int(match.group(1)) > 23 or int(match.group(2)) > 59:
        raise ValueError(f"{label}: ungültige Uhrzeit.")
    return f"{int(match.group(1)):02d}:{match.group(2)}"


def _hour(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text not in bell.CLASS_HOURS:
        raise ValueError(f"{label}: ungültige Stunde.")
    return text


def clean_exam(body: dict[str, Any]) -> dict[str, Any]:
    start, end = _time(body.get("start"), "Beginn"), _time(body.get("end"), "Ende")
    if end <= start:
        raise ValueError("Das Ende muss nach dem Beginn liegen.")
    return {
        "subject": _text(body.get("subject"), "Fach", 60, required=True),
        "date": _day(body.get("date"), "Datum"),
        "start": start,
        "end": end,
        "type": _text(body.get("type"), "Art", 30) or "Klausur",
        "comment": _text(body.get("comment"), "Notiz", 200),
    }


def clean_course(body: dict[str, Any]) -> dict[str, Any]:
    start, end = _time(body.get("start"), "Beginn"), _time(body.get("end"), "Ende")
    if end <= start:
        raise ValueError("Das Ende muss nach dem Beginn liegen.")
    days = body.get("weekdays")
    if isinstance(days, str):
        days = [d for d in days.split(",") if d.strip()]
    try:
        weekdays = sorted({int(d) for d in days or []})
    except (TypeError, ValueError):
        raise ValueError("Wochentage: ungültig.") from None
    if not weekdays:
        raise ValueError("Bitte mindestens einen Wochentag wählen.")
    if any(d < 0 or d > 4 for d in weekdays):
        raise ValueError("Wochentage: nur Montag bis Freitag.")
    first = _day(body["from"], "Gültig ab") if body.get("from") else ""
    last = _day(body["to"], "Gültig bis") if body.get("to") else ""
    if first and last and last < first:
        raise ValueError("Gültig bis darf nicht vor Gültig ab liegen.")
    return {
        "subject": _text(body.get("subject"), "Fach", 60, required=True),
        "weekdays": weekdays,
        "start": start,
        "end": end,
        "place": _text(body.get("place"), "Ort", 60),
        "from": first,
        "to": last,
    }


def clean_leave(body: dict[str, Any]) -> dict[str, Any]:
    first = _day(body.get("from"), "Von")
    last = _day(body.get("to") or first, "Bis")
    if last < first:
        raise ValueError("Das Ende darf nicht vor dem Beginn liegen.")
    if (date.fromisoformat(last) - date.fromisoformat(first)).days >= MAX_LEAVE_DAYS:
        raise ValueError(f"Eine Beurlaubung kann höchstens {MAX_LEAVE_DAYS} Tage lang sein.")
    hour_from = _hour(body.get("hour_from"), "Von Stunde")
    hour_to = _hour(body.get("hour_to"), "Bis Stunde")
    if hour_from:
        if first != last:
            raise ValueError("Einzelne Stunden gehen nur für einen einzelnen Tag.")
        hour_to = hour_to or hour_from
        if int(hour_to) < int(hour_from):
            raise ValueError("Die letzte Stunde darf nicht vor der ersten liegen.")
    else:
        hour_to = ""
    return {
        "from": first,
        "to": last,
        "hour_from": hour_from,
        "hour_to": hour_to,
        "reason": _text(body.get("reason"), "Grund", 200),
    }


# ── Darstellung ──────────────────────────────────────────────────────


def exam_item(entry: dict[str, Any]) -> dict[str, Any]:
    """Eigene Klausur im selben Format wie die Klassenarbeiten aus Schulmanager."""
    return {
        "id": entry["id"],
        "subject": entry["subject"],
        "date": entry["date"],
        "start": entry["start"],
        "end": entry["end"],
        "hour": "",
        "type": entry.get("type") or "Klausur",
        "comment": entry.get("comment") or "",
        "manual": True,
    }


def leave_hours(leave: dict[str, Any]) -> str:
    """'3. Std' / '3.–6. Std', leer bei ganzen Tagen."""
    first, last = leave.get("hour_from") or "", leave.get("hour_to") or ""
    if not first:
        return ""
    return f"{first}. Std" if not last or last == first else f"{first}.–{last}. Std"


def leave_label(leave: dict[str, Any]) -> str:
    first, last = leave["from"], leave["to"]
    if first == last:
        text = fmt.short_date(first)
        hours = leave_hours(leave)
        return f"{text}, {hours}" if hours else text
    return f"{fmt.short_date(first)} bis {fmt.short_date(last)}"


def leaves_on(leaves: list[dict[str, Any]] | None, iso: str) -> list[dict[str, Any]]:
    return [dict(l, label=leave_label(l), hours=leave_hours(l)) for l in leaves or [] if l["from"] <= iso <= l["to"]]


def leave_window(leave: dict[str, Any]) -> tuple[str, str] | None:
    """Uhrzeiten einer stundenweisen Beurlaubung, None bei ganzen Tagen."""
    if not leave.get("hour_from"):
        return None
    return bell.CLASS_HOURS[leave["hour_from"]][0], bell.CLASS_HOURS[leave.get("hour_to") or leave["hour_from"]][1]


# ── Über den Stundenplan legen ───────────────────────────────────────


def _overlaps(lesson: dict[str, Any], window: tuple[str, str]) -> bool:
    start, end = lesson.get("start") or "", lesson.get("end") or ""
    return bool(start and end and start < window[1] and end > window[0])


def _on_leave(lesson: dict[str, Any], leaves: list[dict[str, Any]]) -> bool:
    for leave in leaves:
        if not leave["from"] <= lesson["date"] <= leave["to"]:
            continue
        window = leave_window(leave)
        if window is None or _overlaps(lesson, window):
            return True
    return False


def _exam_lesson(exam: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"exam:{exam['id']}",
        "date": exam["date"],
        "hour": "",
        "start": exam["start"],
        "end": exam["end"],
        "subject": exam["subject"],
        "abbr": "",
        "course": "",
        "teacher": "",
        "room": "",
        "state": "exam",
        "exam_type": exam["type"],
        "original_subject": "",
        "original_teacher": "",
        "original_room": "",
        "comment": exam.get("comment") or "",
    }


WEEKDAY_NAMES = ["Mo", "Di", "Mi", "Do", "Fr"]


def course_lessons(
    courses: list[dict[str, Any]], first: date, last: date, is_off: Callable[[date], bool]
) -> list[dict[str, Any]]:
    """Wiederkehrender eigener Unterricht (z. B. ein Kurs an einer anderen Schule) als Stunden.

    Er fällt in Schulferien und an Feiertagen aus (`is_off`), nicht aber an schulfreien Tagen nur der
    eigenen Schule. Eine Beurlaubung betrifft ihn (`mark_leave`), Klausuren verdrängen ihn nicht."""
    out: list[dict[str, Any]] = []
    day = first
    while day <= last:
        iso = day.isoformat()
        if day.weekday() < 5 and not is_off(day):
            for course in courses:
                if day.weekday() not in course["weekdays"]:
                    continue
                if (course.get("from") and iso < course["from"]) or (course.get("to") and iso > course["to"]):
                    continue
                out.append({
                    "id": f"course:{course['id']}:{iso}",
                    "date": iso,
                    "hour": bell.hour_span(course["start"], course["end"]),
                    "start": course["start"],
                    "end": course["end"],
                    "subject": course["subject"],
                    "abbr": "",
                    "course": "",
                    "teacher": "",
                    "room": course.get("place") or "",
                    "state": "external",
                    "original_subject": "",
                    "original_teacher": "",
                    "original_room": "",
                    "comment": "",
                })
        day += timedelta(days=1)
    return out


def mark_leave(lessons: list[dict[str, Any]], leaves: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Beurlaubte Stunden bekommen den Zustand "leave". Entfall und EVA bleiben, wie sie sind."""
    if not leaves:
        return lessons
    return [
        dict(l, state="leave") if l["state"] not in ("cancelled", "eva") and _on_leave(l, leaves) else l
        for l in lessons
    ]


def next_course_date(
    course: dict[str, Any], today: date, is_off: Callable[[date], bool], horizon: int = 120
) -> str | None:
    """Der nächste Termin eines wiederkehrenden Unterrichts ab heute, None wenn keiner in Sicht ist."""
    found = course_lessons([course], today, today + timedelta(days=horizon), is_off)
    return found[0]["date"] if found else None


def course_label(course: dict[str, Any]) -> str:
    """'Do · 15:00–17:15 · 9–11. Std', für die Liste in den Einstellungen."""
    days = ", ".join(WEEKDAY_NAMES[d] for d in course["weekdays"])
    hours = bell.hour_span(course["start"], course["end"])
    return f"{days} · {course['start']}–{course['end']}" + (f" · {hours}. Std" if hours else "")


def overlay(
    lessons: list[dict[str, Any]], exams: list[dict[str, Any]], leaves: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Stundenplan mit Klausuren und Beurlaubungen.

    Stunden, die in das Zeitfenster einer eigenen Klausur fallen, verschwinden, an ihre Stelle
    tritt die Klausur. Beurlaubte Stunden bekommen den Zustand "leave". Entfall und EVA bleiben
    unverändert, dort muss man ohnehin nicht hin."""
    own_exams = [e for e in exams if e.get("manual") and e.get("start") and e.get("end")]
    windows: dict[str, list[tuple[str, str]]] = {}
    for exam in own_exams:
        windows.setdefault(exam["date"], []).append((exam["start"], exam["end"]))

    out: list[dict[str, Any]] = []
    for lesson in lessons:
        if any(_overlaps(lesson, w) for w in windows.get(lesson["date"], ())):
            continue
        if lesson["state"] not in ("cancelled", "eva") and _on_leave(lesson, leaves):
            lesson = dict(lesson, state="leave")
        out.append(lesson)
    out.extend(_exam_lesson(e) for e in own_exams)
    out.sort(key=lambda l: (l["date"], l.get("start") or "99:99"))  # stabil: Reihenfolge je Zeit bleibt
    return out
