"""Rohantworten der Schulmanager-API -> stabile, flache Strukturen.

Alles hier ist bewusst defensiv: Felder können fehlen, Typen schwanken
(Zahl vs. String), und die API ändert sich ohne Ankündigung.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date
from typing import Any, Callable

from . import bell

LETTER_URL = "https://login.schulmanager-online.de/#/modules/letters/view/{id}"
MESSAGE_URL = "https://login.schulmanager-online.de/#/modules/messenger/messages/{id}"


def _s(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _hhmm(value: Any) -> str:
    text = _s(value)
    return text[:5] if re.match(r"^\d{1,2}:\d{2}", text) else ""


def _short_hash(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]


def _teacher(teachers: Any) -> str:
    if not isinstance(teachers, list) or not teachers:
        return ""
    names = []
    for t in teachers:
        if isinstance(t, dict):
            names.append(_s(t.get("lastname")) or _s(t.get("abbreviation")))
    return ", ".join(n for n in names if n)


def _subject(lesson: dict[str, Any] | None) -> tuple[str, str, str]:
    """(Fachname, Kürzel, Kurs). Der Kurs ("M  L2") steht in subjectLabel."""
    if not lesson:
        return "", "", ""
    subject = lesson.get("subject") or {}
    label = re.sub(r"\s+", " ", _s(lesson.get("subjectLabel")))
    name = _s(subject.get("name")) or label or _s(subject.get("abbreviation"))
    return name, _s(subject.get("abbreviation")), label


def _hour_sort(hour: str) -> float:
    match = re.match(r"(\d+)", hour)
    return float(match.group(1)) if match else 99.0


# ── Stundenplan ──────────────────────────────────────────────────────


def lessons(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    for item in raw:
        if not isinstance(item, dict) or not item.get("date"):
            continue
        day = _s(item["date"])[:10]
        class_hour = item.get("classHour") or {}
        hour = _s(class_hour.get("number"))
        actual = item.get("actualLesson") or None
        originals = item.get("originalLessons") or []
        original = originals[0] if originals and isinstance(originals[0], dict) else None
        lesson_type = _s(item.get("type"))

        subject, abbr, course = _subject(actual)
        orig_subject, orig_abbr, orig_course = _subject(original)
        teacher = _teacher((actual or {}).get("teachers"))
        orig_teacher = _teacher((original or {}).get("teachers"))
        room = _s(((actual or {}).get("room") or {}).get("name"))
        orig_room = _s(((original or {}).get("room") or {}).get("name"))

        subject_changed = bool(original is not None and orig_subject and orig_subject != subject)
        teacher_changed = bool(original is not None and orig_teacher and orig_teacher != teacher)
        room_changed = bool(original is not None and orig_room and orig_room != room)

        cancelled = bool(item.get("isCancelled")) or lesson_type == "cancelledLesson" or (
            actual is None and original is not None
        )
        if cancelled:
            state = "cancelled"
        elif subject_changed or teacher_changed:
            state = "substitution"
        elif room_changed:
            # Schulmanager markiert auch reine Raumwechsel mit isSubstitution
            state = "room-change"
        elif original is None and (item.get("isSubstitution") or lesson_type == "substitution"):
            state = "substitution"
        elif original is None and (item.get("isNew") or lesson_type in {"additionalLesson", "newLesson"}):
            state = "extra"
        elif lesson_type == "event":
            state = "event"
        else:
            # Auch "changedLesson" ohne erkennbaren Unterschied zählt als regulär
            state = "regular"

        display_subject = (orig_subject or subject) if cancelled else (subject or orig_subject or "Unterricht")
        key_subject = orig_subject or subject or "?"
        base_id = f"{day}#{hour}#{key_subject}"
        seen[base_id] = seen.get(base_id, 0) + 1
        lesson_id = base_id if seen[base_id] == 1 else f"{base_id}#{seen[base_id]}"

        start, end = _hhmm(class_hour.get("from")), _hhmm(class_hour.get("until"))
        if not start or not end:
            start, end = bell.times_for(hour)

        out.append(
            {
                "id": lesson_id,
                "date": day,
                "hour": hour,
                "start": start,
                "end": end,
                "subject": display_subject,
                "abbr": abbr or orig_abbr,
                "course": (orig_course if cancelled else course) or orig_course,
                "teacher": teacher if not cancelled else (orig_teacher or teacher),
                "room": room if not cancelled else (orig_room or room),
                "state": state,
                "original_subject": orig_subject,
                "original_teacher": orig_teacher,
                "original_room": orig_room,
                "comment": _s(item.get("comment")) or _s((actual or {}).get("comment")),
            }
        )
    out.sort(key=lambda l: (l["date"], _hour_sort(l["hour"]), l["start"]))
    return out


# ── Hausaufgaben ─────────────────────────────────────────────────────


def _subject_keys(*names: str) -> set[str]:
    return {re.sub(r"\s+", " ", n).strip().lower() for n in names if n}


def lesson_days(lesson_list: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Fach -> sortierte Tage, an denen es tatsächlich stattfindet."""
    index: dict[str, set[str]] = {}
    for lesson in lesson_list:
        if lesson["state"] == "cancelled":
            continue
        for key in _subject_keys(lesson["subject"], lesson.get("abbr", "")):
            index.setdefault(key, set()).add(lesson["date"])
    return {key: sorted(days) for key, days in index.items()}


