"""Schulfreie Tage: Schulferien und Feiertage (OpenHolidays API) plus
Ferientage aus dem Schulmanager-Kalender (z. B. bewegliche Ferientage)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import httpx

from .db import Database

log = logging.getLogger(__name__)

API = "https://openholidaysapi.org"
CACHE_KEY = "holidays_cache"


@dataclass(frozen=True)
class Period:
    start: date
    end: date  # inklusiv
    name: str
    kind: str  # "school" | "public" | "school-calendar"

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end


class SchoolCalendar:
    def __init__(self, db: Database, subdivision: str) -> None:
        self.db = db
        self.subdivision = subdivision
        self.country = subdivision.split("-")[0]
        self._periods: list[Period] = []
        self._calendar_periods: list[Period] = []
        self._load_from_cache()

    # ── Laden ────────────────────────────────────────────────────────

    def _load_from_cache(self) -> None:
        cached = self.db.get(CACHE_KEY) or {}
        self._periods = [
            Period(date.fromisoformat(p["start"]), date.fromisoformat(p["end"]), p["name"], p["kind"])
            for p in cached.get("periods", [])
        ]

    async def refresh(self, today: date, force: bool = False) -> None:
        cached = self.db.get(CACHE_KEY) or {}
        if not force and cached.get("fetched") == today.isoformat() and cached.get("periods"):
            return
        start = today - timedelta(days=60)
        end = today + timedelta(days=430)
        params = {
            "countryIsoCode": self.country,
            "subdivisionCode": self.subdivision,
            "languageIsoCode": "DE",
            "validFrom": start.isoformat(),
            "validTo": end.isoformat(),
        }
        periods: list[dict[str, Any]] = []
        try:
            async with httpx.AsyncClient(timeout=20) as http:
                for path, kind in (("/SchoolHolidays", "school"), ("/PublicHolidays", "public")):
                    resp = await http.get(API + path, params=params, headers={"Accept": "application/json"})
                    resp.raise_for_status()
                    for item in resp.json():
                        if kind == "public" and not _applies(item, self.subdivision):
                            continue
                        periods.append(
                            {
                                "start": item["startDate"],
                                "end": item["endDate"],
                                "name": _name(item),
                                "kind": kind,
                            }
                        )
        except Exception as exc:  # Netzwerkfehler: alten Cache weiterverwenden
            log.warning("Ferien konnten nicht geladen werden: %s", exc)
            return
        self.db.set(CACHE_KEY, {"fetched": today.isoformat(), "periods": periods})
        self._load_from_cache()

    def set_calendar_holidays(self, events: list[dict[str, Any]]) -> None:
        """Ferientage aus dem Schulmanager-Kalender ergänzen."""
        extra: list[Period] = []
        for event in events:
            if not event.get("is_holiday"):
                continue
            try:
                start = date.fromisoformat(event["start"][:10])
                end = date.fromisoformat((event.get("end") or event["start"])[:10])
            except (KeyError, ValueError):
                continue
            # Ganztägige Termine enden oft um 00:00 des Folgetags
            if event.get("all_day") and end > start and (event.get("end") or "")[11:16] in {"", "00:00"}:
                end -= timedelta(days=1)
            extra.append(Period(start, max(start, end), event.get("title") or "Schulfrei", "school-calendar"))
        self._calendar_periods = extra

    # ── Abfragen ─────────────────────────────────────────────────────

    @property
    def periods(self) -> list[Period]:
        return self._periods + self._calendar_periods

    def holiday(self, day: date) -> Period | None:
        matches = [p for p in self.periods if p.contains(day)]
        if not matches:
            return None
        # Ferien vor einzelnen Feiertagen bevorzugen, damit "Herbstferien" statt "Allerheiligen" erscheint
        matches.sort(key=lambda p: (p.kind != "school", (p.end - p.start).days * -1))
        return matches[0]

    def is_school_day(self, day: date) -> bool:
        return day.weekday() < 5 and self.holiday(day) is None

    def next_school_day(self, day: date, include_today: bool = False) -> date:
        current = day if include_today else day + timedelta(days=1)
        for _ in range(120):
            if self.is_school_day(current):
                return current
            current += timedelta(days=1)
        return current

    def current_break(self, day: date) -> dict[str, Any] | None:
        """Laufende Ferien inkl. angrenzender Wochenenden, für die Anzeige "frei bis …"."""
        period = self.holiday(day)
        if period is None and day.weekday() < 5:
            return None
        back_to_school = self.next_school_day(day)
        if period is None:
            return {"name": "Wochenende", "until": (back_to_school - timedelta(days=1)).isoformat(),
                    "back": back_to_school.isoformat(), "kind": "weekend"}
        return {"name": period.name, "until": (back_to_school - timedelta(days=1)).isoformat(),
                "back": back_to_school.isoformat(), "kind": period.kind}

    def upcoming(self, day: date, limit: int = 3) -> list[dict[str, Any]]:
        out = []
        for p in sorted(self.periods, key=lambda p: p.start):
            if p.end < day:
                continue
            if p.kind == "public" and p.start.weekday() >= 5:
                continue
            out.append({"name": p.name, "start": p.start.isoformat(), "end": p.end.isoformat(), "kind": p.kind})
            if len(out) >= limit:
                break
        return out


def _name(item: dict[str, Any]) -> str:
    names = item.get("name") or []
    for entry in names:
        if entry.get("language") == "DE":
            return entry.get("text", "Ferien")
    return names[0].get("text", "Ferien") if names else "Ferien"


def _applies(item: dict[str, Any], subdivision: str) -> bool:
    if item.get("nationwide"):
        return True
    return any(s.get("code") == subdivision for s in item.get("subdivisions") or [])
