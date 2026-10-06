"""Demo-Datenquelle im Format der echten API.

Wird mit DEMO_MODE=1 aktiviert. Jeder Abruf verändert mit einer gewissen
Wahrscheinlichkeit etwas (Entfall, neue Note, neuer Brief …), damit sich
Änderungserkennung und Push ohne echtes Konto ausprobieren lassen.
"""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from typing import Any, Callable

from .db import Database

# Fach, Kürzel, Lehrkraft, Raum
SUBJECTS = {
    "M": ("Mathematik", "M", "Kowalski", "A204"),
    "D": ("Deutsch", "D", "Hoffmann", "A112"),
    "E": ("Englisch", "E", "Yilmaz", "A115"),
    "BI": ("Biologie", "BI", "Schäfer", "N03"),
    "PH": ("Physik", "PH", "Lindner", "N12"),
    "GE": ("Geschichte", "GE", "Becker", "A208"),
    "SP": ("Sport", "SP", "Neumann", "Turnhalle 2"),
    "KU": ("Kunst", "KU", "Vogt", "K01"),
    "IF": ("Informatik", "IF", "Krüger", "C014"),
    "EK": ("Erdkunde", "EK", "Brandt", "A210"),
    "F": ("Französisch", "F", "Dubois", "A117"),
    "MU": ("Musik", "MU", "Wagner", "M02"),
}

WEEK = {
    0: ["M", "M", "D", "E", "BI", "GE"],
    1: ["E", "PH", "PH", "D", "SP", "SP", "IF"],
    2: ["F", "M", "GE", "EK", "D", "KU", "KU"],
    3: ["D", "E", "M", "BI", "F", "MU"],
    4: ["IF", "IF", "EK", "M", "E", "F"],
}

SUBSTITUTES = ["Lorenz", "Pohl", "Arslan", "Weber"]

HOMEWORK_TEXTS = {
    "M": ["S. 84 Nr. 3a–d und 5", "Arbeitsblatt Lineare Funktionen fertig machen", "Übungsaufgaben zur Klassenarbeit, S. 92"],
    "D": ["Inhaltsangabe zu Kapitel 4 schreiben", "Gedicht analysieren (Strophe 1–3)", "Lesen bis S. 120"],
    "E": ["Workbook p. 37, ex. 2 and 4", "Vocabulary Unit 3 lernen", "Write a short e-mail to a friend (120 words)"],
    "BI": ["Protokoll zum Versuch vervollständigen", "Zellaufbau: Skizze beschriften"],
    "PH": ["Aufgaben 1–4 zum Ohmschen Gesetz", "Versuchsauswertung als Diagramm"],
    "GE": ["Quelle M5 bearbeiten", "Zeitstrahl Weimarer Republik ergänzen"],
    "IF": ["Python-Programm zur Notenberechnung erweitern"],
    "EK": ["Klimadiagramm Kairo auswerten"],
    "F": ["Vokabeln Leçon 4", "Cahier S. 22 Nr. 1–3"],
}

LETTERS = [
    ("Wandertag der Jahrgangsstufe", "Schulleitung"),
    ("Elternsprechtag: Anmeldung geöffnet", "Sekretariat"),
    ("Information zur Busverbindung während der Bauarbeiten", "Schulleitung"),
    ("Einladung zur Klassenpflegschaftssitzung", "Klassenleitung"),
    ("Fotograf am Donnerstag", "Sekretariat"),
]

STATE_KEY = "demo_state"


