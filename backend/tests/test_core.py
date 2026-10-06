from __future__ import annotations

from datetime import date, timedelta
from zoneinfo import ZoneInfo

import pytest

from app import bell, briefing, diff, ical, normalize, own
from app.db import Database
from app.holidays import Period, SchoolCalendar
from app.schulmanager import salted_hash

MONDAY = date(2026, 10, 5)


@pytest.fixture()
def calendar(tmp_path):
    cal = SchoolCalendar(Database(tmp_path / "t.db"), "DE-NW")
    cal._periods = [Period(date(2026, 10, 17), date(2026, 10, 31), "Herbstferien", "school")]
    return cal


def raw_lesson(day: str, hour: str, subject: str, teacher: str = "Kowalski", room: str = "A204", **extra):
    lesson = {"subject": {"name": subject, "abbreviation": subject[:2]}, "teachers": [{"lastname": teacher}],
              "room": {"name": room}}
    item = {"date": day, "classHour": {"number": hour}, "actualLesson": lesson}
    item.update(extra)
    return item


# ── Normalisierung ───────────────────────────────────────────────────

def test_lesson_states():
    regular = raw_lesson("2026-10-06", "1", "Mathematik")
    original = regular["actualLesson"]
    cancelled = raw_lesson("2026-10-06", "2", "Deutsch", actualLesson=None, isCancelled=True,
                           originalLessons=[{"subject": {"name": "Deutsch"}, "teachers": [{"lastname": "Hoffmann"}], "room": {"name": "A112"}}])
    substitution = raw_lesson("2026-10-06", "3", "Erdkunde", teacher="Pohl", originalLessons=[dict(original)])
    room = raw_lesson("2026-10-06", "4", "Mathematik", room="B112", originalLessons=[dict(original)])
    out = normalize.lessons([regular, cancelled, substitution, room])
    assert [l["state"] for l in out] == ["regular", "cancelled", "substitution", "room-change"]
    assert out[1]["subject"] == "Deutsch" and out[1]["teacher"] == "Hoffmann"
    assert out[2]["original_subject"] == "Mathematik"
    assert out[3]["original_room"] == "A204" and out[3]["room"] == "B112"
    assert (out[0]["start"], out[0]["end"]) == ("07:55", "08:40")  # aus dem Stundenraster


def test_lesson_ids_stable_for_substitution():
    """Die ID hängt am ursprünglichen Fach, damit eine Vertretung als Änderung erkannt wird."""
    original = raw_lesson("2026-10-06", "3", "Mathematik")
    sub = raw_lesson("2026-10-06", "3", "Erdkunde", teacher="Pohl", originalLessons=[original["actualLesson"]])
    assert normalize.lessons([original])[0]["id"] == normalize.lessons([sub])[0]["id"]


def test_homework_due_date_falls_back_to_next_school_day(calendar):
    raw = [{"date": "2026-10-16", "subject": "Mathematik", "homework": "S. 84 Nr. 3"}]
    out = normalize.homework(raw, [], calendar.next_school_day)
    assert out[0]["due"] == "2026-11-02"  # Freitag vor den Herbstferien -> erster Schultag danach
    assert out[0]["due_estimated"] is True


def test_homework_due_is_next_lesson_of_subject(calendar):
    """Echter Fall: Geschichte am Fr 02.10. aufgegeben, nächste Geschichtsstunde Di 06.10."""
    lessons = normalize.lessons([
        raw_lesson("2026-09-28", "1", "Geschichte"),
        raw_lesson("2026-10-02", "3", "Geschichte"),
        raw_lesson("2026-10-05", "1", "Mathematik"),
        raw_lesson("2026-10-06", "2", "Geschichte"),
        raw_lesson("2026-10-07", "2", "Geschichte", actualLesson=None, isCancelled=True,
                   originalLessons=[{"subject": {"name": "Geschichte"}}]),
    ])
    raw = [
        {"date": "2026-10-02", "subject": "Geschichte", "homework": "Am Handout weiterarbeiten"},
        {"date": "2026-10-06", "subject": "Geschichte", "homework": "Quelle lesen"},
        {"date": "2026-10-05", "subject": "Physik", "homework": "Nicht im Plan"},
    ]
    out = {h["text"]: h for h in normalize.homework(raw, lessons, calendar.next_school_day)}
    assert out["Am Handout weiterarbeiten"]["due"] == "2026-10-06"
    assert out["Am Handout weiterarbeiten"]["due_estimated"] is False
    # Die Stunde am 07.10. fällt aus, nach dem 06.10. ist keine Geschichtsstunde bekannt
    assert out["Quelle lesen"]["due"] == "2026-10-07" and out["Quelle lesen"]["due_estimated"] is True
    assert out["Nicht im Plan"]["due"] == "2026-10-06"


