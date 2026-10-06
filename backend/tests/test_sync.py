from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.sync.calendar import parse_event_time
from types import SimpleNamespace

from app.sync.sucourse import (
    EXAM_KEYWORDS,
    is_exam,
    parse_ics,
    priority_for,
    recalculate_priorities,
    validate_url,
)

ISTANBUL = ZoneInfo("Europe/Istanbul")
SAMPLE = (Path(__file__).parent / "fixtures" / "sucourse_sample.ics").read_bytes()


def _by_uid(events: list[dict]) -> dict[str, dict]:
    return {e["external_id"]: e for e in events}


def test_parse_ics_reads_each_event_and_skips_ones_without_uid():
    events = parse_ics(SAMPLE)
    assert [e["external_id"] for e in events] == [
        "1001@sucourse.example.edu",
        "1002@sucourse.example.edu",
        "1003@sucourse.example.edu",
    ]


def test_parse_ics_fields():
    hw = _by_uid(parse_ics(SAMPLE))["1001@sucourse.example.edu"]
    assert hw["title"] == "Homework 2 is due"
    assert hw["due_at"] == datetime(2026, 10, 10, 20, 59, tzinfo=timezone.utc)
    assert hw["body"] == "Submit your solutions as a single PDF."
    assert hw["raw"] == {"course": "CS204"}


def test_parse_ics_missing_description_and_categories():
    proposal = _by_uid(parse_ics(SAMPLE))["1002@sucourse.example.edu"]
    assert proposal["body"] is None
    assert proposal["raw"] == {"course": None}


def test_date_only_dtstart_becomes_istanbul_midnight():
    proposal = _by_uid(parse_ics(SAMPLE))["1002@sucourse.example.edu"]
    assert proposal["due_at"] == datetime(2026, 10, 15, 0, 0, tzinfo=ISTANBUL)
    assert proposal["due_at"].utcoffset() == timedelta(hours=3)


def test_tzid_dtstart_keeps_its_timezone():
    quiz = _by_uid(parse_ics(SAMPLE))["1003@sucourse.example.edu"]
    assert quiz["due_at"] == datetime(2026, 10, 20, 6, 30, tzinfo=timezone.utc)
    assert quiz["raw"] == {"course": "MATH201"}


def test_long_description_is_truncated_to_1000_characters():
    ics = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\n"
        "UID:long@x\r\nSUMMARY:Essay\r\nDTSTART:20261010T120000Z\r\n"
        f"DESCRIPTION:{'a' * 1500}\r\n"
        "END:VEVENT\r\nEND:VCALENDAR\r\n"
    ).encode()
    [event] = parse_ics(ics)
    assert len(event["body"]) == 1000


@pytest.mark.parametrize(
    "due_in, expected",
    [
        (timedelta(hours=-5), "high"),  # overdue
        (timedelta(days=1), "high"),
        (timedelta(days=3), "high"),
        (timedelta(days=3, minutes=1), "medium"),
        (timedelta(days=10), "medium"),
    ],
)
def test_priority_for(due_in, expected):
    now = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
    assert priority_for("Homework 2 is due", now + due_in, now) == expected


@pytest.mark.parametrize("title", ["Quiz 1 opens", "Midterm Quiz OPENS ", "Lab 3 opens"])
def test_opens_events_are_low_priority_even_when_soon(title):
    now = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
    assert priority_for(title, now + timedelta(hours=2), now) == "low"


def test_closes_events_keep_the_deadline_rule():
    now = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
    assert priority_for("Quiz 1 closes", now + timedelta(days=1), now) == "high"


@pytest.mark.parametrize("title", [
    "CS 405 Mid", "CS201 Midterm Make up", "MATH201 Final", "Final Exam", "HIST191 Quiz 2 [Required! 5 points]",
    "quiz 3 closes", "Mid-term review session", "Finals week", "Weekly Quizzes", "EXAM: Chapter 4",
])
def test_exams_are_high_priority_even_when_far_away(title):
    now = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
    assert is_exam(title)
    assert priority_for(title, now + timedelta(days=30), now) == "high"


