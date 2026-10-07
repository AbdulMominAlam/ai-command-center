"""Google Tasks sync: each incomplete Google Task becomes one of our tasks.

Every task list of every linked account is read on each sync (showCompleted and
showHidden, so tasks completed in Google are seen too). Each Google Task is an
item (source "google_tasks", with its account) plus a task with
created_by="google_tasks":
- incomplete in Google: created if new, title and due date kept up to date;
- completed in Google: our task is marked done (one never seen while open is skipped);
- deleted in Google, or gone from its list: our task is marked done;
- reopened in Google after being completed there: our task is reopened. A task
  you mark done here while it's still open in Google stays done.
Google Tasks only stores a due *date* (the time is always 00:00 UTC), so the due
time becomes 23:59 Istanbul time that day, like date-only deadlines in emails.
Notes are not stored. No LLM calls.
"""

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from sqlalchemy import literal_column, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.auth.google import TASKS_SCOPE, get_google_credentials, has_scope
from app.config import settings
from app.models import GoogleAccount, Item, SyncState, Task, User

TZ = ZoneInfo(settings.TIMEZONE)
SOURCE = "google_tasks"
RETRIES = 6


class TasksNotAvailable(Exception):
    """This account can't be read yet: it needs to re-consent, or the Tasks API is off."""


def due_from_google(due: str | None) -> datetime | None:
    """Google's "2026-10-09T00:00:00.000Z" (a date only) -> 23:59 that day in Istanbul."""
    if not due:
        return None
    return datetime.combine(date.fromisoformat(due[:10]), time(23, 59), tzinfo=TZ)


def sync_google_tasks(db: Session, user: User, account: GoogleAccount) -> dict:
    """Mirrors one account's Google Tasks into tasks. Returns counts."""
    if not has_scope(account, TASKS_SCOPE):
        raise TasksNotAvailable("Reconnect this account to allow Google Tasks.")
    service = build("tasks", "v1", credentials=get_google_credentials(account), cache_discovery=False)
    try:
        google_tasks = list(_all_tasks(service))
    except HttpError as e:
        if e.status_code == 403:  # scope missing or the Tasks API not enabled in the Cloud project
            raise TasksNotAvailable("Google refused access to Tasks: enable the Tasks API and reconnect.") from e
        raise

    stats = {"seen": len(google_tasks), "created": 0, "updated": 0, "completed": 0, "reopened": 0}
    seen: set[str] = set()
    for g in google_tasks:
        if g.get("deleted"):
            continue  # handled below like a task that's gone
        seen.add(g["id"])
        outcome = _apply(db, user, account, g)
        if outcome:
            stats[outcome] += 1
    stats["completed"] += _close_missing(db, user, account, seen)

    _mark_synced(db, user, account)
    db.commit()
    return stats


def _all_tasks(service):
    """Every task in every list, including completed and hidden ones."""
    page = None
    while True:
        lists = service.tasklists().list(maxResults=100, pageToken=page).execute(num_retries=RETRIES)
        for tasklist in lists.get("items", []):
            task_page = None
            while True:
                resp = service.tasks().list(
                    tasklist=tasklist["id"], showCompleted=True, showHidden=True, showDeleted=False,
                    maxResults=100, pageToken=task_page,
                ).execute(num_retries=RETRIES)
                for task in resp.get("items", []):
                    yield {**task, "list_title": tasklist.get("title")}
                task_page = resp.get("nextPageToken")
                if not task_page:
                    break
        page = lists.get("nextPageToken")
        if not page:
            return


def _apply(db: Session, user: User, account: GoogleAccount, g: dict) -> str | None:
    """Upserts the item and creates or updates its task. Returns which stat it counts as."""
    completed = g.get("status") == "completed"
    title = (g.get("title") or "").strip() or "(untitled Google Task)"
    due_at = due_from_google(g.get("due"))

    # A column select always reads the database, unlike a loaded Item, which the
    # Core upsert below would leave stale within the same session.
    previous = db.execute(select(Item.raw).where(
        Item.user_id == user.id, Item.source == SOURCE, Item.account_id == account.id, Item.external_id == g["id"])
    ).first()
    was_completed = bool(previous and (previous[0] or {}).get("status") == "completed")
    if previous is None and completed:
        return None  # finished before we ever saw it: nothing to track

    stmt = insert(Item).values(
        user_id=user.id, account_id=account.id, source=SOURCE, external_id=g["id"], type="google_task",
        title=title, due_at=due_at, processed=True,  # never sent to extraction
        raw={"status": g.get("status"), "list": g.get("list_title"), "updated": g.get("updated")},
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["user_id", "source", "account_id", "external_id"],
        set_={k: stmt.excluded[k] for k in ("title", "due_at", "raw")},
    ).returning(Item.id, literal_column("(xmax = 0)"))
    item_id, _ = db.execute(stmt).one()

    task = db.scalar(select(Task).where(Task.item_id == item_id, Task.created_by == SOURCE))
    if task is None:
        db.add(Task(user_id=user.id, item_id=item_id, title=title, due_at=due_at, created_by=SOURCE))
        return "created"
    task.title, task.due_at = title, due_at
    if completed and task.status == "open":
        task.status = "done"
        return "completed"
    if not completed and was_completed and task.status == "done":
        task.status = "open"  # reopened in Google
        return "reopened"
    return "updated"


def _close_missing(db: Session, user: User, account: GoogleAccount, seen: set[str]) -> int:
    """Marks open tasks whose Google Task was deleted or is no longer listed as done."""
    rows = db.execute(
        select(Task, Item.external_id).join(Item, Task.item_id == Item.id)
        .where(Task.user_id == user.id, Task.created_by == SOURCE, Task.status == "open",
               Item.account_id == account.id)
    ).all()
    closed = 0
    for task, external_id in rows:
        if external_id not in seen:
            task.status = "done"
            closed += 1
    return closed


def _mark_synced(db: Session, user: User, account: GoogleAccount) -> None:
    state = db.scalar(select(SyncState).where(
        SyncState.user_id == user.id, SyncState.source == SOURCE, SyncState.account_id == account.id))
    if state is None:
        state = SyncState(user_id=user.id, source=SOURCE, account_id=account.id)
        db.add(state)
    state.last_synced_at = datetime.now(timezone.utc)