REAL_ROOM_CHANGE = {
    "date": "2026-10-05", "comment": None, "classHour": {"id": 74564, "number": "1"}, "type": "changedLesson",
    "actualLesson": {"room": {"id": 334951, "name": "M 6"},
                     "subject": {"id": 255872, "abbreviation": "EK", "name": "Erdkunde", "isPseudoSubject": False},
                     "teachers": [{"id": 557661, "abbreviation": "BÜH", "firstname": "Katharina", "lastname": "Bühnen"}],
                     "comment": None, "subjectLabel": "EK L1", "substitutionId": 48903492},
    "originalLessons": [{"room": {"id": 334942, "name": "N 13"},
                         "subject": {"id": 255872, "abbreviation": "EK", "name": "Erdkunde", "isPseudoSubject": False},
                         "teachers": [{"id": 557661, "abbreviation": "BÜH", "firstname": "Katharina", "lastname": "Bühnen"}],
                         "subjectLabel": "EK L1", "lessonId": 23057526}],
    "isSubstitution": True, "isNew": False,
}

REAL_REGULAR = {
    "date": "2026-10-05", "classHour": {"id": 74566, "number": "3"}, "type": "regularLesson",
    "actualLesson": {"room": {"id": 334945, "name": "N 16"},
                     "subject": {"id": 255899, "abbreviation": "M", "name": "Mathematik", "isPseudoSubject": False},
                     "teachers": [{"id": 557676, "abbreviation": "HER", "firstname": "Christoph", "lastname": "Herold"}],
                     "subjectLabel": "M  L2", "lessonId": 23057566},
}


def test_real_room_change_is_not_a_substitution():
    lesson = normalize.lessons([REAL_ROOM_CHANGE])[0]
    assert lesson["state"] == "room-change"
    assert (lesson["room"], lesson["original_room"]) == ("M 6", "N 13")
    assert lesson["subject"] == "Erdkunde" and lesson["teacher"] == "Bühnen"
    assert (lesson["start"], lesson["end"]) == ("07:55", "08:40")


def test_real_regular_lesson_uses_subject_name_and_bell_times():
    lesson = normalize.lessons([REAL_REGULAR])[0]
    assert lesson["subject"] == "Mathematik"
    assert lesson["course"] == "M L2"
    assert (lesson["start"], lesson["end"]) == ("09:45", "10:30")
    assert lesson["state"] == "regular"


def test_bell_schedule():
    assert bell.times_for("1") == ("07:55", "08:40")
    assert bell.times_for("3") == ("09:45", "10:30")
    assert bell.times_for("5/6") == ("11:35", "13:05")
    assert bell.times_for("7") == ("13:30", "14:15")
    assert bell.times_for("") == ("", "")


def test_letters_unread_for_own_student():
    raw = [{"id": 5, "title": "Wandertag", "sentDate": "2026-10-01T09:00:00",
            "studentStatuses": [{"studentId": 1, "readTimestamp": "x"}, {"studentId": 2, "readTimestamp": None}]}]
    assert normalize.letters(raw, 1)[0]["unread"] is False
    assert normalize.letters(raw, 2)[0]["unread"] is True


def test_normalize_tolerates_garbage():
    assert normalize.lessons(None) == []
    assert normalize.lessons([None, {"foo": 1}]) == []
    assert normalize.exams({"unexpected": True}) == []


# ── Änderungserkennung ───────────────────────────────────────────────

def test_first_sync_produces_no_changes():
    curr = normalize.lessons([raw_lesson("2026-10-06", "1", "Mathematik", isCancelled=True, actualLesson=None,
                                         originalLessons=[{"subject": {"name": "Mathematik"}}])])
    assert diff.diff_lessons(None, curr, MONDAY) == []


def test_cancellation_detected_and_grouped():
    prev = normalize.lessons([raw_lesson("2026-10-06", "1", "Mathematik"), raw_lesson("2026-10-06", "2", "Deutsch")])
    curr_raw = [
        raw_lesson("2026-10-06", "1", "Mathematik", actualLesson=None, isCancelled=True,
                   originalLessons=[{"subject": {"name": "Mathematik"}}]),
        raw_lesson("2026-10-06", "2", "Deutsch", actualLesson=None, isCancelled=True,
                   originalLessons=[{"subject": {"name": "Deutsch"}}]),
    ]
    changes = diff.diff_lessons(prev, normalize.lessons(curr_raw), MONDAY)
    assert len(changes) == 1
    assert changes[0].title == "Morgen: 2 Änderungen im Stundenplan"
    assert "Mathematik fällt aus" in changes[0].body
    assert changes[0].kind == "cancelled"


def test_unchanged_plan_is_quiet():
    lessons = normalize.lessons([raw_lesson("2026-10-06", "1", "Mathematik")])
    assert diff.diff_lessons(lessons, lessons, MONDAY) == []