@pytest.mark.parametrize("title", [
    "Homework 2 is due", "Midnight snack", "Finalize project proposal", "Examine the dataset", "Quizlet set",
])
def test_words_that_only_contain_a_keyword_are_not_exams(title):
    now = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
    assert not is_exam(title)
    assert priority_for(title, now + timedelta(days=30), now) == "medium"


def test_exam_keywords_are_a_plain_list():
    assert {"Mid", "Midterm", "Final", "Exam", "Quiz"} <= set(EXAM_KEYWORDS)


def _sucourse_task(title, due_at, priority, set_by_user=False):
    return SimpleNamespace(title=title, due_at=due_at, priority=priority, priority_set_by_user=set_by_user)


class TasksDB:
    def __init__(self, tasks):
        self.tasks = tasks

    def scalars(self, stmt):
        return SimpleNamespace(all=lambda: self.tasks)


def test_recalculate_priorities_rescores_open_sucourse_tasks():
    now = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
    far = now + timedelta(days=20)
    tasks = [
        _sucourse_task("CS 405 Mid", far, "medium"),                       # exam: medium -> high
        _sucourse_task("Homework 3 is due", far, "medium"),                # unchanged
        _sucourse_task("Lab 2 is due", now + timedelta(days=1), "medium"),  # soon -> high
        _sucourse_task("Quiz 4 opens", far, "high"),                       # opens -> low
    ]

    assert recalculate_priorities(TasksDB(tasks), SimpleNamespace(id=1), now) == 3
    assert [t.priority for t in tasks] == ["high", "medium", "high", "low"]


def test_recalculate_priorities_keeps_priorities_you_set():
    now = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
    far = now + timedelta(days=20)
    tasks = [
        _sucourse_task("CS 405 Mid", far, "low", set_by_user=True),          # rule says high: kept low
        _sucourse_task("Quiz 4 opens", far, "high", set_by_user=True),       # rule says low: kept high
        _sucourse_task("Homework 3 is due", now + timedelta(days=1), "low", set_by_user=True),
        _sucourse_task("Quiz 5 opens", far, "high"),                         # not yours: back to low
    ]

    assert recalculate_priorities(TasksDB(tasks), SimpleNamespace(id=1), now) == 1
    assert [t.priority for t in tasks] == ["low", "high", "low", "low"]


def test_utc_dtstart_is_converted_to_istanbul_time():
    # Same line Moodle sends for the CS201 Midterm Make up: 03:40 UTC is 06:40 in Istanbul.
    ics = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\n"
        "UID:100001@x\r\nSUMMARY:CS201 Midterm Make up\r\nDTSTART:20261024T034000Z\r\n"
        "END:VEVENT\r\nEND:VCALENDAR\r\n"
    ).encode()
    [event] = parse_ics(ics)
    local = event["due_at"].astimezone(ISTANBUL)
    assert (local.hour, local.minute) == (6, 40)


def test_validate_url_accepts_https_and_trims():
    url = "https://sucourse.example.edu/calendar/export_execute.php?userid=1&authtoken=abc"
    assert validate_url(f"  {url}\n") == url


@pytest.mark.parametrize("url", ["http://sucourse.example.edu/x?authtoken=secret", "secret", ""])
def test_validate_url_rejects_without_echoing_the_url(url):
    with pytest.raises(ValueError) as exc:
        validate_url(url)
    assert "secret" not in str(exc.value)


def test_calendar_timed_event_keeps_its_offset():
    assert parse_event_time({"dateTime": "2026-10-10T14:00:00+03:00"}) == datetime(
        2026, 10, 10, 11, 0, tzinfo=timezone.utc
    )


def test_calendar_all_day_event_is_istanbul_midnight():
    start = parse_event_time({"date": "2026-10-10"})
    assert start == datetime(2026, 10, 10, 0, 0, tzinfo=ISTANBUL)
    assert start.utcoffset() == timedelta(hours=3)


def test_floating_dtstart_is_read_as_istanbul_time():
    ics = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\n"
        "UID:floating@x\r\nSUMMARY:Lab\r\nDTSTART:20261024T094000\r\n"
        "END:VEVENT\r\nEND:VCALENDAR\r\n"
    ).encode()
    [event] = parse_ics(ics)
    assert event["due_at"] == datetime(2026, 10, 24, 9, 40, tzinfo=ISTANBUL)
