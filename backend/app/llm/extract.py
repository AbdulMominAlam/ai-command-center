"""Turns emails into tasks with Claude (Milestone 4).

One Claude call per email. Claude must answer by calling the save_extraction
tool, so its reply is always JSON in the Extraction shape instead of free text.
Claude also sees the open tasks, so it can skip repeats and close finished ones.
"""

import re
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

import anthropic
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.llm.client import CreditTooLow, create_message
from app.llm.pricing import Usage, estimate_cost
from app.llm.redact import redact
from app.models import Item, LLMUsage, Task, User

TZ = ZoneInfo(settings.TIMEZONE)  # Europe/Istanbul

# Emails whose subject or sender matches any of these (case-insensitive regex)
# are never sent to Claude: one-time codes, credentials and bank alerts.
SENSITIVE_PATTERNS = [
    r"pass ?code",
    r"\botp\b",
    r"verification code",
    r"login credentials",
    r"password",
    r"\bhbl\b",
    r"transfer",
    r"nayapay",  # payment receipts with harmless-looking subjects
    r"\bealert\b",  # CDC eAlerts quote account and transaction numbers
    r"\baccount no\b",
]
SENSITIVE_RE = re.compile("|".join(SENSITIVE_PATTERNS), re.IGNORECASE)
SKIPPED_SUMMARY = "Skipped: sensitive email"


def is_sensitive(subject: str | None, sender: str | None) -> bool:
    return bool(SENSITIVE_RE.search(f"{subject or ''}\n{sender or ''}"))


# Senders that are never actionable (case-insensitive regex on the From header).
# Match exact addresses where a site also sends useful mail: LinkedIn's
# security-noreply@ and Coursera's course emails still go to Claude.
NOISE_SENDERS = [
    r"jobalerts-noreply@linkedin\.com",
    r"jobs-noreply@linkedin\.com",
    r"newsletters-noreply@linkedin\.com",
    r"editors-noreply@linkedin\.com",
    r"@(?:[\w-]+\.)*freelancer\.com\b",
    r"@priority\.facebookmail\.com\b",
    r"@m\.learn\.coursera\.org\b",
]
NOISE_RE = re.compile("|".join(NOISE_SENDERS), re.IGNORECASE)
NOISE_SUMMARY = "Skipped: noise"


def is_noise(sender: str | None) -> bool:
    return bool(NOISE_RE.search(sender or ""))


class ExtractedTask(BaseModel):
    title: str = Field(description="Imperative to-do under 8 words, e.g. 'Submit CS301 homework 3'.")
    due_at: datetime | None = Field(
        description="Deadline or start time as ISO 8601 with the UTC offset the email states "
        "(+05:00 for GMT+5, Z for UTC), or +03:00 when the email gives no zone. Null if none."
    )
    priority: Literal["high", "medium", "low"]

    @field_validator("due_at")
    @classmethod
    def to_istanbul(cls, v: datetime | None) -> datetime | None:
        """Claude copies the time and offset from the email; Python does the
        time zone math. A time without an offset is read as Istanbul time."""
        if v is None:
            return None
        if v.tzinfo is None:
            v = v.replace(tzinfo=TZ)
        return v.astimezone(TZ)


class Extraction(BaseModel):
    is_actionable: bool = Field(description="True if the email asks the reader to do something.")
    tasks: list[ExtractedTask] = Field(
        description="New tasks only. Empty when is_actionable is false or every action is already an open task."
    )
    summary: str = Field(description="One sentence describing the email.")
    duplicate_of_task_ids: list[int] = Field(
        default_factory=list,
        description="IDs of open tasks this email describes again; those are not repeated in tasks.",
    )
    resolved_task_ids: list[int] = Field(
        default_factory=list,
        description="IDs of open tasks this email shows are done or no longer needed.",
    )


