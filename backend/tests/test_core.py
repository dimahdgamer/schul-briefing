from __future__ import annotations

from datetime import date, timedelta
from zoneinfo import ZoneInfo

import pytest

from app import bell, briefing, diff, ical, normalize
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