def test_past_days_are_ignored():
    prev = normalize.lessons([raw_lesson("2026-10-02", "1", "Mathematik")])
    curr = normalize.lessons([raw_lesson("2026-10-02", "1", "Mathematik", room="X1")])
    assert diff.diff_lessons(prev, curr, MONDAY) == []


def test_restored_lesson():
    cancelled = normalize.lessons([raw_lesson("2026-10-05", "3", "Physik", actualLesson=None, isCancelled=True,
                                              originalLessons=[{"subject": {"name": "Physik"}}])])
    regular = normalize.lessons([raw_lesson("2026-10-05", "3", "Physik")])
    changes = diff.diff_lessons(cancelled, regular, MONDAY)
    assert changes[0].title == "Heute: Physik findet statt"


def test_new_homework_and_exam():
    hw_prev = [{"id": "a", "subject": "M", "text": "x", "due": "2026-10-06", "assigned": "2026-10-05"}]
    hw_curr = hw_prev + [{"id": "b", "subject": "Deutsch", "text": "Lesen", "due": "2026-10-06", "assigned": "2026-10-05"}]
    hw = diff.diff_homework(hw_prev, hw_curr, MONDAY)
    assert hw[0].title == "Neue Hausaufgabe in Deutsch bis morgen"

    ex_prev = [{"id": "1", "subject": "Mathematik", "date": "2026-10-08", "type": "Klassenarbeit", "comment": ""}]
    ex_curr = [{"id": "1", "subject": "Mathematik", "date": "2026-10-09", "type": "Klassenarbeit", "comment": ""}]
    moved = diff.diff_exams(ex_prev, ex_curr, MONDAY)
    assert moved[0].kind == "moved"


def test_room_change_message():
    prev = normalize.lessons([dict(REAL_ROOM_CHANGE, date="2026-10-06", type="regularLesson", isSubstitution=False,
                                   actualLesson=REAL_ROOM_CHANGE["originalLessons"][0], originalLessons=None)])
    curr = normalize.lessons([dict(REAL_ROOM_CHANGE, date="2026-10-06")])
    changes = diff.diff_lessons(prev, curr, MONDAY)
    assert changes[0].title == "Morgen: Raumänderung Erdkunde"
    assert changes[0].body == "1. Stunde: Erdkunde in Raum M 6 statt N 13"


def test_thread_unread_increase():
    prev = [{"id": "1", "subject": "AG", "sender": "Krüger", "unread": 0, "last_at": "a", "preview": "", "url": "u"}]
    curr = [{"id": "1", "subject": "AG", "sender": "Krüger", "unread": 1, "last_at": "b", "preview": "Hallo", "url": "u"}]
    assert diff.diff_threads(prev, curr)[0].title == "Neue Nachricht: AG"
    assert diff.diff_threads(curr, curr) == []


# ── Ferien & Briefing ────────────────────────────────────────────────

def test_school_days(calendar):
    assert calendar.is_school_day(MONDAY)
    assert not calendar.is_school_day(date(2026, 10, 10))  # Samstag
    assert not calendar.is_school_day(date(2026, 10, 20))  # Herbstferien
    assert calendar.next_school_day(date(2026, 10, 16)) == date(2026, 11, 2)
    brk = calendar.current_break(date(2026, 10, 20))
    assert brk["name"] == "Herbstferien" and brk["back"] == "2026-11-02"


def test_briefing_late_start(calendar):
    raw = [
        raw_lesson("2026-10-05", "1", "Mathematik", actualLesson=None, isCancelled=True,
                   originalLessons=[{"subject": {"name": "Mathematik"}}]),
        raw_lesson("2026-10-05", "2", "Deutsch"),
    ]
    lessons = normalize.lessons(raw)
    summary = briefing.day_summary(MONDAY, lessons, [], [], [], set(), calendar)
    assert summary["late_start"] and summary["start"] == "08:40" and summary["planned_start"] == "07:55"
    push = briefing.build_push("morning", summary, MONDAY, 1, 0)
    assert push["title"] == "Heute 08:40–09:25 Uhr · 1 Änderung"
    assert "Später los" in push["body"]
    assert "1 ungelesener Brief" in push["body"]


def test_pbkdf2_hash_shape():
    value = salted_hash("geheim", "salz")
    assert len(value) == 1024 and all(c in "0123456789abcdef" for c in value)


def test_ical_feed_is_valid_ascii_structure():
    lessons = normalize.lessons([raw_lesson("2026-10-06", "1", "Mathematik, Kurs; A")])
    body = ical.build(lessons, [{"id": "1", "subject": "Physik", "type": "Test", "date": "2026-10-08",
                                 "start": "", "end": "", "comment": ""}], [], ZoneInfo("Europe/Berlin"))
    assert body.startswith("BEGIN:VCALENDAR\r\n") and body.endswith("END:VCALENDAR\r\n")
    assert "SUMMARY:Mathematik\\, Kurs\\; A" in body
    assert "DTSTART:20261006T055500Z" in body  # 07:55 MESZ = 05:55 UTC
    assert "DTSTART;VALUE=DATE:20261008" in body
    assert all(len(line.encode()) <= 75 for line in body.split("\r\n"))