def _inline_refs(schema: dict) -> dict:
    """Pydantic puts nested models under $defs and points to them with $ref.
    This copies each definition into place so the tool schema is self-contained."""
    defs = schema.pop("$defs", {})

    def resolve(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(defs[node["$ref"].split("/")[-1]])
            return {k: resolve(v) for k, v in node.items()}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    return resolve(schema)


SAVE_TOOL = {
    "name": "save_extraction",
    "description": "Save the tasks and summary extracted from one email.",
    "input_schema": _inline_refs(Extraction.model_json_schema()),
}

# Bump when you change SYSTEM_PROMPT or the save_extraction schema, so eval
# reports in evals/results/ say which prompt they measured.
PROMPT_VERSION = "v4"

SYSTEM_PROMPT = """You read one email at a time for a university student and extract the things they need to do. You also see the student's open tasks, so the task list stays free of repeats and finished work.

Call save_extraction exactly once:
- is_actionable: true if the email asks the student to do, submit, attend, reply to or pay for something. Newsletters, receipts, notifications and FYI messages are not actionable. These are not actionable either, even when the email has a button, a link or a date:
  - Confirmations of something the student already did: an account opened, a booking, order or subscription confirmed, an application or form received, a payment made. This includes notices that the student's own account changed (profile photo, name, email address, password or settings): the student made that change, so there is nothing to do, even if the email adds "if this wasn't you, ...". A confirmed meeting, interview, exam or presentation that is still to come is different: it is a task with its start time.
  - Routine security alerts: new sign-ins, login attempts, new devices. "If this wasn't you, ..." is not a request (see the security alert rule below).
  - Social-network notifications: new messages, likes, comments, mentions, follows, friend or connection requests.
  - Optional invitations and marketing: webinars, events, coaching or career sessions, courses, newsletters, offers and discounts. They are actionable only if the email shows the student already registered; then attending is a task with its start time.
  - Test-prep and admissions-test marketing: reminders about tests such as TMUA, SAT, GRE, GMAT or IELTS from universities, test providers or prep companies ("register now", "test dates are coming up", "practice papers"). Not actionable unless the email confirms the student already registered or booked a test date; then the test is a task with its start time.
- tasks: one entry per distinct new action, with a short imperative title under 8 words and no URLs. Leave it empty when the email is not actionable.
- duplicate_of_task_ids: if an action in the email is already an open task (a reminder, a resend, the same deadline), put that task's id here and leave it out of tasks.
- resolved_task_ids: ids of open tasks that this email shows are done or no longer needed, such as a submission confirmation, an accepted application or a cancelled meeting. Only use ids from the open task list.
- due_at: the deadline (or start time for a meeting or exam) as ISO 8601. Copy the clock time exactly as the email writes it, with the UTC offset of the zone the email states, and do not convert it to another zone: "4:15pm (GMT+5)" or "4:15pm PKT" is 16:15:00+05:00, "10:00 UTC" is 10:00:00Z, "9am CET" is 09:00:00+01:00. When the email gives no zone, use +03:00 (Europe/Istanbul). Resolve relative dates such as "tomorrow noon", "this Friday" or "next Friday" against the email's sent time in Europe/Istanbul, not against today's date. "Next Friday" means the first Friday after the sent date, since an early deadline is safer than a late one. If only a date is given, use 23:59 that day. Use null when there is no date.
- priority: high for graded work, exams and anything due within 3 days of the sent time; low for optional things; medium otherwise.
- Security alerts are actionable only if the email says the activity was blocked or looks suspicious and asks for a specific action (reset your password, secure your account, review this sign-in), and it was sent less than 3 days before Today. Then add one high-priority task for that action, with due_at null. Every other security alert is routine and not actionable.
- summary: one plain sentence about the email.
- Never put codes, passwords, account numbers or amounts of money in a title or summary, even if the email contains them.

Example 1 (assignment)
Today: Monday 2026-03-02 12:00 (Europe/Istanbul)
Open tasks: (none)
Sent: Monday 2026-03-02 10:15 (Europe/Istanbul)
From: CS204 Course <cs204@university.edu>
Subject: Homework 2 released
Body: Homework 2 on linked lists is now on the course page. Submit your code by this Friday 23:55.
save_extraction: {"is_actionable": true, "tasks": [{"title": "Submit CS204 Homework 2 (linked lists)", "due_at": "2026-03-06T23:55:00+03:00", "priority": "high"}], "summary": "CS204 released Homework 2 on linked lists, due Friday night.", "duplicate_of_task_ids": [], "resolved_task_ids": []}

Example 2 (meeting invite)
Today: Tuesday 2026-04-14 18:00 (Europe/Istanbul)
Open tasks: (none)
Sent: Tuesday 2026-04-14 17:40 (Europe/Istanbul)
From: Ayse Demir <ayse.demir@university.edu>
Subject: Project sync
Body: Can we meet tomorrow at 14:00 in FENS 1040 to go over the project plan? Please bring your draft.
save_extraction: {"is_actionable": true, "tasks": [{"title": "Meet Ayse about the project plan", "due_at": "2026-04-15T14:00:00+03:00", "priority": "medium"}, {"title": "Bring project plan draft to meeting", "due_at": "2026-04-15T14:00:00+03:00", "priority": "medium"}], "summary": "Ayse asked to meet tomorrow at 14:00 to review the project plan draft.", "duplicate_of_task_ids": [], "resolved_task_ids": []}

Example 3 (newsletter)
Today: Thursday 2026-05-07 12:00 (Europe/Istanbul)
Open tasks: (none)
Sent: Thursday 2026-05-07 09:00 (Europe/Istanbul)
From: Campus Life <news@university.edu>
Subject: This week on campus
Body: Spring festival photos are up, the library has new opening hours, and the cafeteria menu has changed.
save_extraction: {"is_actionable": false, "tasks": [], "summary": "Campus newsletter with festival photos, library hours and a new cafeteria menu.", "duplicate_of_task_ids": [], "resolved_task_ids": []}

Example 4 (duplicate)
Today: Wednesday 2026-03-04 12:00 (Europe/Istanbul)
Open tasks:
- [41] Submit CS204 Homework 2 (linked lists) (due Friday 2026-03-06 23:55)
Sent: Wednesday 2026-03-04 09:30 (Europe/Istanbul)
From: CS204 Course <cs204@university.edu>
Subject: Reminder: Homework 2
Body: A reminder that Homework 2 is due this Friday at 23:55. Office hours are Thursday 15:00.
save_extraction: {"is_actionable": true, "tasks": [], "summary": "Reminder that CS204 Homework 2 is due Friday night.", "duplicate_of_task_ids": [41], "resolved_task_ids": []}

Example 5 (resolved)
Today: Friday 2026-02-20 12:00 (Europe/Istanbul)
Open tasks:
- [57] Fix discrepancies in internship profile (no due date)
Sent: Friday 2026-02-20 11:05 (Europe/Istanbul)
From: Career Center <career@university.edu>
Subject: Internship profile accepted
Body: Your internship profile has been reviewed and accepted. No further action is needed.
save_extraction: {"is_actionable": false, "tasks": [], "summary": "The Career Center accepted the internship profile.", "duplicate_of_task_ids": [], "resolved_task_ids": [57]}

Example 6 (routine security alert)
Today: Monday 2026-06-15 10:00 (Europe/Istanbul)
Open tasks: (none)
Sent: Monday 2026-06-15 09:20 (Europe/Istanbul)
From: Example Shop <account-update@shop.example.com>
Subject: Sign-in attempt on your account
Body: Someone signed in to your account from a new device in Istanbul. If this was you, you don't need to do anything. If it wasn't, please change your password.
save_extraction: {"is_actionable": false, "tasks": [], "summary": "Example Shop reported a sign-in from a new device in Istanbul.", "duplicate_of_task_ids": [], "resolved_task_ids": []}

Example 7 (optional invitation)
Today: Tuesday 2026-09-08 12:00 (Europe/Istanbul)
Open tasks: (none)
Sent: Tuesday 2026-09-08 11:30 (Europe/Istanbul)
From: Career Network <events@careers.example.com>
Subject: You're invited: interview skills webinar
Body: Join our free webinar on interview skills this Thursday at 18:00. Register now to save your spot!
save_extraction: {"is_actionable": false, "tasks": [], "summary": "Invitation to an optional interview skills webinar on Thursday at 18:00.", "duplicate_of_task_ids": [], "resolved_task_ids": []}

Example 8 (time in another zone)
Today: Monday 2026-09-28 15:00 (Europe/Istanbul)
Open tasks: (none)
Sent: Monday 2026-09-28 14:10 (Europe/Istanbul)
From: Example Labs Hiring <hiring@labs.example.com>
Subject: Interview confirmed
Body: Your technical interview is confirmed for Thursday 1 October at 11:30 AM PKT (GMT+5) on Google Meet.
save_extraction: {"is_actionable": true, "tasks": [{"title": "Attend Example Labs technical interview", "due_at": "2026-10-01T11:30:00+05:00", "priority": "high"}], "summary": "Example Labs confirmed a technical interview on Thursday at 11:30 PKT.", "duplicate_of_task_ids": [], "resolved_task_ids": []}

Example 9 (own account change)
Today: Wednesday 2026-09-30 20:00 (Europe/Istanbul)
Open tasks: (none)
Sent: Wednesday 2026-09-30 19:45 (Europe/Istanbul)
From: Example Network <notifications@network.example.com>
Subject: Your profile photo was changed
Body: Hi, your profile photo was updated today. If you didn't make this change, please secure your account.
save_extraction: {"is_actionable": false, "tasks": [], "summary": "Example Network confirmed the profile photo was changed.", "duplicate_of_task_ids": [], "resolved_task_ids": []}"""

# How many open tasks to show Claude with each email.
OPEN_TASKS_IN_PROMPT = 30


class ExtractionFailed(Exception):
    """Claude answered but the answer was unusable. Carries the token counts so
    the call can still be logged."""

    def __init__(self, message: str, usage: Usage):
        super().__init__(message)
        self.usage = usage


def _fmt(dt: datetime) -> str:
    return dt.astimezone(TZ).strftime("%A %Y-%m-%d %H:%M")


def format_open_tasks(tasks: list[Task]) -> str:
    if not tasks:
        return "Open tasks: (none)"
    lines = [
        f"- [{t.id}] {t.title} ({'due ' + _fmt(t.due_at) if t.due_at else 'no due date'})"
        for t in tasks
    ]
    return "Open tasks:\n" + "\n".join(lines)


def _normalize(title: str) -> str:
    return " ".join(title.lower().split())


def open_tasks(db: Session, user: User, limit: int) -> list[Task]:
    """The user's most recently created open tasks."""
    return list(db.scalars(
        select(Task)
        .where(Task.user_id == user.id, Task.status == "open")
        .order_by(Task.created_at.desc(), Task.id.desc())
        .limit(limit)
    ))


CACHE = {"type": "ephemeral"}  # 5-minute prompt cache


def call_tool(system: str, user_content: str | list[dict], tool: dict, model: type[BaseModel],
              max_tokens: int, cache: bool = False) -> tuple[BaseModel, Usage]:
    """One Claude call that must answer with `tool`. Returns (parsed input, usage).

    cache=True marks the tool and system prompt for prompt caching (they come
    first in the prompt and never change). The cache only kicks in once the
    cached prefix reaches the model's minimum (4096 tokens on Haiku 4.5);
    below that the marker does nothing and costs nothing.
    Raises CreditTooLow when the account is out of credit."""
    msg = create_message(
        model=settings.EXTRACT_MODEL,
        max_tokens=max_tokens,
        system=[{"type": "text", "text": system, **({"cache_control": CACHE} if cache else {})}],
        tools=[tool],
        tool_choice={"type": "tool", "name": tool["name"]},
        messages=[{"role": "user", "content": user_content}],
    )
    usage = Usage.of(msg.usage)

    if msg.stop_reason == "max_tokens":
        raise ExtractionFailed("reply was cut off at max_tokens", usage)
    tool_use = next((b for b in msg.content if b.type == "tool_use"), None)
    if tool_use is None:
        raise ExtractionFailed(f"no tool call (stop_reason={msg.stop_reason})", usage)
    try:
        parsed = model.model_validate(tool_use.input)
    except ValidationError as e:
        raise ExtractionFailed(f"invalid tool input: {e}", usage) from e
    return parsed, usage


def extract(item: Item, today: datetime, tasks: list[Task]) -> tuple[Extraction, Usage]:
    """Runs one email through Claude, showing it the given open tasks.
    Card, CNIC, IBAN and phone numbers are masked first (app/llm/redact.py).
    Returns (extraction, usage).

    The message has two parts. "Today" and the open tasks stay the same for
    every email in a run until a task is added, so they are marked for the
    prompt cache together with the system prompt; only the email part is new."""
    sent = _fmt(item.occurred_at) if item.occurred_at else "unknown"
    context = f"Today: {_fmt(today)} (Europe/Istanbul)\n{format_open_tasks(tasks)}"
    email = (
        f"Sent: {sent} (Europe/Istanbul)\n"
        f"From: {item.sender or 'unknown'}\n"
        f"Subject: {redact(item.title)}\n"
        f"Body:\n{redact(item.body) or '(empty)'}"
    )
    content = [{"type": "text", "text": context, "cache_control": CACHE}, {"type": "text", "text": email}]
    return call_tool(SYSTEM_PROMPT, content, SAVE_TOOL, Extraction, max_tokens=600, cache=True)


def apply_extraction(extraction: Extraction, tasks: list[Task]) -> tuple[list[ExtractedTask], list[Task], int]:
    """Decides what an extraction changes, given the open tasks Claude saw.
    Returns (new tasks to create, open tasks to mark done, duplicates skipped).
    Ids Claude made up are ignored, and a new task whose title matches an open
    task exactly is skipped as a duplicate even if Claude missed it."""
    by_id = {t.id: t for t in tasks}
    resolved = [by_id[i] for i in dict.fromkeys(extraction.resolved_task_ids) if i in by_id]
    duplicates = len({i for i in extraction.duplicate_of_task_ids if i in by_id})

    new = []
    if extraction.is_actionable:
        existing = {_normalize(t.title) for t in tasks if t not in resolved}
        for t in extraction.tasks:
            if _normalize(t.title) in existing:
                duplicates += 1
            else:
                existing.add(_normalize(t.title))
                new.append(t)
    return new, resolved, duplicates


def process_unprocessed(db: Session, user: User, limit: int) -> dict:
    """Extracts tasks from up to `limit` unprocessed emails, oldest first.

    Every email ends up processed=true, either with its tasks saved or with
    process_error set, so a broken email is never retried forever. A bad API
    key or an empty credit balance stops the run instead (CreditTooLow), and
    the email it stopped on stays unprocessed for the next run.
    """
    items = db.scalars(
        select(Item)
        .where(Item.user_id == user.id, Item.type == "email", Item.processed.is_(False))
        .order_by(Item.occurred_at.asc().nulls_last(), Item.id)
        .limit(limit)
    ).all()

    stats = {"processed": 0, "skipped_sensitive": 0, "skipped_noise": 0, "failed": 0,
             "tasks_created": 0, "task_titles": [], "duplicates_skipped": 0,
             "tasks_resolved": 0, "resolved_titles": []}
    total = Usage()
    today = datetime.now(TZ)

    for item in items:
        # Filtered emails are marked processed without a Claude call.
        if is_sensitive(item.title, item.sender):
            skip_summary, skip_stat = SKIPPED_SUMMARY, "skipped_sensitive"
        elif is_noise(item.sender):
            skip_summary, skip_stat = NOISE_SUMMARY, "skipped_noise"
        else:
            skip_summary = None
        if skip_summary:
            item.summary = skip_summary
            item.process_error = None
            item.processed = True
            stats["processed"] += 1
            stats[skip_stat] += 1
            db.commit()
            continue

        # Re-read for every email so tasks from the previous email are included.
        tasks = open_tasks(db, user, OPEN_TASKS_IN_PROMPT)
        usage = None
        try:
            extraction, usage = extract(item, today, tasks)
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError, CreditTooLow):
            # A bad API key or no credit would fail every email the same way;
            # stop instead of marking the whole inbox as failed.
            raise
        except ExtractionFailed as e:
            usage = e.usage
            item.process_error = str(e)[:1000]
        except Exception as e:  # SDK already retried; record the error and move on
            item.process_error = f"{type(e).__name__}: {e}"[:1000]
        else:
            item.summary = extraction.summary
            item.process_error = None
            new, resolved, duplicates = apply_extraction(extraction, tasks)
            for t in new:
                db.add(Task(user_id=user.id, item_id=item.id, title=t.title,
                            due_at=t.due_at, priority=t.priority, created_by="extraction"))
                stats["task_titles"].append(t.title)
            for t in resolved:
                t.status = "done"
                stats["resolved_titles"].append(t.title)
            stats["tasks_created"] += len(new)
            stats["tasks_resolved"] += len(resolved)
            stats["duplicates_skipped"] += duplicates

        if usage:
            db.add(usage_row(usage, "extraction", settings.EXTRACT_MODEL, item_id=item.id))
            total = total.plus(usage)

        item.processed = True
        stats["processed"] += 1
        if item.process_error:
            stats["failed"] += 1
        db.commit()  # one email at a time, so an interrupted run keeps its progress

    stats.update(total._asdict())
    stats["estimated_cost_usd"] = estimate_cost(settings.EXTRACT_MODEL, *total)
    return stats


def usage_row(usage: Usage, purpose: str, model: str, item_id: int | None = None) -> LLMUsage:
    """An llm_usage row for one Claude call, with its cache token counts."""
    return LLMUsage(item_id=item_id, purpose=purpose, model=model,
                    input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                    cache_creation_input_tokens=usage.cache_creation_tokens,
                    cache_read_input_tokens=usage.cache_read_tokens)
