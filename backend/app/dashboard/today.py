"""Data for the dashboard's Today page (Milestone 7).

The queries live in small helpers so the grouping in build_today() can be
tested without a database. Every list comes back already sorted, so the
frontend only has to render it.
"""

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.llm.extract import TZ
from app.models import Item, Task, User

WEEK_DAYS = 7
SUCOURSE_LIMIT = 5
PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def day_start(now: datetime) -> datetime:
    """Midnight in Istanbul on the day of `now`."""
    return now.astimezone(TZ).replace(hour=0, minute=0, second=0, microsecond=0)


def task_view(task: Task, source: str | None) -> dict:
    return {
        "id": task.id,
        "title": task.title,
        "due_at": task.due_at.astimezone(TZ).isoformat() if task.due_at else None,
        "priority": task.priority,
        "status": task.status,
        # Tasks from an email or SUCourse name the item's source; others who made them.
        "source": source or task.created_by,
        "item_id": task.item_id,
    }


def _sort_key(task: Task):
    return (task.due_at, PRIORITY_ORDER.get(task.priority, 1), task.id)


def group_tasks(rows: list[tuple[Task, str | None]], now: datetime) -> dict[str, list[dict]]:
    """Splits open tasks into overdue (due before now), today (rest of today) and
    this_week (tomorrow through the next 7 days). Undated and later tasks are left out."""
    today = day_start(now)
    tomorrow = today + timedelta(days=1)
    week_end = tomorrow + timedelta(days=WEEK_DAYS)
    groups = {"overdue": [], "today": [], "this_week": []}
    for task, source in sorted((r for r in rows if r[0].due_at), key=lambda r: _sort_key(r[0])):
        if task.due_at < now:
            groups["overdue"].append(task_view(task, source))
        elif task.due_at < tomorrow:
            groups["today"].append(task_view(task, source))
        elif task.due_at < week_end:
            groups["this_week"].append(task_view(task, source))
    return groups


def event_view(item: Item) -> dict:
    raw = item.raw or {}
    return {
        "id": item.id,
        "title": item.title,
        "start": item.occurred_at.astimezone(TZ).isoformat() if item.occurred_at else None,
        "end": item.due_at.astimezone(TZ).isoformat() if item.due_at else None,
        "all_day": bool(raw.get("all_day")),
        "location": raw.get("location"),
    }


def assignment_view(item: Item, task: Task | None) -> dict:
    return {
        "id": item.id,
        "title": item.title,
        "due_at": item.due_at.astimezone(TZ).isoformat() if item.due_at else None,
        "course": (item.raw or {}).get("course"),
        "task_id": task.id if task else None,
        "task_status": task.status if task else None,
    }


# --- queries ------------------------------------------------------------------

def open_tasks_due_before(db: Session, user: User, end: datetime) -> list[tuple[Task, str | None]]:
    return db.execute(
        select(Task, Item.source)
        .outerjoin(Item, Task.item_id == Item.id)
        .where(Task.user_id == user.id, Task.status == "open", Task.due_at < end)
    ).all()


def count_undated_open_tasks(db: Session, user: User) -> int:
    return db.scalar(
        select(func.count()).select_from(Task)
        .where(Task.user_id == user.id, Task.status == "open", Task.due_at.is_(None))
    ) or 0


def events_between(db: Session, user: User, start: datetime, end: datetime) -> list[Item]:
    """Calendar events that overlap [start, end), including multi-day and all-day ones."""
    return db.scalars(
        select(Item)
        .where(Item.user_id == user.id, Item.source == "calendar", Item.type == "event",
               Item.occurred_at < end, func.coalesce(Item.due_at, Item.occurred_at) > start)
        .order_by(Item.occurred_at, Item.id)
    ).all()


def next_sucourse_items(db: Session, user: User, now: datetime) -> list[tuple[Item, Task | None]]:
    return db.execute(
        select(Item, Task)
        .outerjoin(Task, (Task.item_id == Item.id) & (Task.created_by == "sucourse"))
        .where(Item.user_id == user.id, Item.source == "sucourse", Item.due_at >= now)
        .order_by(Item.due_at, Item.id)
        .limit(SUCOURSE_LIMIT)
    ).all()


def build_today(db: Session, user: User, now: datetime) -> dict:
    today = day_start(now)
    tomorrow = today + timedelta(days=1)
    week_end = tomorrow + timedelta(days=WEEK_DAYS)
    return {
        "now": now.astimezone(TZ).isoformat(),
        "date": today.date().isoformat(),
        **group_tasks(open_tasks_due_before(db, user, week_end), now),
        "undated_count": count_undated_open_tasks(db, user),
        "events": [event_view(i) for i in events_between(db, user, today, tomorrow)],
        "sucourse": [assignment_view(i, t) for i, t in next_sucourse_items(db, user, now)],
    }