def test_settings_validation(tmp_path):
    db = Database(tmp_path / "s.db")
    s = db.update_settings({"briefing_time": "7:05", "poll_interval": 2, "reminder_days": [1, 3, 3, 99], "unknown": 1})
    assert s["briefing_time"] == "07:05"
    assert s["poll_interval"] == 10
    assert s["reminder_days"] == [3, 1]
    with pytest.raises(ValueError):
        db.update_settings({"evening_time": "25:00"})


def test_room_change_taken_back_is_reported():
    changed = normalize.lessons([dict(REAL_ROOM_CHANGE, date="2026-10-06")])
    back = normalize.lessons([dict(REAL_ROOM_CHANGE, date="2026-10-06", type="regularLesson", isSubstitution=False,
                                   actualLesson=REAL_ROOM_CHANGE["originalLessons"][0], originalLessons=None)])
    changes = diff.diff_lessons(changed, back, MONDAY)
    assert changes[0].title == "Morgen: Erdkunde wieder wie geplant"
    assert changes[0].body == "1. Stunde: Erdkunde wieder wie geplant in Raum N 13"


def test_ical_uids_are_plain_and_stable():
    lessons = normalize.lessons([raw_lesson("2026-10-06", "5", "Katholische Religionslehre"),
                                 dict(REAL_ROOM_CHANGE, date="2026-10-06")])
    body = ical.build(lessons, [], [], ZoneInfo("Europe/Berlin"))
    uids = [line[4:] for line in body.split("\r\n") if line.startswith("UID:")]
    assert len(uids) == 2 and all(" " not in u and "#" not in u for u in uids)
    assert uids == [line[4:] for line in ical.build(lessons, [], [], ZoneInfo("Europe/Berlin")).split("\r\n") if line.startswith("UID:")]
    assert "Raum statt N 13" in body
    assert "DTSTART:20261006T093500Z" in body  # 5. Stunde 11:35 MESZ


# ── EVA (Eigenverantwortliches Arbeiten) ─────────────────────────────

_EK = {"id": 255872, "abbreviation": "EK", "name": "Erdkunde", "isPseudoSubject": False}
_BUEH = [{"id": 557661, "abbreviation": "BÜH", "firstname": "Katharina", "lastname": "Bühnen"}]

# Echter Fall: Schulmanager führt EVA als Vertretung mit Raum "EVA" und gleicher Lehrkraft
REAL_EVA = {
    "date": "2026-10-07", "comment": "Eigenverantwortliches Arbeiten", "classHour": {"id": 74570, "number": "7"},
    "type": "changedLesson",
    "actualLesson": {"room": {"id": 335685, "name": "EVA"}, "subject": _EK, "teachers": _BUEH,
                     "comment": "Eigenverantwortliches Arbeiten", "subjectLabel": "EK L1", "substitutionId": 49057682},
    "originalLessons": [{"room": {"id": 334959, "name": "S 07"}, "subject": _EK, "teachers": _BUEH,
                         "subjectLabel": "EK L1", "lessonId": 23057530}],
    "isSubstitution": True, "isNew": False,
}


def raw_eva(day: str, hour: str, subject: str, teacher: str = "Kowalski"):
    original = {"subject": {"name": subject, "abbreviation": subject[:2]}, "teachers": [{"lastname": teacher}],
                "room": {"name": "A204"}}
    return {"date": day, "classHour": {"number": hour}, "type": "changedLesson",
            "comment": "Eigenverantwortliches Arbeiten", "isSubstitution": True,
            "actualLesson": dict(original, room={"name": "EVA"}, comment="Eigenverantwortliches Arbeiten"),
            "originalLessons": [original]}


def test_real_eva_is_eva_not_room_change():
    lesson = normalize.lessons([REAL_EVA])[0]
    assert lesson["state"] == "eva"
    assert lesson["subject"] == "Erdkunde" and lesson["teacher"] == "Bühnen"
    assert lesson["room"] == "" and lesson["original_room"] == "S 07"
    assert lesson["comment"] == ""  # der Standardtext steckt schon im Zustand
    assert (lesson["start"], lesson["end"]) == ("13:30", "14:15")


def test_eva_needs_more_than_a_first_name():
    original = {"subject": {"name": "Mathematik"}, "teachers": [{"lastname": "Kowalski"}], "room": {"name": "A204"}}
    lesson = raw_lesson("2026-10-06", "2", "Mathematik", room="B112", comment="Eva Schmidt vertritt",
                        originalLessons=[original])
    assert normalize.lessons([lesson])[0]["state"] == "room-change"
    other = raw_lesson("2026-10-06", "2", "Mathematik", room="B112", comment="EVA", originalLessons=[original])
    assert normalize.lessons([other])[0]["state"] == "eva"


