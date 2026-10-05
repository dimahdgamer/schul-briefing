"""Tageszusammenfassung für Morgen-Briefing, Abend-Vorschau und Heute-Ansicht."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from . import fmt
from .holidays import SchoolCalendar


def day_summary(
    day: date,
    lessons: list[dict[str, Any]],
    homework: list[dict[str, Any]],
    exams: list[dict[str, Any]],
    events: list[dict[str, Any]],
    done_ids: set[str],
    calendar: SchoolCalendar,
) -> dict[str, Any]:
    iso = day.isoformat()
    day_lessons = [l for l in lessons if l["date"] == iso]
    active = [l for l in day_lessons if l["state"] != "cancelled"]
    planned_start = min((l["start"] for l in day_lessons if l["start"]), default="")
    planned_end = max((l["end"] for l in day_lessons if l["end"]), default="")
    start = min((l["start"] for l in active if l["start"]), default="")
    end = max((l["end"] for l in active if l["end"]), default="")

    first_active = next((l for l in active if l["start"] == start), None) if start else None
    upcoming_exams = [e for e in exams if iso <= e["date"] <= (day + timedelta(days=14)).isoformat()]
    day_events = [
        e for e in events
        if not e["is_holiday"] and e["start"][:10] <= iso <= (e["end"] or e["start"])[:10]
    ]
    hw_due = [h for h in homework if h["due"] == iso]

    return {
        "date": iso,
        "label": fmt.long_date(day),
        "school_day": calendar.is_school_day(day),
        "break": calendar.current_break(day),
        "lessons": day_lessons,
        "start": start,
        "end": end,
        "planned_start": planned_start,
        "planned_end": planned_end,
        "late_start": bool(start and planned_start and start > planned_start),
        "early_end": bool(end and planned_end and end < planned_end),
        "first_lesson": first_active,
        "all_cancelled": bool(day_lessons) and not active,
        "changes": [l for l in day_lessons if l["state"] != "regular"],
        "homework_due": [dict(h, done=h["id"] in done_ids) for h in hw_due],
        "exams_today": [e for e in exams if e["date"] == iso],
        "exams_upcoming": upcoming_exams,
        "events": day_events,
    }


def _change_line(lesson: dict[str, Any]) -> str:
    prefix = f"{lesson['hour']}. Std: " if lesson["hour"] else ""
    if lesson["state"] == "cancelled":
        return f"{prefix}{lesson['subject']} entfällt"
    if lesson["state"] == "substitution":
        orig = lesson.get("original_subject")
        what = f"{orig} → {lesson['subject']}" if orig and orig != lesson["subject"] else lesson["subject"]
        extra = f" ({lesson['room']})" if lesson.get("room") else ""
        return f"{prefix}Vertretung {what}{extra}"
    if lesson["state"] == "room-change":
        return f"{prefix}{lesson['subject']} in {lesson['room']}"
    if lesson["state"] == "extra":
        return f"{prefix}zusätzlich {lesson['subject']}"
    return f"{prefix}{lesson['subject']}"


def build_push(
    kind: str,
    summary: dict[str, Any],
    today: date,
    unread_letters: int,
    unread_messages: int,
) -> dict[str, str]:
    """Kurztext für die Push-Benachrichtigung (Titel + max. ~6 Zeilen)."""
    day = fmt.parse(summary["date"])
    prefix = "Heute" if kind == "morning" else fmt.relative_day(day, today)
    lines: list[str] = []

    if not summary["lessons"]:
        title = f"{prefix}: kein Unterricht eingetragen"
    elif summary["all_cancelled"]:
        title = f"{prefix}: Der gesamte Unterricht fällt aus"
    else:
        title = f"{prefix} {summary['start']}–{summary['end']} Uhr"
        n = len(summary["changes"])
        if n:
            title += f" · {fmt.plural(n, 'Änderung', 'Änderungen')}"

    if summary["late_start"] and summary["first_lesson"]:
        first = summary["first_lesson"]
        lines.append(f"Später los: Beginn {summary['start']} ({first['hour']}. Std {first['subject']})")
    if summary["early_end"]:
        lines.append(f"Früher Schluss um {summary['end']}")

    change_lines = [_change_line(l) for l in summary["changes"]]
    if len(change_lines) > 3:
        lines.extend(change_lines[:3])
        lines.append(f"+ {len(change_lines) - 3} weitere Änderungen")
    else:
        lines.extend(change_lines)

    for exam in summary["exams_today"]:
        lines.append(f"{exam['type']} {prefix.lower()}: {exam['subject']}")

    open_hw = [h for h in summary["homework_due"] if not h.get("done")]
    if open_hw:
        lines.append("Hausaufgaben fällig: " + fmt.join_de(sorted({h["subject"] for h in open_hw})))

    later_exams = [e for e in summary["exams_upcoming"] if e["date"] != summary["date"]]
    if later_exams:
        e = later_exams[0]
        lines.append(f"Nächste {e['type']}: {e['subject']} {fmt.in_days(e['date'], day)}")

    for ev in summary["events"][:2]:
        lines.append(f"Termin: {ev['title']}")

    unread = []
    if unread_letters:
        unread.append(fmt.plural(unread_letters, "ungelesener Brief", "ungelesene Briefe"))
    if unread_messages:
        unread.append(fmt.plural(unread_messages, "neue Nachricht", "neue Nachrichten"))
    if unread:
        lines.append(fmt.join_de(unread))

    if not lines:
        lines.append("Keine Änderungen, alles nach Plan.")

    return {"title": title, "body": "\n".join(lines), "url": f"/#/heute?date={summary['date']}"}
