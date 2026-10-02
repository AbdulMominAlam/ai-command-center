"""Cleans up tasks that already exist, without re-extracting every email.

One Claude call sees all open tasks plus the one-sentence summaries of the
emails since the oldest task's email (never the bodies). It answers with
save_cleanup: which tasks repeat another one and which a later email shows are
finished. Duplicates get status "duplicate" and finished tasks "done"; nothing
is deleted.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.llm.extract import TZ, _fmt, _inline_refs, call_tool, estimate_cost, open_tasks
from app.models import Item, LLMUsage, Task, User

MAX_TASKS = 100
MAX_EMAILS = 400


class CleanupDecision(BaseModel):
    task_id: int
    action: Literal["duplicate", "resolved"]
    duplicate_of: int | None = Field(
        default=None, description="For duplicates: the id of the open task to keep."
    )
    reason: str = Field(description="Short reason, without codes, passwords or amounts of money.")


class Cleanup(BaseModel):
    decisions: list[CleanupDecision] = Field(
        description="Only tasks that should leave the open list. Empty if nothing should change."
    )


CLEANUP_TOOL = {
    "name": "save_cleanup",
    "description": "Save which open tasks are duplicates or already resolved.",
    "input_schema": _inline_refs(Cleanup.model_json_schema()),
}

CLEANUP_PROMPT = """You keep a university student's task list clean. You get their open tasks, each with the email it came from, and one-sentence summaries of the emails received since then.

Call save_cleanup exactly once with a decision for each task that should leave the open list:
- duplicate: the task is the same action as another open task (a reminder, a resend, the same deadline). Keep the one with the most accurate due date and set duplicate_of to its id. Never mark both tasks of a pair.
- resolved: a later email shows the task is done or no longer needed, such as a submission confirmation, an accepted application ("profile accepted" after "profile marked discrepant") or a cancelled meeting.
- resolved: the task came from a security alert (new sign-in, login attempt, suspicious activity) sent 3 or more days before Today.

Do not resolve a task only because its due date has passed. When unsure, leave the task alone."""


def valid_decisions(decisions: list[CleanupDecision], open_ids: set[int]) -> list[CleanupDecision]:
    """Drops decisions that point at unknown tasks, repeat a task, or would
    remove both sides of a duplicate pair."""
    kept: list[CleanupDecision] = []
    removed: set[int] = set()
    for d in decisions:
        if d.task_id not in open_ids or d.task_id in removed:
            continue
        if d.action == "duplicate":
            if d.duplicate_of not in open_ids or d.duplicate_of == d.task_id:
                continue
            if d.duplicate_of in removed:
                continue
        kept.append(d)
        removed.add(d.task_id)
    # A task kept as the original must not also be removed.
    originals = {d.duplicate_of for d in kept if d.action == "duplicate"}
    return [d for d in kept if d.task_id not in originals]


def run_cleanup(db: Session, user: User, dry_run: bool = False) -> dict:
    tasks = open_tasks(db, user, MAX_TASKS)
    result = {"open_tasks": len(tasks), "dry_run": dry_run, "decisions": [],
              "input_tokens": 0, "output_tokens": 0, "estimated_cost_usd": 0.0}
    if not tasks:
        return result

    sources = {i.id: i for i in db.scalars(select(Item).where(Item.id.in_({t.item_id for t in tasks if t.item_id})))}
    oldest = min((i.occurred_at for i in sources.values() if i.occurred_at), default=None)

    emails_query = (
        select(Item)
        .where(Item.user_id == user.id, Item.type == "email", Item.processed.is_(True),
               Item.summary.is_not(None), Item.summary.not_like("Skipped:%"))
        .order_by(Item.occurred_at.desc().nulls_last())
        .limit(MAX_EMAILS)
    )
    if oldest:
        emails_query = emails_query.where(Item.occurred_at >= oldest)
    emails = list(reversed(db.scalars(emails_query).all()))

    def source_line(t: Task) -> str:
        src = sources.get(t.item_id)
        if not src:
            return "no source email"
        sent = _fmt(src.occurred_at) if src.occurred_at else "unknown time"
        return f"from email [{src.id}] sent {sent}, {src.sender or 'unknown sender'}: {src.title}"

    task_lines = [
        f"- [{t.id}] {t.title} ({'due ' + _fmt(t.due_at) if t.due_at else 'no due date'}; {source_line(t)})"
        for t in sorted(tasks, key=lambda t: t.id)
    ]
    email_lines = [
        f"- [{e.id}] {_fmt(e.occurred_at) if e.occurred_at else 'unknown time'} | {e.sender or 'unknown'} | {e.title} | {e.summary}"
        for e in emails
    ]
    user_message = (
        f"Today: {_fmt(datetime.now(TZ))} (Europe/Istanbul)\n\n"
        "Open tasks:\n" + "\n".join(task_lines) + "\n\n"
        "Emails since the oldest task, oldest first:\n" + ("\n".join(email_lines) or "(none)")
    )

    cleanup, input_tokens, output_tokens = call_tool(
        CLEANUP_PROMPT, user_message, CLEANUP_TOOL, Cleanup, max_tokens=2000
    )
    db.add(LLMUsage(purpose="cleanup", model=settings.EXTRACT_MODEL,
                    input_tokens=input_tokens, output_tokens=output_tokens))

    by_id = {t.id: t for t in tasks}
    decisions = valid_decisions(cleanup.decisions, set(by_id))
    for d in decisions:
        if not dry_run:
            by_id[d.task_id].status = "duplicate" if d.action == "duplicate" else "done"
        result["decisions"].append({
            "task_id": d.task_id,
            "title": by_id[d.task_id].title,
            "action": d.action,
            "duplicate_of": d.duplicate_of if d.action == "duplicate" else None,
            "reason": d.reason,
        })
    db.commit()  # the usage row is saved even on a dry run

    result.update(input_tokens=input_tokens, output_tokens=output_tokens,
                  estimated_cost_usd=estimate_cost(input_tokens, output_tokens),
                  ignored=len(cleanup.decisions) - len(decisions))
    return result