def test_eva_hours_do_not_count_as_attendance(calendar):
    day = date(2026, 10, 7)
    lessons = normalize.lessons([raw_lesson("2026-10-07", "1", "Mathematik"), raw_lesson("2026-10-07", "2", "Deutsch"),
                                 REAL_EVA])
    summary = briefing.day_summary(day, lessons, [], [], [], set(), calendar)
    assert summary["end"] == "09:25" and summary["planned_end"] == "14:15" and summary["early_end"]
    assert summary["has_eva"] and not summary["all_cancelled"]
    push = briefing.build_push("morning", summary, MONDAY + timedelta(days=1), 0, 0)
    assert push["title"] == "Morgen 07:55–09:25 Uhr · 1 Änderung"
    assert "7. Std Erdkunde: EVA, noch keine Aufgaben eingestellt" in push["body"]
    assert "Früher Schluss um 09:25" in push["body"]
    assert "als EVA" not in push["body"]  # nur die EVA-Zeile, keine zweite Änderungszeile


def test_day_with_only_eva_needs_no_school(calendar):
    day = date(2026, 10, 7)
    summary = briefing.day_summary(day, normalize.lessons([REAL_EVA]), [], [], [], set(), calendar)
    assert summary["all_cancelled"] and summary["has_eva"] and summary["start"] == ""
    push = briefing.build_push("morning", summary, day, 0, 0)
    assert push["title"] == "Heute: Kein Unterricht vor Ort (EVA)"


def test_eva_tasks_are_only_from_the_last_lesson(calendar):
    """Geschichte: Fr 25.09., Di 29.09., Fr 02.10. regulär, Di 06.10. EVA. Fällig am EVA-Tag ist nur,
    was in der letzten Stunde (02.10.) oder am EVA-Tag selbst eingestellt wurde, nichts Älteres."""
    lessons = normalize.lessons([
        raw_lesson("2026-09-25", "3", "Geschichte"),
        raw_lesson("2026-09-29", "2", "Geschichte"),
        raw_lesson("2026-10-02", "3", "Geschichte"),
        raw_eva("2026-10-06", "2", "Geschichte"),
    ])
    raw = [
        {"date": "2026-09-25", "subject": "Geschichte", "homework": "vor zwei Wochen"},
        {"date": "2026-09-29", "subject": "Geschichte", "homework": "vor einer Woche"},
        {"date": "2026-10-02", "subject": "Geschichte", "homework": "letzte Stunde"},
        {"date": "2026-10-06", "subject": "Geschichte", "homework": "am EVA-Tag eingestellt"},
        {"date": "2026-10-06", "subject": "Physik", "homework": "anderes Fach"},
    ]
    homework = normalize.homework(raw, lessons, calendar.next_school_day)
    by_text = {h["text"]: h for h in homework}
    assert by_text["vor einer Woche"]["due"] == "2026-10-02" and not by_text["vor einer Woche"]["eva"]
    assert by_text["letzte Stunde"]["due"] == "2026-10-06" and by_text["letzte Stunde"]["eva"]
    assert by_text["am EVA-Tag eingestellt"]["due"] == "2026-10-06"
    assert by_text["am EVA-Tag eingestellt"]["eva"] and not by_text["am EVA-Tag eingestellt"]["due_estimated"]
    assert not by_text["anderes Fach"]["eva"]

    summary = briefing.day_summary(date(2026, 10, 6), lessons, homework, [], [], set(), calendar)
    (entry,) = summary["eva"]
    assert entry["subject"] == "Geschichte" and entry["hour"] == "2"
    assert {t["text"] for t in entry["tasks"]} == {"letzte Stunde", "am EVA-Tag eingestellt"}

    push = briefing.build_push("morning", summary, date(2026, 10, 6), 0, 0)
    assert "2. Std Geschichte: EVA, Aufgaben: letzte Stunde; am EVA-Tag eingestellt" in push["body"]
    assert "Hausaufgaben fällig" not in push["body"]  # nicht noch einmal als normale Hausaufgabe


def test_homework_on_day_with_regular_and_eva_hour_stays_normal(calendar):
    lessons = normalize.lessons([
        raw_lesson("2026-10-06", "1", "Mathematik"),
        raw_eva("2026-10-06", "7", "Mathematik"),
        raw_lesson("2026-10-08", "1", "Mathematik"),
    ])
    raw = [{"date": "2026-10-06", "subject": "Mathematik", "homework": "S. 5"}]
    (hw,) = normalize.homework(raw, lessons, calendar.next_school_day)
    assert hw["due"] == "2026-10-08" and not hw["eva"]


