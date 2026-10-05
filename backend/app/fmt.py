"""Deutsche Datums- und Textformatierung für Benachrichtigungen."""

from __future__ import annotations

from datetime import date

WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
WEEKDAYS_SHORT = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
MONTHS = [
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
]


def parse(value: str | date) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def long_date(value: str | date) -> str:
    d = parse(value)
    return f"{WEEKDAYS[d.weekday()]}, {d.day}. {MONTHS[d.month - 1]}"


def short_date(value: str | date) -> str:
    d = parse(value)
    return f"{WEEKDAYS_SHORT[d.weekday()]} {d.day:02d}.{d.month:02d}."


def relative_day(value: str | date, today: date) -> str:
    d = parse(value)
    delta = (d - today).days
    if delta == 0:
        return "Heute"
    if delta == 1:
        return "Morgen"
    if delta == 2:
        return "Übermorgen"
    if 2 < delta < 7:
        return WEEKDAYS[d.weekday()]
    return short_date(d)


def in_days(value: str | date, today: date) -> str:
    delta = (parse(value) - today).days
    if delta == 0:
        return "heute"
    if delta == 1:
        return "morgen"
    if delta < 0:
        return f"vor {-delta} Tagen"
    return f"in {delta} Tagen"


def hour_label(hour: str) -> str:
    return f"{hour}. Stunde" if hour else "Stunde"


def plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def join_de(items: list[str]) -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " und " + items[-1]
