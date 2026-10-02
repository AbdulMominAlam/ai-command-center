"""Cleans up tasks that already exist, without re-extracting every email.

One Claude call sees all open tasks plus the one-sentence summaries of the
emails since the oldest task's email (never the bodies). It answers with
save_cleanup. Each decision sets a task's status:
- "duplicate": the same action as another open task
- "done": an email shows it was completed or is no longer needed (must cite that email)
- "expired": its date passed with no sign it was completed, so it stays visible as missed
Nothing is deleted. Every run is saved as a CleanupPlan, so a dry run can be
applied later exactly as shown, without a second Claude call.
"""

from datetime import datetime
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.llm.extract import TZ, _fmt, _inline_refs, call_tool, estimate_cost, open_tasks
from app.models import CleanupPlan, Item, LLMUsage, Task, User

MAX_TASKS = 100
MAX_EMAILS = 400
EXPIRED_REASON = "Its date passed and no email shows it was completed."


class CleanupDecision(BaseModel):
    task_id: int
    action: Literal["duplicate", "done", "expired"]
    duplicate_of: int | None = Field(
        default=None, description="For duplicate: the id of the open task to keep."
    )
    evidence_email_id: int | None = Field(
        default=None,
        description="For done: the id of the email showing the task was completed or is no longer needed.",
    )
    reason: str = Field(description="Short reason, without codes, passwords or amounts of money.")


class Cleanup(BaseModel):
    decisions: list[CleanupDecision] = Field(
        description="Only tasks that should leave the open list. Empty if nothing should change."
    )


CLEANUP_TOOL = {
    "name": "save_cleanup",
    "description": "Save which open tasks are duplicates, done or expired.",
    "input_schema": _inline_refs(Cleanup.model_json_schema()),
}

CLEANUP_PROMPT = """You keep a university student's task list clean. You get their open tasks, each with the email it came from, and one-sentence summaries of the emails received since then.

Call save_cleanup exactly once with a decision for each task that should leave the open list:
- duplicate: the task is the same action as another open task (a reminder, a resend, the same event). Keep the one with the most accurate due date and set duplicate_of to its id. Never mark both tasks of a pair.
- done: an email shows the task was actually completed or is no longer needed, such as a submission confirmation, an accepted application ("profile accepted" after "profile marked discrepant") or a cancelled meeting. Set evidence_email_id to that email's id. A security alert (new sign-in, login attempt, suspicious activity) sent 3 or more days before Today is no longer needed; use the alert's own email id as evidence.
- expired: the deadline or event date has passed (before Today) and no email shows the task was completed. Do not call these done.

When unsure, leave the task alone."""


def valid_decisions(decisions: list[CleanupDecision], tasks: list[Task], email_ids: set[int],
                    now: datetime) -> list[CleanupDecision]:
    """Keeps only decisions that are safe to apply, then expires any other open
    task whose due date has passed.

    Dropped: unknown or repeated task ids, duplicates of themselves or of an
    unknown task, and removing both sides of a duplicate pair. A "done" without
    a real evidence email becomes "expired" if its date passed and is dropped
    otherwise, so a task is never called done just because time went by.
    """
    by_id = {t.id: t for t in tasks}
    passed = {t.id for t in tasks if t.due_at and t.due_at < now}
    kept: list[CleanupDecision] = []
    decided: set[int] = set()

    for d in decisions:
        if d.task_id not in by_id or d.task_id in decided:
            continue
        if d.action == "duplicate":
            if d.duplicate_of not in by_id or d.duplicate_of in (d.task_id, *decided):
                continue
        elif d.action == "done" and d.evidence_email_id not in email_ids:
            if d.task_id not in passed:
                continue
            d = CleanupDecision(task_id=d.task_id, action="expired", reason=EXPIRED_REASON)
        elif d.action == "expired" and d.task_id not in passed and by_id[d.task_id].due_at:
            continue  # its due date is still ahead
        kept.append(d)
        decided.add(d.task_id)

    # A task kept as the original must not also be removed.
    originals = {d.duplicate_of for d in kept if d.action == "duplicate"}
    kept = [d for d in kept if d.task_id not in originals]
    decided = {d.task_id for d in kept}

    for task_id in sorted(passed - decided):
        kept.append(CleanupDecision(task_id=task_id, action="expired", reason=EXPIRED_REASON))
    return kept