def test_eva_message_and_taken_back():
    regular = normalize.lessons([dict(REAL_EVA, type="regularLesson", isSubstitution=False, comment=None,
                                      actualLesson=REAL_EVA["originalLessons"][0], originalLessons=None)])
    eva = normalize.lessons([REAL_EVA])
    (change,) = diff.diff_lessons(regular, eva, MONDAY)
    assert change.title == "Übermorgen: EVA in Erdkunde"
    assert change.body == "7. Stunde: Erdkunde als EVA"
    assert diff.diff_lessons(eva, eva, MONDAY) == []
    (back,) = diff.diff_lessons(eva, regular, MONDAY)
    assert back.title == "Übermorgen: Erdkunde wieder wie geplant"


def test_cancelled_then_eva_is_not_reported_as_restored():
    cancelled = normalize.lessons([dict(REAL_EVA, type="cancelledLesson", isCancelled=True, actualLesson=None,
                                        comment=None)])
    (change,) = diff.diff_lessons(cancelled, normalize.lessons([REAL_EVA]), MONDAY)
    assert change.kind == "eva"


def test_new_eva_task_is_named_in_the_message():
    curr = [{"id": "a", "subject": "Geschichte", "text": "Quelle lesen", "due": "2026-10-06", "assigned": "2026-10-06",
             "eva": True}]
    (change,) = diff.diff_homework([], curr, date(2026, 10, 6))
    assert change.title == "Neue EVA-Aufgabe in Geschichte bis heute"


def test_ical_marks_eva():
    body = ical.build(normalize.lessons([REAL_EVA]), [], [], ZoneInfo("Europe/Berlin"))
    assert "SUMMARY:EVA: Erdkunde" in body
    assert "LOCATION" not in body


# ── Eigene Klausuren und Beurlaubungen ───────────────────────────────

TUESDAY = date(2026, 10, 6)


def day_plan(day: str = "2026-10-06", hours: int = 6):
    """Je Stunde ein Fach, mit Zeiten aus dem Stundenraster."""
    subjects = ["Mathematik", "Deutsch", "Geschichte", "Physik", "Erdkunde", "Musik"]
    return normalize.lessons([raw_lesson(day, str(h), subjects[h - 1]) for h in range(1, hours + 1)])


def exam_entry(**extra):
    base = {"id": "own-1", "subject": "Mathematik", "date": "2026-10-06", "start": "08:00", "end": "10:15",
            "type": "Klausur", "comment": ""}
    return own.exam_item(dict(base, **extra))


def leave_entry(**extra):
    base = {"id": "own-9", "from": "2026-10-06", "to": "2026-10-06", "hour_from": "", "hour_to": "", "reason": ""}
    return dict(base, **extra)


def test_exam_replaces_overlapping_lessons(calendar):
    exam = exam_entry()
    lessons = own.overlay(day_plan(hours=4), [exam], [])
    # 1., 2. und 3. Stunde liegen (teilweise) im Zeitfenster, nur die 4. bleibt
    assert [(l["state"], l["hour"]) for l in lessons] == [("exam", ""), ("regular", "4")]
    summary = briefing.day_summary(TUESDAY, lessons, [], [exam], [], set(), calendar)
    assert (summary["start"], summary["end"]) == ("08:00", "11:15")
    assert summary["changes"] == [] and not summary["late_start"]
    push = briefing.build_push("morning", summary, TUESDAY, 0, 0)
    assert push["title"] == "Heute 08:00–11:15 Uhr"
    assert "Klausur heute: Mathematik (08:00–10:15 Uhr)" in push["body"]


def test_exam_on_other_day_changes_nothing():
    lessons = day_plan(hours=3)
    assert own.overlay(lessons, [exam_entry(date="2026-10-07")], [])[:3] == lessons[:3]


def test_schulmanager_exams_do_not_hide_lessons():
    """Nur selbst eingetragene Klausuren legen sich über den Plan, Arbeiten aus Schulmanager nicht."""
    lessons = day_plan(hours=3)
    from_school = {"id": "7", "subject": "Mathematik", "date": "2026-10-06", "start": "08:00", "end": "10:15",
                   "hour": "1/2", "type": "Klausur", "comment": ""}
    assert own.overlay(lessons, [from_school], []) == lessons


def test_bell_hour_at():
    assert bell.hour_at("08:00") == "1"  # mitten in der 1. Stunde
    assert bell.hour_at("09:30") == "3"  # in der Pause: die folgende Stunde
    assert bell.hour_at("15:50") == "10"
    assert bell.hour_at("23:00") == "" and bell.hour_at("") == ""