class DemoSource:
    def __init__(self, db: Database, is_school_day: Callable[[date], bool] | None = None) -> None:
        self.db = db
        self.rng = random.Random()
        self.is_school_day = is_school_day or (lambda d: d.weekday() < 5)

    @property
    def user(self) -> dict[str, Any]:
        return {
            "id": 1,
            "firstname": "Demo",
            "lastname": "Schüler",
            "institutionId": 1,
            "associatedStudent": {"id": 4711, "firstname": "Demo", "lastname": "Schüler", "className": "9b"},
        }

    # ── Zustand ──────────────────────────────────────────────────────

    def _state(self, today: date) -> dict[str, Any]:
        state = self.db.get(STATE_KEY)
        if state:
            return state
        rng = random.Random(today.toordinal())
        state = {"tick": 0, "overrides": {}, "homework": [], "exams": [], "letters": [], "threads": [], "next_id": 1000}
        # Ein paar Änderungen für heute und morgen
        for offset in (0, 1, 3):
            d = today + timedelta(days=offset)
            if d.weekday() < 5:
                self._random_override(state, d, rng)
        # Hausaufgaben der letzten Tage
        for back in range(1, 8):
            d = today - timedelta(days=back)
            if d.weekday() < 5:
                for code in rng.sample(WEEK[d.weekday()], k=min(2, len(WEEK[d.weekday()]))):
                    self._add_homework(state, d, code, rng)
        # Klassenarbeiten
        for offset, code, kind in ((3, "M", "Klassenarbeit"), (9, "E", "Test"), (16, "D", "Klassenarbeit"), (24, "BI", "Test")):
            self._add_exam(state, today + timedelta(days=offset), code, kind)
        # Post
        for i, (title, sender) in enumerate(LETTERS[:3]):
            self._add_letter(state, title, sender, today - timedelta(days=i * 4 + 1), read=i > 0)
        state["threads"] = [
            {"id": 501, "threadId": 9001, "unreadCount": 1, "isArchived": False,
             "thread": {"id": 9001, "subject": "Material für Kunst", "senderString": "Vogt, Kunst",
                        "lastMessageTimestamp": f"{today.isoformat()}T07:12:00", "lastMessage": {"text": "Bitte bis Mittwoch Wasserfarben mitbringen."}}},
            {"id": 502, "threadId": 9002, "unreadCount": 0, "isArchived": False,
             "thread": {"id": 9002, "subject": "AG Robotik", "senderString": "Krüger",
                        "lastMessageTimestamp": f"{(today - timedelta(days=3)).isoformat()}T15:40:00", "lastMessage": {"text": "Treffen diese Woche im C014."}}},
        ]
        self.db.set(STATE_KEY, state)
        return state

    def _next_id(self, state: dict[str, Any]) -> int:
        state["next_id"] += 1
        return state["next_id"]

    def _random_override(self, state: dict[str, Any], d: date, rng: random.Random) -> None:
        plan = WEEK[d.weekday()]
        hour = rng.randint(1, len(plan))
        key = f"{d.isoformat()}#{hour}"
        kind = rng.choice(["cancelled", "cancelled", "substitution", "room", "eva"])
        if kind == "cancelled":
            state["overrides"][key] = {"type": "cancelled"}
        elif kind == "eva":
            state["overrides"][key] = {"type": "eva"}
        elif kind == "substitution":
            code = rng.choice([c for c in SUBJECTS if c != plan[hour - 1]])
            state["overrides"][key] = {"type": "substitution", "subject": code, "teacher": rng.choice(SUBSTITUTES)}
        else:
            state["overrides"][key] = {"type": "room", "room": rng.choice(["B112", "A001", "C105", "Aula"])}

    def _add_homework(self, state: dict[str, Any], d: date, code: str, rng: random.Random) -> None:
        texts = HOMEWORK_TEXTS.get(code)
        if not texts:
            return
        state["homework"].append({"date": d.isoformat(), "subject": SUBJECTS[code][0], "homework": rng.choice(texts),
                                  "teacher": {"lastname": SUBJECTS[code][2]}})

    def _add_exam(self, state: dict[str, Any], d: date, code: str, kind: str) -> None:
        while d.weekday() >= 5:
            d += timedelta(days=1)
        hour = str(WEEK[d.weekday()].index(code) + 1) if code in WEEK[d.weekday()] else "3"
        state["exams"].append({
            "id": self._next_id(state), "date": d.isoformat(),
            "subject": {"name": SUBJECTS[code][0], "abbreviation": code},
            "type": {"name": kind}, "comment": "",
            "classHour": {"number": hour},
        })

    def _add_letter(self, state: dict[str, Any], title: str, sender: str, d: date, read: bool) -> None:
        letter_id = self._next_id(state)
        state["letters"].append({
            "id": letter_id, "title": title, "senderName": sender, "sentDate": f"{d.isoformat()}T09:30:00",
            "studentStatuses": [{"id": letter_id * 10, "studentId": 4711,
                                 "readTimestamp": f"{d.isoformat()}T12:00:00" if read else None}],
        })

    def _mutate(self, state: dict[str, Any], today: date) -> None:
        rng = self.rng
        state["tick"] += 1
        if rng.random() > 0.6:
            return
        action = rng.choice(["override", "override", "restore", "homework", "letter", "message", "exam"])
        upcoming = [today + timedelta(days=i) for i in range(0, 8) if (today + timedelta(days=i)).weekday() < 5]
        if action == "override" and upcoming:
            self._random_override(state, rng.choice(upcoming[:3]), rng)
        elif action == "restore" and state["overrides"]:
            state["overrides"].pop(rng.choice(sorted(state["overrides"])))
        elif action == "homework":
            day = today if today.weekday() < 5 else today - timedelta(days=today.weekday() - 4)
            self._add_homework(state, day, rng.choice(WEEK[day.weekday()]), rng)
        elif action == "letter":
            title, sender = rng.choice(LETTERS)
            self._add_letter(state, title, sender, today, read=False)
        elif action == "message" and state["threads"]:
            thread = rng.choice(state["threads"])
            thread["unreadCount"] += 1
            thread["thread"]["lastMessageTimestamp"] = datetime.now().isoformat(timespec="seconds")
            thread["thread"]["lastMessage"]["text"] = rng.choice(
                ["Kurze Erinnerung an morgen.", "Danke für die Rückmeldung!", "Raum hat sich geändert."]
            )
        elif action == "exam":
            self._add_exam(state, today + timedelta(days=rng.randint(5, 20)), rng.choice(["PH", "GE", "F", "EK"]), "Test")

    # ── Rohdaten im API-Format ───────────────────────────────────────

    def fetch(self, today: date, lesson_start: date, lesson_end: date, mutate: bool = True) -> dict[str, Any]:
        state = self._state(today)
        if mutate:
            self._mutate(state, today)
            self.db.set(STATE_KEY, state)
        return {
            "lessons": self.lessons(state, lesson_start, lesson_end),
            "homework": state["homework"],
            "exams": state["exams"],
            "letters": state["letters"],
            "threads": state["threads"],
            "calendar": self._calendar(today),
            "absences": self._absences(today),
        }

    def _absences(self, today: date) -> dict[str, Any]:
        """Fehlzeiten im Format des Klassenbuchs: Statistik je Fach und Liste mit Entschuldigungsstatus."""
        def day(back: int) -> str:
            d = today - timedelta(days=back)
            while d.weekday() >= 5:
                d -= timedelta(days=1)
            return d.isoformat()

        return {
            "statistics": [
                {"subject": {"name": "Mathematik"}, "absentLessons": 4, "totalLessons": 80},
                {"subject": {"name": "Deutsch"}, "absentLessons": 3, "totalLessons": 70},
                {"subject": {"name": "Geschichte"}, "absentLessons": 1, "totalLessons": 50},
            ],
            "statistics_unexcused": [{"subject": {"name": "Mathematik"}, "absentLessons": 2, "totalLessons": 80}],
            "list": [
                {"date": day(3), "from": "07:55", "until": "09:25", "comment": None, "excused": False,
                 "sickNote": None, "exemptionRequest": None},
                {"date": day(12), "from": None, "until": None, "comment": None, "excused": False,
                 "sickNote": {"certificateType": "Medical"}, "exemptionRequest": None},
                {"date": day(20), "from": "09:45", "until": None, "comment": "Arzttermin", "excused": True,
                 "sickNote": None, "exemptionRequest": None},
                {"date": day(30), "from": None, "until": None, "comment": None, "excused": False,
                 "sickNote": None, "exemptionRequest": {"isInternal": False, "comment": "Familienfeier"}},
                {"date": day(33), "from": "10:30", "until": "11:15", "comment": None, "excused": False,
                 "sickNote": {"certificateType": None}, "exemptionRequest": None},
            ],
        }

    def lessons(self, state: dict[str, Any] | None, start: date, end: date) -> list[dict[str, Any]]:
        state = state or self.db.get(STATE_KEY) or {"overrides": {}}
        out = []
        d = start
        while d <= end:
            plan = WEEK.get(d.weekday())
            if plan and self.is_school_day(d):
                for index, code in enumerate(plan, start=1):
                    hour = str(index)
                    name, abbr, teacher, room = SUBJECTS[code]
                    regular = {"subject": {"name": name, "abbreviation": abbr}, "subjectLabel": f"{abbr} G1",
                               "teachers": [{"lastname": teacher, "abbreviation": teacher[:3].upper()}],
                               "room": {"name": room}}
                    item: dict[str, Any] = {
                        "date": d.isoformat(),
                        "type": "regularLesson",
                        "classHour": {"number": hour},
                        "actualLesson": regular,
                    }
                    override = state["overrides"].get(f"{d.isoformat()}#{hour}")
                    if override:
                        item["originalLessons"] = [regular]
                        item["type"] = "changedLesson"
                        item["isSubstitution"] = override["type"] != "cancelled"
                        if override["type"] == "cancelled":
                            item["actualLesson"] = None
                            item["isCancelled"] = True
                        elif override["type"] == "eva":
                            # So führt Schulmanager EVA: gleiches Fach und gleiche Lehrkraft, Raum "EVA"
                            item["comment"] = "Eigenverantwortliches Arbeiten"
                            item["actualLesson"] = dict(regular, room={"name": "EVA"},
                                                        comment="Eigenverantwortliches Arbeiten")
                        elif override["type"] == "substitution":
                            s_name, s_abbr, _t, s_room = SUBJECTS[override["subject"]]
                            item["actualLesson"] = {"subject": {"name": s_name, "abbreviation": s_abbr}, "subjectLabel": f"{s_abbr} G1",
                                                    "teachers": [{"lastname": override["teacher"]}], "room": {"name": s_room}}
                        else:
                            item["actualLesson"] = dict(regular, room={"name": override["room"]})
                    out.append(item)
            d += timedelta(days=1)
        return out

    def _calendar(self, today: date) -> dict[str, Any]:
        d = today + timedelta(days=(3 - today.weekday()) % 7 + 7)
        return {"nonRecurringEvents": [
            {"id": 77, "summary": "Wandertag", "start": f"{d.isoformat()}T00:00:00", "end": f"{(d + timedelta(days=1)).isoformat()}T00:00:00", "allDay": True, "categoryId": 3},
            {"id": 78, "summary": "Elternsprechtag", "start": f"{(today + timedelta(days=12)).isoformat()}T15:00:00", "end": f"{(today + timedelta(days=12)).isoformat()}T19:00:00", "allDay": False, "categoryId": 2, "location": "Aula"},
        ], "recurringEvents": []}
