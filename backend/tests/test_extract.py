from datetime import datetime, timezone

import pytest

from app.llm.cleanup import CLEANUP_TOOL, CleanupDecision, valid_decisions
from app.llm.extract import SAVE_TOOL, Extraction, apply_extraction, is_noise, is_sensitive
from app.models import Task


@pytest.mark.parametrize(
    "subject, sender",
    [
        ("Your OTP is ready", "noreply@bank.com"),
        ("Your verification code", "accounts@example.com"),
        ("Your pass code for login", None),
        ("Passcode for sign-in", None),
        ("Your login credentials", "it@university.edu"),
        ("Reset your PASSWORD", "security@example.com"),
        ("Funds Transfer Confirmation", "alerts@bank.com"),
        ("Transaction alert", "HBL Alerts <alerts@hbl.com>"),
    ],
)
def test_sensitive_emails_are_detected(subject, sender):
    assert is_sensitive(subject, sender)


@pytest.mark.parametrize(
    "subject, sender",
    [
        ("Homework 2 released", "CS204 Course <cs204@university.edu>"),
        ("Hotpot night on Friday", "clubs@university.edu"),  # "otp" inside a word
        ("Project sync", "Ayse Demir <ayse.demir@university.edu>"),
        (None, None),
    ],
)
def test_normal_emails_are_not_flagged(subject, sender):
    assert not is_sensitive(subject, sender)


@pytest.mark.parametrize(
    "sender",
    [
        "LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>",
        "LinkedIn <jobs-noreply@linkedin.com>",
        "LinkedIn Newsletters <newsletters-noreply@linkedin.com>",
        "LinkedIn News <editors-noreply@linkedin.com>",
        "Freelancer <noreply@notifications.freelancer.com>",
        "Freelancer.com <noreply@freelancer.com>",
        "Facebook <notification@priority.facebookmail.com>",
        "Coursera <no-reply@m.learn.coursera.org>",
    ],
)
def test_noise_senders_are_detected(sender):
    assert is_noise(sender)


@pytest.mark.parametrize(
    "sender",
    [
        "LinkedIn <security-noreply@linkedin.com>",  # security alerts can be actionable
        "LinkedIn <messages-noreply@linkedin.com>",
        "Facebook <security@facebookmail.com>",
        "Coursera <no-reply@coursera.org>",  # course deadlines
        "Someone <me@notfreelancer.com>",
        "CS204 Course <cs204@university.edu>",
        None,
    ],
)
def test_other_senders_are_not_noise(sender):
    assert not is_noise(sender)


# --- open-task context: duplicates and resolved tasks ---


def _task(id, title):
    return Task(id=id, title=title, status="open")


def _extraction(**kw):
    base = {"is_actionable": True, "tasks": [], "summary": "s"}
    return Extraction.model_validate(base | kw)


def test_tool_schemas_are_inline_and_new_fields_optional():
    schema = SAVE_TOOL["input_schema"]
    assert "$defs" not in str(schema) and "$ref" not in str(schema)
    assert "$defs" not in str(CLEANUP_TOOL["input_schema"])
    assert set(schema["required"]) == {"is_actionable", "tasks", "summary"}
    assert Extraction.model_validate({"is_actionable": False, "tasks": [], "summary": "s"}).resolved_task_ids == []


def test_resolved_ids_are_applied_and_unknown_ids_ignored():
    tasks = [_task(1, "Fix internship profile"), _task(2, "Submit HW2")]
    new, resolved, dups = apply_extraction(_extraction(is_actionable=False, resolved_task_ids=[1, 99]), tasks)
    assert [t.id for t in resolved] == [1]
    assert new == [] and dups == 0


def test_duplicates_are_counted_and_same_title_is_skipped():
    tasks = [_task(2, "Submit CS204 Homework 2")]
    due = datetime(2026, 3, 6, 20, 55, tzinfo=timezone.utc).isoformat()
    extraction = _extraction(
        duplicate_of_task_ids=[2, 2, 77],
        tasks=[
            {"title": "submit  cs204 homework 2", "due_at": due, "priority": "high"},  # Claude missed the repeat
            {"title": "Read chapter 5", "due_at": None, "priority": "low"},
        ],
    )
    new, resolved, dups = apply_extraction(extraction, tasks)
    assert [t.title for t in new] == ["Read chapter 5"]
    assert dups == 2 and resolved == []


def test_not_actionable_creates_no_tasks():
    extraction = _extraction(is_actionable=False, tasks=[{"title": "x", "due_at": None, "priority": "low"}])
    assert apply_extraction(extraction, [])[0] == []


def test_cleanup_drops_bad_decisions():
    d = lambda tid, action, of=None: CleanupDecision(task_id=tid, action=action, duplicate_of=of, reason="r")
    decisions = [
        d(1, "duplicate", 2),   # kept: 1 repeats 2
        d(2, "duplicate", 1),   # dropped: would remove both of the pair
        d(3, "resolved"),       # kept
        d(3, "resolved"),       # dropped: repeat
        d(99, "resolved"),      # dropped: unknown task
        d(4, "duplicate", 4),   # dropped: duplicate of itself
        d(5, "duplicate", 98),  # dropped: original unknown
    ]
    kept = valid_decisions(decisions, {1, 2, 3, 4, 5})
    assert [(k.task_id, k.action) for k in kept] == [(1, "duplicate"), (3, "resolved")]