def test_leave_by_hours(calendar):
    lessons = day_plan()
    lessons[3] = dict(lessons[3], state="cancelled")  # 4. Stunde fällt ohnehin aus
    leave = leave_entry(hour_from="3", hour_to="4", reason="Arzttermin")
    shown = own.overlay(lessons, [], [leave])
    assert [l["state"] for l in shown] == ["regular", "regular", "leave", "cancelled", "regular", "regular"]
    summary = briefing.day_summary(TUESDAY, shown, [], [], [], set(), calendar, [leave])
    assert summary["leaves"][0]["hours"] == "3.–4. Std" and not summary["full_leave"]
    assert summary["changes"] == [{**lessons[3]}]  # nur der Entfall, die Beurlaubung ist keine Planänderung
    push = briefing.build_push("morning", summary, TUESDAY, 0, 0)
    assert "Beurlaubt 3.–4. Std (Arzttermin)" in push["body"]


def test_full_day_leave(calendar):
    leave = leave_entry(to="2026-10-08", reason="Praktikum")
    lessons = own.overlay(day_plan("2026-10-07", 3), [], [leave])
    assert {l["state"] for l in lessons} == {"leave"}
    summary = briefing.day_summary(date(2026, 10, 7), lessons, [], [], [], set(), calendar, [leave])
    assert summary["full_leave"] and summary["all_cancelled"] and summary["start"] == ""
    push = briefing.build_push("morning", summary, TUESDAY, 0, 0)
    assert push["title"] == "Morgen: Beurlaubt"
    assert "Beurlaubt bis Do 08.10. (Praktikum)" in push["body"]


def test_full_day_leave_without_timetable(calendar):
    """Auch ohne Stundenplan-Daten für den Tag (weiter als vier Wochen voraus) ist die Beurlaubung da."""
    leave = leave_entry(**{"from": "2026-12-01", "to": "2026-12-01"})
    summary = briefing.day_summary(date(2026, 12, 1), [], [], [], [], set(), calendar, [leave])
    assert summary["full_leave"] and summary["lessons"] == []
    assert briefing.build_push("morning", summary, TUESDAY, 0, 0)["title"] == "Di 01.12.: Beurlaubt"


def test_exam_input_validation():
    ok = own.clean_exam({"subject": "  Mathematik ", "date": "2026-10-12", "start": "8:05", "end": "10:20"})
    assert ok == {"subject": "Mathematik", "date": "2026-10-12", "start": "08:05", "end": "10:20",
                  "type": "Klausur", "comment": ""}
    base = {"subject": "Mathematik", "date": "2026-10-12", "start": "08:00", "end": "10:00"}
    for bad in (dict(base, subject=""), dict(base, date="12.10."), dict(base, start="25:00"),
                dict(base, end="08:00"), dict(base, end="07:00"), dict(base, comment="x" * 201)):
        with pytest.raises(ValueError):
            own.clean_exam(bad)


def test_leave_input_validation():
    assert own.clean_leave({"from": "2026-10-12", "to": "2026-10-14"}) == {
        "from": "2026-10-12", "to": "2026-10-14", "hour_from": "", "hour_to": "", "reason": ""}
    single = own.clean_leave({"from": "2026-10-12", "hour_from": "3"})
    assert single["to"] == "2026-10-12" and single["hour_to"] == "3"
    for bad in (
        {"from": "2026-10-12", "to": "2026-10-14", "hour_from": "3"},  # Stunden nur an einem Tag
        {"from": "2026-10-12", "hour_from": "5", "hour_to": "3"},
        {"from": "2026-10-12", "hour_from": "12"},
        {"from": "2026-10-14", "to": "2026-10-12"},
        {"from": "2026-10-12", "to": "2027-10-12"},
        {"to": "2026-10-12"},
    ):
        with pytest.raises(ValueError):
            own.clean_leave(bad)


def test_ical_has_leave_and_exam_once():
    exam = exam_entry()
    leave = leave_entry(hour_from="5", hour_to="6", reason="Arzttermin")
    long_leave = leave_entry(id="own-8", to="2026-10-08")
    lessons = own.overlay(day_plan(), [exam], [leave])
    body = ical.build(lessons, [exam], [], ZoneInfo("Europe/Berlin"), True, [leave, long_leave])
    assert "SUMMARY:Klausur: Mathematik" in body and "DTSTART:20261006T060000Z" in body  # 08:00 MESZ
    assert "SUMMARY:Mathematik\r\n" not in body  # die Klausur steht nur einmal im Kalender
    assert "SUMMARY:Beurlaubt: Arzttermin" in body and "DTSTART:20261006T093500Z" in body  # 5. Std 11:35
    assert "SUMMARY:Erdkunde" not in body and "SUMMARY:Musik" not in body  # beurlaubte Stunden fehlen
    assert "DTSTART;VALUE=DATE:20261006" in body and "DTEND;VALUE=DATE:20261009" in body


# ── Eigener wiederkehrender Unterricht ───────────────────────────────

