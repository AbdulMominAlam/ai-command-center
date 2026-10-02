"""Turns emails into tasks with Claude (Milestone 4).

One Claude call per email. Claude must answer by calling the save_extraction
tool, so its reply is always JSON in the Extraction shape instead of free text.
"""

import re
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

import anthropic
from pydantic import AwareDatetime, BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.llm.client import client
from app.models import Item, LLMUsage, Task, User

TZ = ZoneInfo(settings.TIMEZONE)  # Europe/Istanbul

# Prices for the cost estimate, in USD per million tokens.
INPUT_PRICE_PER_M = 1.0
OUTPUT_PRICE_PER_M = 5.0

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
]
SENSITIVE_RE = re.compile("|".join(SENSITIVE_PATTERNS), re.IGNORECASE)
SKIPPED_SUMMARY = "Skipped: sensitive email"


def is_sensitive(subject: str | None, sender: str | None) -> bool:
    return bool(SENSITIVE_RE.search(f"{subject or ''}\n{sender or ''}"))


class ExtractedTask(BaseModel):
    title: str = Field(description="Imperative to-do under 8 words, e.g. 'Submit CS301 homework 3'.")
    due_at: AwareDatetime | None = Field(
        description="Deadline or start time as ISO 8601 with a UTC offset, or null if none."
    )
    priority: Literal["high", "medium", "low"]


class Extraction(BaseModel):
    is_actionable: bool = Field(description="True if the email asks the reader to do something.")
    tasks: list[ExtractedTask] = Field(description="Empty when is_actionable is false.")
    summary: str = Field(description="One sentence describing the email.")


def _inline_refs(schema: dict) -> dict:
    """Pydantic puts ExtractedTask under $defs and points to it with $ref.
    This copies the definition into place so the tool schema is self-contained."""
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

SYSTEM_PROMPT = """You read one email at a time for a university student and extract the things they need to do.

Call save_extraction exactly once:
- is_actionable: true if the email asks the student to do, submit, attend, reply to or pay for something. Newsletters, receipts, notifications and FYI messages are not actionable.
- tasks: one entry per distinct action, with a short imperative title under 8 words and no URLs. Leave it empty when the email is not actionable.
- due_at: the deadline (or start time for a meeting or exam) as ISO 8601 with the +03:00 offset. Resolve relative dates such as "tomorrow noon", "this Friday" or "next Friday" against the email's sent time in Europe/Istanbul, not against today's date. "Next Friday" means the first Friday after the sent date, since an early deadline is safer than a late one. If only a date is given, use 23:59 that day. Use null when there is no date.
- priority: high for graded work, exams and anything due within 3 days of the sent time; low for optional things; medium otherwise.
- summary: one plain sentence about the email.
- Never put codes, passwords, account numbers or amounts of money in a title or summary, even if the email contains them.

Example 1 (assignment)
Sent: Monday 2026-03-02 10:15 (Europe/Istanbul)
From: CS204 Course <cs204@university.edu>
Subject: Homework 2 released
Body: Homework 2 on linked lists is now on the course page. Submit your code by this Friday 23:55.
save_extraction: {"is_actionable": true, "tasks": [{"title": "Submit CS204 Homework 2 (linked lists)", "due_at": "2026-03-06T23:55:00+03:00", "priority": "high"}], "summary": "CS204 released Homework 2 on linked lists, due Friday night."}

Example 2 (meeting invite)
Sent: Tuesday 2026-04-14 17:40 (Europe/Istanbul)
From: Ayse Demir <ayse.demir@university.edu>
Subject: Project sync
Body: Can we meet tomorrow at 14:00 in FENS 1040 to go over the project plan? Please bring your draft.
save_extraction: {"is_actionable": true, "tasks": [{"title": "Meet Ayse about the project plan", "due_at": "2026-04-15T14:00:00+03:00", "priority": "medium"}, {"title": "Bring project plan draft to meeting", "due_at": "2026-04-15T14:00:00+03:00", "priority": "medium"}], "summary": "Ayse asked to meet tomorrow at 14:00 to review the project plan draft."}

Example 3 (newsletter)
Sent: Thursday 2026-05-07 09:00 (Europe/Istanbul)
From: Campus Life <news@university.edu>
Subject: This week on campus
Body: Spring festival photos are up, the library has new opening hours, and the cafeteria menu has changed.
save_extraction: {"is_actionable": false, "tasks": [], "summary": "Campus newsletter with festival photos, library hours and a new cafeteria menu."}"""


