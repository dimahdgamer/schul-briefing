"""Stundenraster der Schule.

Schulmanager liefert zu jeder Stunde nur die Nummer (classHour.number), keine
Uhrzeit. Die Zeiten kommen deshalb von hier. Unterricht beginnt um 7:55, jede
Stunde dauert 45 Minuten, Pausen nach der 2., 4. und 6. Stunde.
"""

from __future__ import annotations

import re

CLASS_HOURS: dict[str, tuple[str, str]] = {
    "1": ("07:55", "08:40"),
    "2": ("08:40", "09:25"),
    # Pause 09:25–09:45
    "3": ("09:45", "10:30"),
    "4": ("10:30", "11:15"),
    # Pause 11:15–11:35
    "5": ("11:35", "12:20"),
    "6": ("12:20", "13:05"),
    # Pause 13:05–13:30
    "7": ("13:30", "14:15"),
    "8": ("14:15", "15:00"),
    "9": ("15:00", "15:45"),
    "10": ("15:45", "16:30"),
    "11": ("16:30", "17:15"),
}


def first_number(hour: str) -> str:
    """'5/6' -> '5', ' 3 ' -> '3'."""
    match = re.match(r"\s*(\d+)", hour or "")
    return match.group(1) if match else ""


def last_number(hour: str) -> str:
    numbers = re.findall(r"\d+", hour or "")
    return numbers[-1] if numbers else ""


def hour_at(hhmm: str) -> str:
    """Nummer der Stunde, in der die Uhrzeit liegt. In einer Pause die folgende Stunde."""
    if not re.match(r"^\d{2}:\d{2}$", hhmm or ""):
        return ""
    for number, (_start, end) in CLASS_HOURS.items():
        if hhmm < end:
            return number
    return ""


def hour_span(start: str, end: str) -> str:
    """Stunden einer Uhrzeitspanne: 15:00–17:15 wird '9–11', eine einzelne Stunde '9'. Leer außerhalb des Rasters."""
    first = hour_at(start)
    if not first or not re.match(r"^\d{2}:\d{2}$", end or ""):
        return ""
    last = first
    for number, (hour_start, _end) in CLASS_HOURS.items():
        if int(number) >= int(first) and hour_start < end:
            last = number
    return first if last == first else f"{first}–{last}"


def times_for(hour: str) -> tuple[str, str]:
    """Beginn und Ende einer Stunde laut Raster, auch für Doppelstunden wie '5/6'."""
    start = CLASS_HOURS.get(first_number(hour), ("", ""))[0]
    end = CLASS_HOURS.get(last_number(hour), ("", ""))[1]
    return start, end