def plan_cleanup(db: Session, user: User) -> tuple[list[dict], int, int]:
    """Asks Claude for cleanup decisions. Returns (decisions, input_tokens, output_tokens)."""
    tasks = open_tasks(db, user, MAX_TASKS)
    if not tasks:
        return [], 0, 0

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
    now = datetime.now(TZ)
    user_message = (
        f"Today: {_fmt(now)} (Europe/Istanbul)\n\n"
        "Open tasks:\n" + "\n".join(task_lines) + "\n\n"
        "Emails since the oldest task, oldest first:\n" + ("\n".join(email_lines) or "(none)")
    )

    cleanup, input_tokens, output_tokens = call_tool(
        CLEANUP_PROMPT, user_message, CLEANUP_TOOL, Cleanup, max_tokens=2000
    )
    db.add(LLMUsage(purpose="cleanup", model=settings.EXTRACT_MODEL,
                    input_tokens=input_tokens, output_tokens=output_tokens))

    by_id = {t.id: t for t in tasks}
    email_ids = {e.id for e in emails} | set(sources)
    decisions = [
        {**d.model_dump(), "title": by_id[d.task_id].title}
        for d in valid_decisions(cleanup.decisions, tasks, email_ids, now)
    ]
    return decisions, input_tokens, output_tokens


def apply_plan(db: Session, user: User, plan: CleanupPlan) -> dict:
    """Applies a saved plan exactly. Tasks that are no longer open are skipped."""
    applied, skipped = [], []
    for d in plan.decisions:
        task = db.get(Task, d["task_id"])
        if task is None or task.user_id != user.id or task.status != "open":
            skipped.append(d["task_id"])
            continue
        task.status = d["action"]  # "duplicate" | "done" | "expired"
        applied.append(d["task_id"])
    plan.applied_at = datetime.now(TZ)
    return {"applied": applied, "skipped_not_open": skipped}


def run_cleanup(db: Session, user: User, dry_run: bool = False, plan_id: int | None = None) -> dict:
    """dry_run=True: ask Claude, save the plan, change nothing.
    plan_id: apply that saved plan (no Claude call).
    Neither: ask Claude and apply right away."""
    if plan_id is not None:
        if dry_run:
            raise HTTPException(400, "Use either dry_run or plan_id, not both.")
        plan = db.get(CleanupPlan, plan_id)
        if plan is None or plan.user_id != user.id:
            raise HTTPException(404, "Cleanup plan not found.")
        if plan.applied_at is not None:
            raise HTTPException(409, "This plan was already applied.")
        outcome = apply_plan(db, user, plan)
        db.commit()
        return {"plan_id": plan.id, "dry_run": False, "decisions": plan.decisions, **outcome,
                "input_tokens": 0, "output_tokens": 0, "estimated_cost_usd": 0.0}

    decisions, input_tokens, output_tokens = plan_cleanup(db, user)
    plan = CleanupPlan(user_id=user.id, decisions=decisions)
    db.add(plan)
    db.flush()  # assigns plan.id
    outcome = {} if dry_run else apply_plan(db, user, plan)
    db.commit()  # the usage row and the plan are saved even on a dry run
    return {"plan_id": plan.id, "dry_run": dry_run, "decisions": decisions, **outcome,
            "input_tokens": input_tokens, "output_tokens": output_tokens,
            "estimated_cost_usd": estimate_cost(input_tokens, output_tokens)}
