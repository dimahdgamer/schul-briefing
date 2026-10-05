"""Rohantworten der Schulmanager-API -> stabile, flache Strukturen.

Alles hier ist bewusst defensiv: Felder können fehlen, Typen schwanken
(Zahl vs. String), und die API ändert sich ohne Ankündigung.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date
from typing import Any, Callable

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


def _subject(lesson: dict[str, Any] | None) -> tuple[str, str]:
    if not lesson:
        return "", ""
    subject = lesson.get("subject") or {}
    name = _s(lesson.get("subjectLabel")) or _s(subject.get("name")) or _s(subject.get("abbreviation"))
    return name, _s(subject.get("abbreviation"))


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

        subject, abbr = _subject(actual)
        orig_subject, orig_abbr = _subject(original)
        teacher = _teacher((actual or {}).get("teachers"))
        orig_teacher = _teacher((original or {}).get("teachers"))
        room = _s(((actual or {}).get("room") or {}).get("name"))
        orig_room = _s(((original or {}).get("room") or {}).get("name"))

        cancelled = bool(item.get("isCancelled")) or lesson_type == "cancelledLesson" or (
            actual is None and original is not None
        )
        if cancelled:
            state = "cancelled"
        elif item.get("isSubstitution") or lesson_type == "substitution" or (
            original is not None
            and ((orig_subject and orig_subject != subject) or (orig_teacher and orig_teacher != teacher))
        ):
            state = "substitution"
        elif original is not None and orig_room and orig_room != room:
            state = "room-change"
        elif original is None and (item.get("isNew") or lesson_type in {"additionalLesson", "newLesson"}):
            state = "extra"
        elif lesson_type == "event":
            state = "event"
        else:
            state = "regular"

        display_subject = subject or orig_subject or "Unterricht"
        key_subject = orig_subject or subject or "?"
        base_id = f"{day}#{hour}#{key_subject}"
        seen[base_id] = seen.get(base_id, 0) + 1
        lesson_id = base_id if seen[base_id] == 1 else f"{base_id}#{seen[base_id]}"

        out.append(
            {
                "id": lesson_id,
                "date": day,
                "hour": hour,
                "start": _hhmm(class_hour.get("from")),
                "end": _hhmm(class_hour.get("until")),
                "subject": display_subject if not cancelled else (orig_subject or display_subject),
                "abbr": abbr or orig_abbr,
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


def homework(raw: Any, next_school_day: Callable[[date], date]) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
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
        exam_date = _s(item.get("date"))[:10]
        name = _s(subject.get("name")) or _s(item.get("subjectText")) or _s(course.get("name")) or "Klassenarbeit"
        out.append(
            {
                "id": _s(item.get("id")) or "ex-" + _short_hash(exam_date, name),
                "subject": name,
                "date": exam_date,
                "start": _hhmm(item.get("startTime") or class_hour.get("from")),
                "end": _hhmm(item.get("endTime") or class_hour.get("until")),
                "hour": _s(class_hour.get("number")),
                "type": _s(exam_type.get("name") if isinstance(exam_type, dict) else exam_type)
                or _s(item.get("typeName"))
                or "Klassenarbeit",
                "comment": _s(item.get("comment")),
            }
        )
    out.sort(key=lambda e: (e["date"], e["start"]))
    return out


# ── Noten ────────────────────────────────────────────────────────────

GRADE_RE = re.compile(r"^([1-6])\s*([+-])?$")


def parse_grade(text: str, system: int) -> float | None:
    text = text.strip().replace(",", ".")
    if system == 1:
        return float(text) if re.fullmatch(r"\d{1,2}", text) and int(text) <= 15 else None
    match = GRADE_RE.match(text)
    if match:
        base = float(match.group(1))
        return base - 0.3 if match.group(2) == "+" else base + 0.3 if match.group(2) == "-" else base
    try:
        value = float(text)
    except ValueError:
        return None
    return value if 1 <= value <= 6 else None


def decode_grade(value: Any, fallback_system: int = 0) -> tuple[str, float | None, int]:
    """'0~2+' -> ('2+', 1.7, 0); '1~13' -> ('13', 13.0, 1)."""
    if value is None:
        return "", None, fallback_system
    raw = str(value)
    if "~" in raw:
        prefix, _, text = raw.partition("~")
        system = int(prefix) if prefix.isdigit() else fallback_system
    else:
        text, system = raw, fallback_system
    return text.strip(), parse_grade(text, system), system


def _weighted(grades: list[dict[str, Any]]) -> float | None:
    usable = [g for g in grades if g["numeric"] is not None]
    if not usable:
        return None
    total_weight = sum(g["weight"] or 1 for g in usable)
    return round(sum(g["numeric"] * (g["weight"] or 1) for g in usable) / total_weight, 2)


def grades(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, dict):
        return []
    subjects = {str(s.get("id")): s for s in raw.get("subjects") or [] if isinstance(s, dict)}
    out = []
    for course in raw.get("courses") or []:
        if not isinstance(course, dict):
            continue
        subject = subjects.get(str(course.get("subjectId"))) or {}
        preset = course.get("gradingPreset") or {}
        system = int(preset.get("gradingSystem") or 0)
        items = []
        for g in course.get("grades") or []:
            if not isinstance(g, dict):
                continue
            text, numeric, g_system = decode_grade(g.get("value", g.get("grade")), system)
            if not text:
                continue
            g_type = g.get("gradeType") or {}
            items.append(
                {
                    "id": _s(g.get("id")) or "gr-" + _short_hash(text, _s(g.get("date"))),
                    "value": text,
                    "numeric": numeric,
                    "system": g_system,
                    "weight": float(g.get("weight") or 1),
                    "type": _s(g_type.get("name") if isinstance(g_type, dict) else g_type),
                    "date": (_s(g.get("date")) or _s(g.get("createdAt")))[:10],
                    "comment": _s(g.get("comment")),
                }
            )
        if not items:
            continue
        items.sort(key=lambda g: g["date"])
        out.append(
            {
                "id": _s(course.get("id")),
                "subject": _s(subject.get("name")) or _s(course.get("name")) or "Fach",
                "abbr": _s(subject.get("abbreviation")),
                "system": system,
                "grades": items,
                "average": _weighted(items),
                "final": _final_grade(raw.get("finalGrades"), course.get("id")),
            }
        )
    out.sort(key=lambda s: s["subject"].lower())
    return out


def _final_grade(final_grades: Any, course_id: Any) -> str:
    if not isinstance(final_grades, list):
        return ""
    for entry in final_grades:
        if not isinstance(entry, dict):
            continue
        if entry.get("courseId") is not None and str(entry.get("courseId")) != str(course_id):
            continue
        value = entry.get("value", entry.get("grade"))
        if isinstance(value, dict):
            value = value.get("value")
        if value is not None:
            return decode_grade(value)[0]
    return ""


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
