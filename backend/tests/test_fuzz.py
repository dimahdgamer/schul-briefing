"""Zufällige Stundenpläne durch die ganze Kette: keine Abstürze, und ein paar Regeln, die immer gelten müssen."""

from __future__ import annotations

import random
from datetime import date, timedelta
from zoneinfo import ZoneInfo

import pytest

from app import briefing, diff, ical, normalize, own
from app.db import Database
from app.holidays import Period, SchoolCalendar

SUBJECTS = ["Mathematik", "Deutsch", "Musik", "Erdkunde", "Physik", "Geschichte"]
TEACHERS = ["Kowalski", "Antwerpen", "Herold"]
ROOMS = ["A204", "M 21", "N 16", "EVA"]
START = date(2026, 10, 5)


def random_raw_lesson(rng: random.Random, day: date, hour: int):
    subject = rng.choice(SUBJECTS)
    teacher, room = rng.choice(TEACHERS), rng.choice(ROOMS[:3])
    original = {"subject": {"name": subject, "abbreviation": subject[:2]}, "teachers": [{"lastname": teacher}],
                "room": {"name": room}, "subjectLabel": f"{subject[:2]} G1"}
    item = {"date": day.isoformat(), "classHour": {"number": str(hour)}, "type": "regularLesson",
            "actualLesson": dict(original)}
    kind = rng.choice(["regular"] * 5 + ["cancelled", "substitution", "room", "eva", "extra"])
    if kind == "cancelled":
        item.update(actualLesson=None, isCancelled=True, type="cancelledLesson", originalLessons=[original])
    elif kind == "substitution":
        item.update(type="changedLesson", isSubstitution=True, originalLessons=[original],
                    actualLesson=dict(original, teachers=[{"lastname": rng.choice(TEACHERS)}],
                                      subject={"name": rng.choice(SUBJECTS)}))
    elif kind == "room":
        item.update(type="changedLesson", isSubstitution=True, originalLessons=[original],
                    actualLesson=dict(original, room={"name": "B112"}))
    elif kind == "eva":
        item.update(type="changedLesson", isSubstitution=True, comment="Eigenverantwortliches Arbeiten",
                    originalLessons=[original], actualLesson=dict(original, room={"name": "EVA"}))
    elif kind == "extra":
        item.update(isNew=True, type="additionalLesson")
    return item


def random_week(rng: random.Random):
    raws = []
    for offset in range(5):
        day = START + timedelta(days=offset)
        hours = sorted(rng.sample(range(1, 12), rng.randint(0, 9)))
        raws += [random_raw_lesson(rng, day, h) for h in hours]
    return normalize.lessons(raws)


def random_own(rng: random.Random):
    exams = []
    for i in range(rng.randint(0, 3)):
        start_min = rng.randint(7 * 60, 15 * 60)
        end_min = start_min + rng.randint(30, 180)
        exams.append(own.exam_item({
            "id": f"own-e{i}", "subject": rng.choice(SUBJECTS), "type": "Klausur", "comment": "",
            "date": (START + timedelta(days=rng.randint(0, 4))).isoformat(),
            "start": f"{start_min // 60:02d}:{start_min % 60:02d}", "end": f"{end_min // 60:02d}:{end_min % 60:02d}"}))
    leaves = []
    for i in range(rng.randint(0, 2)):
        first = START + timedelta(days=rng.randint(0, 4))
        if rng.random() < 0.5:
            leaves.append({"id": f"own-l{i}", "from": first.isoformat(), "to": first.isoformat(),
                           "hour_from": str(rng.randint(1, 6)), "hour_to": str(rng.randint(6, 9)), "reason": ""})
        else:
            leaves.append({"id": f"own-l{i}", "from": first.isoformat(),
                           "to": (first + timedelta(days=rng.randint(0, 2))).isoformat(),
                           "hour_from": "", "hour_to": "", "reason": "Praktikum"})
    courses = [{"id": f"own-c{i}", "subject": "Russisch", "weekdays": sorted(rng.sample(range(5), rng.randint(1, 3))),
                "start": "15:00", "end": "17:15", "place": "Andere Schule", "from": "", "to": ""}
               for i in range(rng.randint(0, 2))]
    return exams, leaves, courses


def pipeline(lessons, exams, leaves, courses, is_off):
    shown = own.overlay(lessons, exams, leaves)
    if courses:
        extra = own.course_lessons(courses, START, START + timedelta(days=6), is_off)
        shown = shown + own.mark_leave(extra, leaves)
        shown.sort(key=lambda l: (l["date"], l.get("start") or "99:99"))
    return normalize.merge_double_lessons(shown)


@pytest.fixture()
def calendar(tmp_path):
    cal = SchoolCalendar(Database(tmp_path / "t.db"), "DE-NW")
    cal._periods = [Period(date(2026, 10, 7), date(2026, 10, 7), "Feiertag", "public")]
    return cal


@pytest.mark.parametrize("seed", range(40))
def test_random_weeks_never_break_the_pipeline(seed, calendar):
    rng = random.Random(seed)
    for _ in range(25):
        lessons = random_week(rng)
        exams, leaves, courses = random_own(rng)
        shown = pipeline(lessons, exams, leaves, courses, calendar.in_official_holiday)

        # Regeln, die immer gelten
        assert [(l["date"], l.get("start") or "99:99") for l in shown] == sorted(
            (l["date"], l.get("start") or "99:99") for l in shown)  # nach Beginn sortiert
        school = [l for l in shown if l["state"] != "exam" and not l["id"].startswith("course:")]
        total = sum(l.get("lesson_count", 1) for l in school)
        in_klausur = 0
        for exam in exams:
            in_klausur += sum(1 for l in lessons if l["date"] == exam["date"] and l["start"] and l["end"]
                              and l["start"] < exam["end"] and l["end"] > exam["start"])
        assert total <= len(lessons) and total >= len(lessons) - in_klausur  # keine Stunde geht verloren oder kommt dazu
        for lesson in shown:
            if lesson.get("lesson_count", 1) > 1:
                first, last = lesson["hour"].split("–")
                assert int(last) - int(first) + 1 == lesson["lesson_count"]  # "3–4" sind genau zwei Stunden
                assert lesson["start"] < lesson["end"]
                assert lesson["state"] not in ("exam", "external") and not lesson["id"].startswith("course:")

        for offset in range(5):
            day = START + timedelta(days=offset)
            summary = briefing.day_summary(day, shown, [], [e for e in exams], [], set(), calendar, leaves)
            push = briefing.build_push("morning", summary, START, 0, 0)
            assert push["title"] and isinstance(push["body"], str)
            assert (summary["start"] <= summary["end"]) or not summary["start"]
            # Wer nicht hin muss, hat weder Beginn noch Schluss, und umgekehrt
            attended = [l for l in summary["lessons"] if l["state"] not in ("cancelled", "eva", "leave")]
            assert bool(attended) == bool(summary["start"])
        body = ical.build(shown, exams, [], ZoneInfo("Europe/Berlin"), True, leaves)
        assert body.startswith("BEGIN:VCALENDAR") and body.endswith("END:VCALENDAR\r\n")
        assert all(len(line.encode()) <= 75 for line in body.split("\r\n"))


@pytest.mark.parametrize("seed", range(10))
def test_random_changes_never_break_the_diff(seed):
    rng = random.Random(1000 + seed)
    for _ in range(25):
        before, after = random_week(rng), random_week(rng)
        today = START + timedelta(days=rng.randint(0, 4))
        for change in diff.diff_lessons(before, after, today):
            assert change.title and change.body
        assert diff.diff_lessons(before, before, today) == []  # unverändert bleibt still
