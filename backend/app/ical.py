"""iCalendar-Feed (RFC 5545) für Stundenplan, Klassenarbeiten und Schultermine."""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo


def _escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r", "").replace("\n", "\\n")
    )


def _fold(line: str) -> str:
    """Zeilen über 75 Byte umbrechen (Folgezeilen beginnen mit Leerzeichen)."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    parts, current = [], b""
    for char in line:
        encoded = char.encode("utf-8")
        if len(current) + len(encoded) > (75 if not parts else 74):
            parts.append(current.decode("utf-8"))
            current = b""
        current += encoded
    parts.append(current.decode("utf-8"))
    return "\r\n ".join(parts)


def _uid(kind: str, key: str) -> str:
    """UIDs nur aus Buchstaben und Ziffern, manche Kalender-Apps scheitern sonst."""
    return f"{kind}-{hashlib.sha1(key.encode('utf-8')).hexdigest()[:20]}@schulbriefing"


def _utc(day: str, hhmm: str, tz: ZoneInfo) -> str:
    local = datetime.fromisoformat(f"{day}T{hhmm}:00").replace(tzinfo=tz)
    return local.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _event(uid: str, summary: str, start: str, end: str | None = None, all_day_end: str | None = None,
           location: str = "", description: str = "", status: str = "") -> list[str]:
    lines = ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"]
    if all_day_end is not None:
        lines += [f"DTSTART;VALUE=DATE:{start}", f"DTEND;VALUE=DATE:{all_day_end}"]
    else:
        lines += [f"DTSTART:{start}", f"DTEND:{end}"]
    lines.append(f"SUMMARY:{_escape(summary)}")
    if location:
        lines.append(f"LOCATION:{_escape(location)}")
    if description:
        lines.append(f"DESCRIPTION:{_escape(description)}")
    if status:
        lines.append(f"STATUS:{status}")
    lines.append("END:VEVENT")
    return lines


def build(lessons: list[dict[str, Any]], exams: list[dict[str, Any]], events: list[dict[str, Any]],
          tz: ZoneInfo, include_lessons: bool = True) -> str:
    out = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//SchulBriefing//DE",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Schule",
        "X-WR-TIMEZONE:Europe/Berlin",
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
        "X-PUBLISHED-TTL:PT1H",
    ]
    if include_lessons:
        for l in lessons:
            if not l["start"] or not l["end"]:
                continue
            prefix = {"cancelled": "Entfall: ", "eva": "EVA: ", "substitution": "Vertretung: ",
                      "room-change": "Raum: "}.get(l["state"], "")
            desc = []
            if l.get("teacher"):
                desc.append(l["teacher"])
            if l["state"] == "substitution" and l.get("original_subject"):
                desc.append(f"statt {l['original_subject']}")
            if l["state"] == "room-change" and l.get("original_room"):
                desc.append(f"Raum statt {l['original_room']}")
            if l.get("course") and l["course"] != l["subject"]:
                desc.append(l["course"])
            if l.get("comment"):
                desc.append(l["comment"])
            out += _event(
                _uid("lesson", l["id"]),
                prefix + l["subject"],
                _utc(l["date"], l["start"], tz),
                _utc(l["date"], l["end"], tz),
                location=l.get("room") or "",
                description=" · ".join(desc),
                status="CANCELLED" if l["state"] == "cancelled" else "",
            )
    for e in exams:
        summary = f"{e['type']}: {e['subject']}"
        if e.get("start") and e.get("end"):
            out += _event(_uid("exam", str(e["id"])), summary, _utc(e["date"], e["start"], tz),
                          _utc(e["date"], e["end"], tz), description=e.get("comment") or "")
        else:
            day = date.fromisoformat(e["date"])
            out += _event(_uid("exam", str(e["id"])), summary, day.strftime("%Y%m%d"),
                          all_day_end=(day + timedelta(days=1)).strftime("%Y%m%d"), description=e.get("comment") or "")
    for ev in events:
        start = ev["start"]
        uid = _uid("event", str(ev["id"]))
        if ev["all_day"] or len(start) < 16:
            first = date.fromisoformat(start[:10])
            last = date.fromisoformat((ev["end"] or start)[:10])
            if last <= first:
                last = first + timedelta(days=1)
            out += _event(uid, ev["title"], first.strftime("%Y%m%d"), all_day_end=last.strftime("%Y%m%d"),
                          location=ev.get("location") or "", description=ev.get("description") or "")
        else:
            end = ev["end"] if len(ev["end"] or "") >= 16 else start
            out += _event(uid, ev["title"], _utc(start[:10], start[11:16], tz), _utc(end[:10], end[11:16], tz),
                          location=ev.get("location") or "", description=ev.get("description") or "")
    out.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in out) + "\r\n"
