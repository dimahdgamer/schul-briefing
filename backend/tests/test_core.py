from __future__ import annotations

from datetime import date, timedelta
from zoneinfo import ZoneInfo

import pytest

from app import briefing, diff, ical, normalize
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
    item = {"date": day, "classHour": {"number": hour, "from": "08:00:00", "until": "08:45:00"}, "actualLesson": lesson}
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
    assert out[0]["start"] == "08:00"


def test_lesson_ids_stable_for_substitution():
    """Die ID hängt am ursprünglichen Fach, damit eine Vertretung als Änderung erkannt wird."""
    original = raw_lesson("2026-10-06", "3", "Mathematik")
    sub = raw_lesson("2026-10-06", "3", "Erdkunde", teacher="Pohl", originalLessons=[original["actualLesson"]])
    assert normalize.lessons([original])[0]["id"] == normalize.lessons([sub])[0]["id"]


def test_homework_due_date_uses_next_school_day(calendar):
    raw = [{"date": "2026-10-16", "subject": "Mathematik", "homework": "S. 84 Nr. 3"}]
    out = normalize.homework(raw, calendar.next_school_day)
    assert out[0]["due"] == "2026-11-02"  # Freitag vor den Herbstferien -> erster Schultag danach


def test_grade_decoding():
    assert normalize.decode_grade("0~2+") == ("2+", pytest.approx(1.7), 0)
    assert normalize.decode_grade("1~13") == ("13", 13.0, 1)
    assert normalize.decode_grade("3-") == ("3-", pytest.approx(3.3), 0)
    raw = {"subjects": [{"id": 1, "name": "Mathematik"}],
           "courses": [{"id": 9, "subjectId": 1, "gradingPreset": {"gradingSystem": 0},
                        "grades": [{"id": 1, "value": "0~2", "weight": 1, "date": "2026-09-10"},
                                   {"id": 2, "value": "0~4", "weight": 3, "date": "2026-09-20"}]}]}
    subjects = normalize.grades(raw)
    assert subjects[0]["subject"] == "Mathematik"
    assert subjects[0]["average"] == 3.5


def test_letters_unread_for_own_student():
    raw = [{"id": 5, "title": "Wandertag", "sentDate": "2026-10-01T09:00:00",
            "studentStatuses": [{"studentId": 1, "readTimestamp": "x"}, {"studentId": 2, "readTimestamp": None}]}]
    assert normalize.letters(raw, 1)[0]["unread"] is False
    assert normalize.letters(raw, 2)[0]["unread"] is True


def test_normalize_tolerates_garbage():
    assert normalize.lessons(None) == []
    assert normalize.lessons([None, {"foo": 1}]) == []
    assert normalize.grades([]) == []
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


def test_new_homework_and_exam_and_grade():
    hw_prev = [{"id": "a", "subject": "M", "text": "x", "due": "2026-10-06", "assigned": "2026-10-05"}]
    hw_curr = hw_prev + [{"id": "b", "subject": "Deutsch", "text": "Lesen", "due": "2026-10-06", "assigned": "2026-10-05"}]
    hw = diff.diff_homework(hw_prev, hw_curr, MONDAY)
    assert hw[0].title == "Neue Hausaufgabe in Deutsch bis morgen"

    ex_prev = [{"id": "1", "subject": "Mathematik", "date": "2026-10-08", "type": "Klassenarbeit", "comment": ""}]
    ex_curr = [{"id": "1", "subject": "Mathematik", "date": "2026-10-09", "type": "Klassenarbeit", "comment": ""}]
    moved = diff.diff_exams(ex_prev, ex_curr, MONDAY)
    assert moved[0].kind == "moved"

    gr_prev = [{"subject": "Mathematik", "average": 2.0, "grades": [{"id": "1", "value": "2"}]}]
    gr_curr = [{"subject": "Mathematik", "average": 1.5, "grades": [{"id": "1", "value": "2"}, {"id": "2", "value": "1", "type": "Test", "date": "2026-10-05"}]}]
    grades = diff.diff_grades(gr_prev, gr_curr, show_values=True)
    assert grades[0].title == "Neue Note in Mathematik"
    assert grades[0].body.startswith("1 · Test")


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
    raw[1]["classHour"] = {"number": "2", "from": "08:50:00", "until": "09:35:00"}
    lessons = normalize.lessons(raw)
    summary = briefing.day_summary(MONDAY, lessons, [], [], [], set(), calendar)
    assert summary["late_start"] and summary["start"] == "08:50"
    push = briefing.build_push("morning", summary, MONDAY, 1, 0)
    assert push["title"] == "Heute 08:50–09:35 Uhr · 1 Änderung"
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
    assert "DTSTART:20261006T060000Z" in body  # 08:00 MESZ = 06:00 UTC
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
