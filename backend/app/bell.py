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


def times_for(hour: str) -> tuple[str, str]:
    """Beginn und Ende einer Stunde laut Raster, auch für Doppelstunden wie '5/6'."""
    start = CLASS_HOURS.get(first_number(hour), ("", ""))[0]
    end = CLASS_HOURS.get(last_number(hour), ("", ""))[1]
    return start, end