def homework(
    raw: Any,
    lesson_list: list[dict[str, Any]],
    next_school_day: Callable[[date], date],
) -> list[dict[str, Any]]:
    """Schulmanager liefert das Datum, an dem die Aufgabe aufgegeben wurde.
    Fällig ist sie in der nächsten Stunde desselben Fachs. Steht das Fach nicht im
    bekannten Stundenplan, wird der nächste Schultag angenommen."""
    if not isinstance(raw, list):
        return []
    days_by_subject = lesson_days(lesson_list)
    known_from = min((l["date"] for l in lesson_list), default="")
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        text = _s(item.get("homework") or item.get("text"))
        if not text:
            continue
        subject = _s(item.get("subject")) if not isinstance(item.get("subject"), dict) else _s(
            item["subject"].get("name")
        )
        subject = subject or "Fach"
        assigned = _s(item.get("date"))[:10]
        due = _s(item.get("homeworkDueDate") or item.get("dueDate"))[:10]
        estimated = not due
        if not due and assigned and known_from and assigned >= known_from:
            for key in _subject_keys(subject):
                later = [d for d in days_by_subject.get(key, []) if d > assigned]
                if later:
                    due, estimated = later[0], False
                    break
        if not due and assigned:
            try:
                due = next_school_day(date.fromisoformat(assigned)).isoformat()
            except ValueError:
                due = assigned
        teacher = item.get("teacher") or {}
        out.append(
            {
                "id": "hw-" + _short_hash(assigned, subject, text),
                "subject": subject,
                "text": text,
                "assigned": assigned,
                "due": due,
                "due_estimated": estimated,
                "teacher": _s(teacher.get("lastname")) if isinstance(teacher, dict) else _s(teacher),
            }
        )
    out.sort(key=lambda h: (h["due"], h["subject"]))
    return out


# ── Klassenarbeiten ──────────────────────────────────────────────────


def exams(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        subject = item.get("subject") or {}
        course = item.get("course") or {}
        exam_type = item.get("type") or {}
        class_hour = item.get("classHour") or item.get("startClassHour") or {}
        end_hour = item.get("endClassHour") or class_hour
        exam_date = _s(item.get("date"))[:10]
        hour = _s(class_hour.get("number"))
        end_number = _s(end_hour.get("number")) or hour
        if hour and end_number and end_number != hour:
            hour = f"{hour}/{end_number}"
        start = _hhmm(item.get("startTime") or class_hour.get("from")) or bell.times_for(hour)[0]
        end = _hhmm(item.get("endTime") or end_hour.get("until")) or bell.times_for(hour)[1]
        name = _s(subject.get("name")) or _s(item.get("subjectText")) or _s(course.get("name")) or "Klassenarbeit"
        out.append(
            {
                "id": _s(item.get("id")) or "ex-" + _short_hash(exam_date, name),
                "subject": name,
                "date": exam_date,
                "start": start,
                "end": end,
                "hour": hour,
                "type": _s(exam_type.get("name") if isinstance(exam_type, dict) else exam_type)
                or _s(item.get("typeName"))
                or "Klassenarbeit",
                "comment": _s(item.get("comment")),
            }
        )
    out.sort(key=lambda e: (e["date"], e["start"]))
    return out


# ── Elternbriefe & Nachrichten ───────────────────────────────────────


def letters(raw: Any, student_id: Any = None) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if not isinstance(item, dict) or item.get("id") is None:
            continue
        statuses = [s for s in item.get("studentStatuses") or [] if isinstance(s, dict)]
        if student_id is not None:
            own = [s for s in statuses if str(s.get("studentId")) == str(student_id)]
            statuses = own or statuses
        unread = any(s.get("readTimestamp") is None for s in statuses)
        out.append(
            {
                "id": _s(item["id"]),
                "title": _s(item.get("title")) or _s(item.get("subject")) or "Elternbrief",
                "sender": _s(item.get("senderName")) or _s(item.get("createdBy")),
                "sent_at": _s(item.get("sentDate")) or _s(item.get("createdAt")),
                "unread": unread,
                "needs_answer": bool(item.get("questions")) or item.get("answerDeadline") is not None,
                "deadline": _s(item.get("answerDeadline"))[:10],
                "url": LETTER_URL.format(id=item["id"]),
            }
        )
    out.sort(key=lambda l: l["sent_at"], reverse=True)
    return out


def threads(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out = []
    for sub in raw:
        if not isinstance(sub, dict) or sub.get("isArchived"):
            continue
        thread = sub.get("thread") or {}
        last = thread.get("lastMessage") or {}
        thread_id = _s(sub.get("threadId") or thread.get("id"))
        out.append(
            {
                "id": thread_id,
                "subject": _s(thread.get("subject")) or "Nachricht",
                "sender": _s(thread.get("senderString")),
                "last_at": _s(thread.get("lastMessageTimestamp")),
                "unread": int(sub.get("unreadCount") or 0),
                "preview": _strip_html(_s(last.get("text")))[:160],
                "url": MESSAGE_URL.format(id=thread_id),
            }
        )
    out.sort(key=lambda t: t["last_at"], reverse=True)
    return out


def _strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()


# ── Kalender ─────────────────────────────────────────────────────────


def calendar(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, dict):
        return []
    items = (raw.get("nonRecurringEvents") or []) + (raw.get("recurringEvents") or [])
    out = []
    for ev in items:
        if not isinstance(ev, dict) or not ev.get("start"):
            continue
        start = _s(ev.get("start"))
        out.append(
            {
                "id": f"{_s(ev.get('id'))}@{start[:10]}",
                "title": _s(ev.get("summary")) or "Termin",
                "start": start,
                "end": _s(ev.get("end")) or start,
                "all_day": bool(ev.get("allDay")),
                "location": _s(ev.get("location")),
                "description": _strip_html(_s(ev.get("description")))[:400],
                "is_holiday": str(ev.get("categoryId")) == "-1",
            }
        )
    out.sort(key=lambda e: e["start"])
    return out