def russian(**extra):
    base = {"id": "own-c1", "subject": "Russisch", "weekdays": [3], "start": "15:00", "end": "17:15",
            "place": "Andere Schule", "from": "", "to": ""}
    return dict(base, **extra)


def test_bell_hour_span():
    assert bell.hour_span("15:00", "17:15") == "9–11"  # die 9. bis 11. Stunde
    assert bell.hour_span("07:55", "08:40") == "1"
    assert bell.hour_span("08:40", "10:30") == "2–3"
    assert bell.hour_span("20:00", "21:00") == "" and bell.hour_span("", "") == ""


def test_course_appears_on_its_weekday_only(calendar):
    out = own.course_lessons([russian()], date(2026, 10, 5), date(2026, 10, 16), calendar.in_official_holiday)
    assert [l["date"] for l in out] == ["2026-10-08", "2026-10-15"]  # beide Donnerstage
    lesson = out[0]
    assert (lesson["state"], lesson["subject"], lesson["hour"], lesson["room"]) == ("external", "Russisch", "9–11", "Andere Schule")
    assert (lesson["start"], lesson["end"]) == ("15:00", "17:15")


def test_course_follows_holidays_but_not_school_free_days(calendar):
    # Herbstferien 17.–31.10. fallen aus, ebenso ein Feiertag
    calendar._periods.append(Period(date(2026, 10, 15), date(2026, 10, 15), "Feiertag", "public"))
    # Ein freier Tag nur der eigenen Schule (aus dem Schulkalender) betrifft die andere Schule nicht
    calendar.set_calendar_holidays([{"is_holiday": True, "start": "2026-10-08T00:00:00", "end": "2026-10-09T00:00:00",
                                     "all_day": True, "title": "Studientag"}])
    assert not calendar.is_school_day(date(2026, 10, 8))  # für die eigene Schule ist frei
    out = own.course_lessons([russian()], date(2026, 10, 5), date(2026, 10, 30), calendar.in_official_holiday)
    assert [l["date"] for l in out] == ["2026-10-08"]  # 15.10. Feiertag, 22. und 29.10. Ferien


def test_course_validity_range_and_several_days(calendar):
    course = russian(weekdays=[1, 3], **{"from": "2026-10-07", "to": "2026-10-13"})
    out = own.course_lessons([course], date(2026, 10, 5), date(2026, 10, 16), calendar.in_official_holiday)
    assert [l["date"] for l in out] == ["2026-10-08", "2026-10-13"]  # Do 08.10. und Di 13.10.


def test_course_makes_the_school_day_end_later(calendar):
    lessons = day_plan("2026-10-08") + own.course_lessons([russian()], date(2026, 10, 8), date(2026, 10, 8), lambda d: False)
    summary = briefing.day_summary(date(2026, 10, 8), lessons, [], [], [], set(), calendar)
    assert summary["end"] == "17:15" and not summary["early_end"] and summary["changes"] == []
    push = briefing.build_push("morning", summary, date(2026, 10, 8), 0, 0)
    assert push["title"] == "Heute 07:55–17:15 Uhr"
    assert "Russisch 15:00–17:15 Uhr, Andere Schule" in push["body"]


def test_course_in_calendar_feed():
    lessons = own.course_lessons([russian()], date(2026, 10, 8), date(2026, 10, 8), lambda d: False)
    body = ical.build(lessons, [], [], ZoneInfo("Europe/Berlin"))
    assert "SUMMARY:Russisch" in body and "LOCATION:Andere Schule" in body
    assert "DTSTART:20261008T130000Z" in body and "DTEND:20261008T151500Z" in body  # 15:00–17:15 MESZ


def test_course_input_validation():
    ok = own.clean_course({"subject": " Russisch ", "weekdays": "3", "start": "15:00", "end": "17:15", "place": "Andere Schule"})
    assert ok == {"subject": "Russisch", "weekdays": [3], "start": "15:00", "end": "17:15",
                  "place": "Andere Schule", "from": "", "to": ""}
    assert own.clean_course({"subject": "X", "weekdays": [4, 1, 1], "start": "9:00", "end": "10:00"})["weekdays"] == [1, 4]
    base = {"subject": "Russisch", "weekdays": [3], "start": "15:00", "end": "17:15"}
    for bad in (dict(base, subject=""), dict(base, weekdays=[]), dict(base, weekdays="x"), dict(base, weekdays=[5]),
                dict(base, end="15:00"), dict(base, start="25:00"), dict(base, **{"from": "2026-10-10", "to": "2026-10-01"})):
        with pytest.raises(ValueError):
            own.clean_course(bad)


def test_course_label():
    assert own.course_label(russian()) == "Do · 15:00–17:15 · 9–11. Std"
    assert own.course_label(russian(weekdays=[0, 2], start="19:00", end="20:00")) == "Mo, Mi · 19:00–20:00"
