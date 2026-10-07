from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from googleapiclient.discovery import build
from sqlalchemy import delete, literal_column, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.auth.google import get_google_credentials
from app.config import settings
from app.models import GoogleAccount, Item, SyncState, User

TZ = ZoneInfo(settings.TIMEZONE)  # Europe/Istanbul
WINDOW_DAYS = 60
RETRIES = 6


def sync_calendar(db: Session, user: User, account: GoogleAccount) -> dict:
    """Mirrors the next 60 days of one linked account's primary calendar into items.

    Returns counts of added, updated and deleted events.
    """
    calendar = build(
        "calendar", "v3", credentials=get_google_credentials(account), cache_discovery=False
    )
    time_min = datetime.now(timezone.utc)
    time_max = time_min + timedelta(days=WINDOW_DAYS)

    seen: set[str] = set()
    added = updated = 0
    page_token = None
    while True:
        resp = calendar.events().list(
            calendarId="primary",
            timeMin=time_min.isoformat(),
            timeMax=time_max.isoformat(),
            singleEvents=True,  # expand recurring events into one item per occurrence
            orderBy="startTime",
            maxResults=2500,
            pageToken=page_token,
        ).execute(num_retries=RETRIES)
        for event in resp.get("items", []):
            if event.get("status") == "cancelled":
                continue
            seen.add(event["id"])
            if _upsert_event(db, user, account, event):
                added += 1
            else:
                updated += 1
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    # Delete events that Google would have returned for this window but no longer
    # does (deleted or moved out). Same overlap rule Google uses: ends after
    # timeMin and starts before timeMax. Past events stay as history.
    deleted = db.execute(
        delete(Item).where(
            Item.user_id == user.id,
            Item.account_id == account.id,
            Item.source == "calendar",
            Item.due_at > time_min,
            Item.occurred_at < time_max,
            Item.external_id.not_in(seen),
        )
    ).rowcount

    _mark_synced(db, user, account)
    db.commit()
    return {"added": added, "updated": updated, "deleted": deleted}


def _upsert_event(db: Session, user: User, account: GoogleAccount, event: dict) -> bool:
    """Inserts or updates one event. Returns True if it was new."""
    all_day = "date" in event.get("start", {})
    values = {
        "user_id": user.id,
        "account_id": account.id,
        "source": "calendar",
        "external_id": event["id"],
        "type": "event",
        "title": event.get("summary") or "(no title)",
        "occurred_at": parse_event_time(event["start"]),
        "due_at": parse_event_time(event["end"]),
        "raw": {
            "location": event.get("location"),
            "htmlLink": event.get("htmlLink"),
            "all_day": all_day,
        },
    }
    stmt = insert(Item).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["user_id", "source", "account_id", "external_id"],
        set_={k: stmt.excluded[k] for k in ("title", "occurred_at", "due_at", "raw")},
    ).returning(literal_column("(xmax = 0)"))  # true for an insert, false for an update
    return bool(db.execute(stmt).scalar())


def parse_event_time(value: dict) -> datetime:
    """Google's {"dateTime": ...} or, for all-day events, {"date": "YYYY-MM-DD"}.

    All-day dates become midnight in Europe/Istanbul. Google's end date for an
    all-day event is exclusive, so a one-day event on the 10th ends at the 11th 00:00.
    """
    if "dateTime" in value:
        return datetime.fromisoformat(value["dateTime"])
    return datetime.combine(date.fromisoformat(value["date"]), time(0), tzinfo=TZ)


def _mark_synced(db: Session, user: User, account: GoogleAccount) -> None:
    state = db.scalar(select(SyncState).where(
        SyncState.user_id == user.id, SyncState.source == "calendar", SyncState.account_id == account.id))
    if state is None:
        state = SyncState(user_id=user.id, source="calendar", account_id=account.id)
        db.add(state)
    state.last_synced_at = datetime.now(timezone.utc)
