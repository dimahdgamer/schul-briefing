"""Vergleicht zwei Snapshots und beschreibt die Unterschiede als Änderungen.

Reine Funktionen ohne Seiteneffekte, damit sie sich gut testen lassen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from . import fmt


@dataclass
class Change:
    category: str  # lessons | homework | exams | letters | messages | calendar
    kind: str
    title: str
    body: str
    ref_date: str | None = None
    url: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


def _by_id(items: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    return {str(i["id"]): i for i in items or []}


# ── Stundenplan ──────────────────────────────────────────────────────

LESSON_FIELDS = ("state", "subject", "teacher", "room")


def _lesson_line(lesson: dict[str, Any], previous: dict[str, Any] | None) -> tuple[str, str] | None:
    """(Art, Textzeile) für eine geänderte Stunde oder None, wenn nichts Relevantes."""
    hour = fmt.hour_label(lesson["hour"])
    state = lesson["state"]
    was = previous["state"] if previous else None

    if state == "cancelled" and was != "cancelled":
        return "cancelled", f"{hour}: {lesson['subject']} fällt aus"
    if was == "cancelled" and state not in ("cancelled", "eva"):
        return "restored", f"{hour}: {lesson['subject']} findet doch statt"
    if was in ("substitution", "room-change", "extra", "eva") and state == "regular":
        room = f" in {_room(lesson['room'])}" if lesson.get("room") else ""
        return "normal", f"{hour}: {lesson['subject']} wieder wie geplant{room}"
    if state == "eva":
        return None if was == "eva" else ("eva", f"{hour}: {lesson['subject']} als EVA")
    if state == "substitution":
        if previous and was == "substitution" and all(previous[f] == lesson[f] for f in LESSON_FIELDS):
            return None
        orig = lesson.get("original_subject") or ""
        subject = lesson["subject"]
        what = f"{orig} → {subject}" if orig and orig != subject else subject
        detail = ", ".join(x for x in (lesson.get("teacher"), _room(lesson.get("room"))) if x)
        return "substitution", f"{hour}: Vertretung {what}" + (f" ({detail})" if detail else "")
    if state == "extra" and was != "extra":
        return "extra", f"{hour}: Zusätzlich {lesson['subject']}" + (
            f" ({_room(lesson['room'])})" if lesson.get("room") else ""
        )
    room_before = (previous or {}).get("room") or lesson.get("original_room") or ""
    if state == "room-change" and (was != "room-change" or (previous and previous["room"] != lesson["room"])):
        return "room", f"{hour}: {lesson['subject']} in {_room(lesson['room'])}" + (
            f" statt {room_before}" if room_before and room_before != lesson["room"] else ""
        )
    if previous and state == "regular" and was == "regular" and previous["room"] != lesson["room"] and lesson["room"]:
        return "room", f"{hour}: {lesson['subject']} jetzt in {_room(lesson['room'])}"
    return None


def _room(room: str | None) -> str:
    if not room:
        return ""
    return room if room.lower().startswith(("raum", "turnhalle", "halle", "aula")) else f"Raum {room}"


def diff_lessons(
    prev: list[dict[str, Any]] | None, curr: list[dict[str, Any]], today: date
) -> list[Change]:
    if prev is None:
        return []
    today_iso = today.isoformat()
    before = _by_id(prev)
    after = _by_id(curr)
    covered_days = {l["date"] for l in curr} & {l["date"] for l in prev}

    per_day: dict[str, list[tuple[str, str, dict[str, Any]]]] = {}
    for lesson_id, lesson in after.items():
        if lesson["date"] < today_iso:
            continue
        line = _lesson_line(lesson, before.get(lesson_id))
        if line:
            per_day.setdefault(lesson["date"], []).append((line[0], line[1], lesson))

    for lesson_id, lesson in before.items():
        if lesson_id in after or lesson["date"] < today_iso or lesson["date"] not in covered_days:
            continue
        if lesson["state"] == "cancelled":
            continue
        # Stunde ist ganz aus dem Plan verschwunden: wie ein Entfall behandeln.
        # Ausnahme: dieselbe Stunde existiert mit anderem Fach (dann ist es eine Vertretung, schon erfasst).
        same_slot = any(
            l["date"] == lesson["date"] and l["hour"] == lesson["hour"] for l in after.values()
        )
        if not same_slot:
            per_day.setdefault(lesson["date"], []).append(
                ("cancelled", f"{fmt.hour_label(lesson['hour'])}: {lesson['subject']} fällt aus", lesson)
            )

    changes = []
    for day in sorted(per_day):
        entries = sorted(per_day[day], key=lambda e: _hour_key(e[2]["hour"]))
        kinds = {e[0] for e in entries}
        label = fmt.relative_day(day, today)
        if len(entries) == 1:
            kind, line, lesson = entries[0]
            title = {
                "cancelled": f"{label}: {lesson['subject']} fällt aus",
                "restored": f"{label}: {lesson['subject']} findet statt",
                "substitution": f"{label}: Vertretung in {lesson.get('original_subject') or lesson['subject']}",
                "extra": f"{label}: Zusätzliche Stunde",
                "eva": f"{label}: EVA in {lesson['subject']}",
                "room": f"{label}: Raumänderung {lesson['subject']}",
                "normal": f"{label}: {lesson['subject']} wieder wie geplant",
            }[kind]
        else:
            title = f"{label}: {fmt.plural(len(entries), 'Änderung', 'Änderungen')} im Stundenplan"
        changes.append(
            Change(
                category="lessons",
                kind="cancelled" if kinds == {"cancelled"} else (next(iter(kinds)) if len(kinds) == 1 else "mixed"),
                title=title,
                body="\n".join(e[1] for e in entries),
                ref_date=day,
                url=f"/#/heute?date={day}",
                data={"lessons": [e[2]["id"] for e in entries]},
            )
        )
    return changes


def _hour_key(hour: str) -> int:
    digits = "".join(ch for ch in hour if ch.isdigit())
    return int(digits) if digits else 99


# ── Hausaufgaben ─────────────────────────────────────────────────────


def diff_homework(prev: list[dict[str, Any]] | None, curr: list[dict[str, Any]], today: date) -> list[Change]:
    if prev is None:
        return []
    known = _by_id(prev)
    new = [h for h in curr if h["id"] not in known and (not h["due"] or h["due"] >= today.isoformat())]
    if not new:
        return []
    if len(new) == 1:
        h = new[0]
        due = ""
        if h["due"]:
            rel = fmt.relative_day(h["due"], today)
            due = f" bis {rel.lower() if rel in ('Heute', 'Morgen', 'Übermorgen') else rel}"
        what = "EVA-Aufgabe" if h.get("eva") else "Hausaufgabe"
        return [
            Change(
                category="homework",
                kind="new",
                title=f"Neue {what} in {h['subject']}{due}",
                body=h["text"][:240],
                ref_date=h["due"] or None,
                url="/#/aufgaben",
                data={"ids": [h["id"]]},
            )
        ]
    subjects = sorted({h["subject"] for h in new})
    return [
        Change(
            category="homework",
            kind="new",
            title=f"{len(new)} neue Hausaufgaben",
            body="\n".join(f"{h['subject']}: {h['text'][:90]}" for h in new[:6]),
            url="/#/aufgaben",
            data={"ids": [h["id"] for h in new], "subjects": subjects},
        )
    ]


# ── Klassenarbeiten ──────────────────────────────────────────────────


def diff_exams(prev: list[dict[str, Any]] | None, curr: list[dict[str, Any]], today: date) -> list[Change]:
    if prev is None:
        return []
    today_iso = today.isoformat()
    before, after = _by_id(prev), _by_id(curr)
    changes = []
    for exam_id, exam in after.items():
        if exam["date"] < today_iso:
            continue
        old = before.get(exam_id)
        when = f"{fmt.short_date(exam['date'])} ({fmt.in_days(exam['date'], today)})"
        if old is None:
            changes.append(
                Change("exams", "new", f"Neue {exam['type']}: {exam['subject']}", when + _comment(exam),
                       exam["date"], "/#/aufgaben?tab=klausuren", {"id": exam_id})
            )
        elif old["date"] != exam["date"]:
            changes.append(
                Change("exams", "moved", f"{exam['subject']}-{exam['type']} verschoben",
                       f"von {fmt.short_date(old['date'])} auf {when}", exam["date"],
                       "/#/aufgaben?tab=klausuren", {"id": exam_id})
            )
    for exam_id, exam in before.items():
        if exam_id not in after and exam["date"] >= today_iso:
            changes.append(
                Change("exams", "removed", f"{exam['subject']}-{exam['type']} entfernt",
                       f"war für {fmt.short_date(exam['date'])} eingetragen", exam["date"],
                       "/#/aufgaben?tab=klausuren", {"id": exam_id})
            )
    return changes


def _comment(exam: dict[str, Any]) -> str:
    return f"\n{exam['comment']}" if exam.get("comment") else ""


# ── Fehlzeiten ───────────────────────────────────────────────────────


def diff_absences(prev: dict[str, Any] | None, curr: dict[str, Any], today: date) -> list[Change]:
    """Neue Fehlzeiten melden, damit eine fehlende Entschuldigung nicht untergeht."""
    if prev is None:
        return []
    known = _by_id(prev.get("items"))
    new = [
        a for a in curr.get("items", [])
        if a["id"] not in known and a["date"] >= (today - timedelta(days=14)).isoformat()
    ]
    if not new:
        return []

    def line(a: dict[str, Any]) -> str:
        status = "unentschuldigt" if a["unexcused"] else ", ".join(a["marks"]).lower()
        return f"{fmt.short_date(a['date'])}, {a['span']}: {status}"

    if len(new) == 1:
        a = new[0]
        return [Change("absences", "new", "Neue Fehlzeit" + (" (unentschuldigt)" if a["unexcused"] else ""),
                       line(a), a["date"], "/#/aufgaben?tab=fehlzeiten", {"ids": [a["id"]]})]
    open_count = sum(1 for a in new if a["unexcused"])
    title = f"{len(new)} neue Fehlzeiten" + (f", {open_count} unentschuldigt" if open_count else "")
    return [Change("absences", "new", title, "\n".join(line(a) for a in new[:6]), None,
                   "/#/aufgaben?tab=fehlzeiten", {"ids": [a["id"] for a in new]})]


# ── Post ─────────────────────────────────────────────────────────────


def diff_letters(prev: list[dict[str, Any]] | None, curr: list[dict[str, Any]]) -> list[Change]:
    if prev is None:
        return []
    known = _by_id(prev)
    changes = []
    for letter in curr:
        if letter["id"] in known:
            continue
        body = letter["sender"] or "Schule"
        if letter.get("deadline"):
            body += f" · Antwort bis {fmt.short_date(letter['deadline'])}"
        changes.append(
            Change("letters", "new", f"Neuer Elternbrief: {letter['title']}", body, None, "/#/post", {"id": letter["id"], "external": letter["url"]})
        )
    return changes


def diff_threads(prev: list[dict[str, Any]] | None, curr: list[dict[str, Any]]) -> list[Change]:
    if prev is None:
        return []
    before = _by_id(prev)
    changes = []
    for thread in curr:
        old = before.get(thread["id"])
        old_unread = old["unread"] if old else 0
        if thread["unread"] > old_unread or (old and thread["unread"] > 0 and thread["last_at"] != old["last_at"]):
            changes.append(
                Change(
                    "messages",
                    "new",
                    f"Neue Nachricht: {thread['subject']}",
                    (f"{thread['sender']}: " if thread["sender"] else "") + (thread["preview"] or ""),
                    None,
                    "/#/post",
                    {"id": thread["id"], "external": thread["url"]},
                )
            )
    return changes


# ── Kalender ─────────────────────────────────────────────────────────


def diff_calendar(prev: list[dict[str, Any]] | None, curr: list[dict[str, Any]], today: date) -> list[Change]:
    if prev is None:
        return []
    known = _by_id(prev)
    changes = []
    for event in curr:
        if event["id"] in known or event["start"][:10] < today.isoformat():
            continue
        when = fmt.short_date(event["start"])
        if not event["all_day"] and len(event["start"]) >= 16:
            when += f" {event['start'][11:16]}"
        changes.append(
            Change("calendar", "new", f"Neuer Termin: {event['title']}",
                   when + (f" · {event['location']}" if event.get("location") else ""),
                   event["start"][:10], "/#/woche", {"id": event["id"]})
        )
    return changes
