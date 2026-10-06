import logging
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx
from icalendar import Calendar
from sqlalchemy import literal_column, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Item, SyncState, Task, User

TZ = ZoneInfo(settings.TIMEZONE)  # Europe/Istanbul
BODY_LIMIT = 1000
HIGH_PRIORITY_WITHIN = timedelta(days=3)

# The export URL contains a Moodle authtoken, so it must never show up in logs.
# httpx logs every request URL at INFO level.
logging.getLogger("httpx").setLevel(logging.WARNING)


class SucourseNotConfigured(Exception):
    """No Moodle calendar export URL saved yet; POST /sync/sucourse/url first."""


class SucourseFetchFailed(Exception):
    """Downloading the .ics failed. The message never includes the URL."""


def validate_url(url: str) -> str:
    """Checks that this looks like an https URL. Errors never repeat the URL."""
    url = url.strip()
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.netloc:
        raise ValueError("The calendar URL must be a full https:// link.")
    return url


def save_url(db: Session, user: User, url: str) -> None:
    url = validate_url(url)
    state = db.scalar(
        select(SyncState).where(SyncState.user_id == user.id, SyncState.source == "sucourse")
    )
    if state is None:
        state = SyncState(user_id=user.id, source="sucourse")
        db.add(state)
    state.cursor = url
    state.last_synced_at = None
    db.commit()


def sync_sucourse(db: Session, user: User) -> dict:
    """Downloads the Moodle .ics and upserts every event as an assignment with a task."""
    state = db.scalar(
        select(SyncState).where(SyncState.user_id == user.id, SyncState.source == "sucourse")
    )
    if state is None or not state.cursor:
        raise SucourseNotConfigured("No SUCourse calendar URL saved. POST /sync/sucourse/url first.")

    events = parse_ics(_download(state.cursor))
    now = datetime.now(timezone.utc)
    stats = {"events": len(events), "added": 0, "updated": 0,
             "tasks_created": 0, "tasks_updated": 0}
    for event in events:
        item_id, is_new = _upsert_item(db, user, event)
        stats["added" if is_new else "updated"] += 1
        stats["tasks_created" if _upsert_task(db, user, item_id, event, now) else "tasks_updated"] += 1

    state.last_synced_at = now
    db.commit()
    return stats


def _download(url: str) -> bytes:
    try:
        resp = httpx.get(url, timeout=30, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        # str(e) would include the URL and its authtoken.
        raise SucourseFetchFailed(f"SUCourse returned HTTP {e.response.status_code}.") from None
    except httpx.HTTPError as e:
        raise SucourseFetchFailed(f"Could not reach SUCourse ({type(e).__name__}).") from None
    return resp.content


def parse_ics(data: bytes) -> list[dict]:
    """Turns a Moodle .ics export into item values, one per VEVENT with a UID and DTSTART."""
    events = []
    for component in Calendar.from_ical(data).walk("VEVENT"):
        uid = component.get("UID")
        if not uid or component.get("DTSTART") is None:
            continue
        description = str(component.get("DESCRIPTION") or "").strip()
        events.append({
            "external_id": str(uid)[:255],
            "title": str(component.get("SUMMARY") or "").strip() or "(no title)",
            "due_at": _to_aware(component.decoded("DTSTART")),
            "body": description[:BODY_LIMIT] or None,
            "raw": {"course": _course(component.get("CATEGORIES"))},
        })
    return events


def _to_aware(value: date | datetime) -> datetime:
    """Date-only values become Istanbul midnight; floating times are read as Istanbul time."""
    if not isinstance(value, datetime):
        return datetime.combine(value, time(0), tzinfo=TZ)
    return value if value.tzinfo else value.replace(tzinfo=TZ)


def _course(categories) -> str | None:
    """Moodle puts the course short name in CATEGORIES. There may be several lines of it."""
    if categories is None:
        return None
    if not isinstance(categories, list):
        categories = [categories]
    names = [str(c) for prop in categories for c in prop.cats if str(c).strip()]
    return ", ".join(names) or None


def priority_for(title: str, due_at: datetime, now: datetime) -> str:
    """'low' for "... opens" events, which are not deadlines. Otherwise 'high' when
    due within 3 days (overdue counts too) and 'medium' after that."""
    if title.strip().lower().endswith("opens"):
        return "low"
    return "high" if due_at - now <= HIGH_PRIORITY_WITHIN else "medium"


def _upsert_item(db: Session, user: User, event: dict) -> tuple[int, bool]:
    stmt = insert(Item).values(user_id=user.id, source="sucourse", type="assignment", **event)
    stmt = stmt.on_conflict_do_update(
        index_elements=["user_id", "source", "external_id"],
        set_={k: stmt.excluded[k] for k in ("title", "due_at", "body", "raw")},
    ).returning(Item.id, literal_column("(xmax = 0)"))  # xmax = 0 means a fresh insert
    item_id, is_new = db.execute(stmt).one()
    return item_id, bool(is_new)


def _upsert_task(db: Session, user: User, item_id: int, event: dict, now: datetime) -> bool:
    """Creates the assignment's task, or refreshes it. Returns True if it was created."""
    task = db.scalar(
        select(Task).where(Task.item_id == item_id, Task.created_by == "sucourse")
    )
    if task is None:
        db.add(Task(user_id=user.id, item_id=item_id, title=event["title"],
                    due_at=event["due_at"], priority=priority_for(event["title"], event["due_at"], now),
                    created_by="sucourse"))
        return True
    task.title = event["title"]
    task.due_at = event["due_at"]
    # Leave tasks you closed alone; only open ones move up to "high" as the deadline nears.
    if task.status == "open":
        task.priority = priority_for(event["title"], event["due_at"], now)
    return False