class ExtractionFailed(Exception):
    """Claude answered but the answer was unusable. Carries the token counts so
    the call can still be logged."""

    def __init__(self, message: str, input_tokens: int, output_tokens: int):
        super().__init__(message)
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


def _fmt(dt: datetime) -> str:
    return dt.astimezone(TZ).strftime("%A %Y-%m-%d %H:%M")


def extract(item: Item, today: datetime) -> tuple[Extraction, int, int]:
    """Runs one email through Claude. Returns (extraction, input_tokens, output_tokens)."""
    sent = _fmt(item.occurred_at) if item.occurred_at else "unknown"
    user_message = (
        f"Today: {_fmt(today)} (Europe/Istanbul)\n\n"
        f"Sent: {sent} (Europe/Istanbul)\n"
        f"From: {item.sender or 'unknown'}\n"
        f"Subject: {item.title}\n"
        f"Body:\n{item.body or '(empty)'}"
    )
    msg = client.messages.create(
        model=settings.EXTRACT_MODEL,
        max_tokens=600,
        system=SYSTEM_PROMPT,
        tools=[SAVE_TOOL],
        tool_choice={"type": "tool", "name": "save_extraction"},
        messages=[{"role": "user", "content": user_message}],
    )
    tokens = (msg.usage.input_tokens, msg.usage.output_tokens)

    if msg.stop_reason == "max_tokens":
        raise ExtractionFailed("reply was cut off at max_tokens", *tokens)
    tool_use = next((b for b in msg.content if b.type == "tool_use"), None)
    if tool_use is None:
        raise ExtractionFailed(f"no tool call (stop_reason={msg.stop_reason})", *tokens)
    try:
        extraction = Extraction.model_validate(tool_use.input)
    except ValidationError as e:
        raise ExtractionFailed(f"invalid tool input: {e}", *tokens) from e
    return extraction, *tokens


def process_unprocessed(db: Session, user: User, limit: int) -> dict:
    """Extracts tasks from up to `limit` unprocessed emails, oldest first.

    Every email ends up processed=true, either with its tasks saved or with
    process_error set, so a broken email is never retried forever.
    """
    items = db.scalars(
        select(Item)
        .where(Item.user_id == user.id, Item.type == "email", Item.processed.is_(False))
        .order_by(Item.occurred_at.asc().nulls_last(), Item.id)
        .limit(limit)
    ).all()

    stats = {"processed": 0, "skipped_sensitive": 0, "failed": 0, "tasks_created": 0, "task_titles": [],
             "input_tokens": 0, "output_tokens": 0}
    today = datetime.now(TZ)

    for item in items:
        if is_sensitive(item.title, item.sender):
            item.summary = SKIPPED_SUMMARY
            item.process_error = None
            item.processed = True
            stats["processed"] += 1
            stats["skipped_sensitive"] += 1
            db.commit()
            continue

        tokens = None
        try:
            extraction, *tokens = extract(item, today)
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
            # A bad API key would fail every email the same way; stop instead of
            # marking the whole inbox as failed.
            raise
        except ExtractionFailed as e:
            tokens = [e.input_tokens, e.output_tokens]
            item.process_error = str(e)[:1000]
        except Exception as e:  # SDK already retried; record the error and move on
            item.process_error = f"{type(e).__name__}: {e}"[:1000]
        else:
            item.summary = extraction.summary
            item.process_error = None
            if extraction.is_actionable:
                for t in extraction.tasks:
                    db.add(Task(user_id=user.id, item_id=item.id, title=t.title,
                                due_at=t.due_at, priority=t.priority, created_by="extraction"))
                    stats["task_titles"].append(t.title)
                stats["tasks_created"] += len(extraction.tasks)

        if tokens:
            db.add(LLMUsage(item_id=item.id, purpose="extraction", model=settings.EXTRACT_MODEL,
                            input_tokens=tokens[0], output_tokens=tokens[1]))
            stats["input_tokens"] += tokens[0]
            stats["output_tokens"] += tokens[1]

        item.processed = True
        stats["processed"] += 1
        if item.process_error:
            stats["failed"] += 1
        db.commit()  # one email at a time, so an interrupted run keeps its progress

    stats["estimated_cost_usd"] = round(
        stats["input_tokens"] / 1e6 * INPUT_PRICE_PER_M
        + stats["output_tokens"] / 1e6 * OUTPUT_PRICE_PER_M,
        6,
    )
    return stats
