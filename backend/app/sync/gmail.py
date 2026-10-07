import base64
import re
import time
from collections import deque
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from sqlalchemy import literal_column, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.auth.google import get_google_credentials
from app.models import Item, SyncState, User

FIRST_RUN_QUERY = "newer_than:30d -category:promotions -category:social"
# One-time backfill of the 30 days before that, with the same filters.
BACKFILL_QUERY = "newer_than:60d older_than:30d -category:promotions -category:social"
# Same filter for incremental runs, checked on labels. SPAM and TRASH are also
# skipped because messages.list leaves them out by default.
SKIP_LABELS = {"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "SPAM", "TRASH"}
BODY_LIMIT = 4000
# Retries 429/5xx and Gmail's 403 rateLimitExceeded with exponential backoff,
# in case the throttle below ever lets a burst slip through.
RETRIES = 6

# This Google Cloud project allows ~550 quota units per user per minute (measured:
# the 403 came after ~109 messages.get calls), far below Gmail's usual 15,000.
# Check APIs & Services > Gmail API > Quotas and raise this if your limit is higher.
UNITS_PER_MINUTE = 500
# Gmail's cost per call, from the Gmail API "Usage limits" page.
COST = {"getProfile": 1, "history.list": 2, "messages.list": 5, "messages.get": 5}


class _Throttle:
    """Sliding one-minute window: sleeps before a call that would exceed the budget."""

    def __init__(self, units_per_minute: int):
        self.budget = units_per_minute
        self.spent: deque[tuple[float, int]] = deque()  # (time, units)

    def spend(self, units: int) -> None:
        while True:
            now = time.monotonic()
            while self.spent and now - self.spent[0][0] >= 60:
                self.spent.popleft()
            if sum(u for _, u in self.spent) + units <= self.budget:
                self.spent.append((now, units))
                return
            time.sleep(60 - (now - self.spent[0][0]))


# One per process, so back-to-back syncs share the same budget.
_throttle = _Throttle(UNITS_PER_MINUTE)


def _execute(request, method: str) -> dict:
    _throttle.spend(COST[method])
    return request.execute(num_retries=RETRIES)


def sync_gmail(db: Session, user: User) -> int:
    """Syncs the user's Gmail into items. Returns how many new emails were added."""
    gmail = build("gmail", "v1", credentials=get_google_credentials(db, user), cache_discovery=False)
    state = db.scalar(
        select(SyncState).where(SyncState.user_id == user.id, SyncState.source == "gmail")
    )

    if state is None or state.cursor is None:
        added, history_id = _full_sync(db, user, gmail)
    else:
        try:
            added, history_id = _incremental_sync(db, user, gmail, state.cursor)
        except HttpError as e:
            if e.status_code != 404:
                raise
            # The saved historyId is too old for Gmail to remember; start over.
            added, history_id = _full_sync(db, user, gmail)

    if state is None:
        state = SyncState(user_id=user.id, source="gmail")
        db.add(state)
    state.cursor = history_id
    state.last_synced_at = datetime.now(timezone.utc)
    db.commit()
    return added


def backfill_gmail(db: Session, user: User) -> dict:
    """One-time import of emails 30 to 60 days old into items.

    Leaves sync_state alone: the saved historyId keeps tracking new mail, and
    these older emails are just upserted next to it. Sensitive and noise emails
    are saved like in a normal sync and skipped later by extraction.
    Returns {"listed": emails matching the query, "added": emails that were new}.
    """
    gmail = build("gmail", "v1", credentials=get_google_credentials(db, user), cache_discovery=False)
    listed, added = _save_matching(db, user, gmail, BACKFILL_QUERY)
    return {"listed": listed, "added": added}


def _full_sync(db: Session, user: User, gmail) -> tuple[int, str]:
    # Read the historyId before listing, so mail arriving during the sync is
    # picked up by the next incremental run instead of being missed.
    history_id = _execute(gmail.users().getProfile(userId="me"), "getProfile")["historyId"]
    _, added = _save_matching(db, user, gmail, FIRST_RUN_QUERY)
    return added, history_id


def _save_matching(db: Session, user: User, gmail, query: str) -> tuple[int, int]:
    """Fetches and upserts every message matching a Gmail search. Returns (listed, added)."""
    listed = added = 0
    page_token = None
    while True:
        resp = _execute(
            gmail.users().messages().list(userId="me", q=query, maxResults=500, pageToken=page_token),
            "messages.list",
        )
        for ref in resp.get("messages", []):
            listed += 1
            added += _fetch_and_save(db, user, gmail, ref["id"])
        page_token = resp.get("nextPageToken")
        if not page_token:
            return listed, added


def _incremental_sync(db: Session, user: User, gmail, start_history_id: str) -> tuple[int, str]:
    new_ids: dict[str, None] = {}  # dict keeps order and drops duplicates
    history_id = start_history_id
    page_token = None
    while True:
        resp = _execute(
            gmail.users().history().list(
                userId="me",
                startHistoryId=start_history_id,
                historyTypes="messageAdded",
                pageToken=page_token,
            ),
            "history.list",
        )
        for record in resp.get("history", []):
            for added in record.get("messagesAdded", []):
                msg = added["message"]
                if not SKIP_LABELS & set(msg.get("labelIds", [])):
                    new_ids[msg["id"]] = None
        history_id = resp.get("historyId", history_id)
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    added = sum(_fetch_and_save(db, user, gmail, message_id) for message_id in new_ids)
    return added, history_id


def _fetch_and_save(db: Session, user: User, gmail, message_id: str) -> int:
    """Fetches one message and upserts it. Returns 1 if it was new, 0 otherwise."""
    try:
        msg = _execute(
            gmail.users().messages().get(userId="me", id=message_id, format="full"),
            "messages.get",
        )
    except HttpError as e:
        if e.status_code == 404:  # deleted between listing and fetching
            return 0
        raise

    payload = msg.get("payload", {})
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
    values = {
        "user_id": user.id,
        "source": "gmail",
        "external_id": msg["id"],
        "type": "email",
        "title": headers.get("subject") or "(no subject)",
        "sender": (headers.get("from") or "")[:320] or None,
        "occurred_at": _parse_date(headers.get("date"), msg.get("internalDate")),
        "body": _extract_body(payload)[:BODY_LIMIT] or None,
        "raw": {"labelIds": msg.get("labelIds", []), "threadId": msg.get("threadId")},
    }
    stmt = insert(Item).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["user_id", "source", "external_id"],
        set_={k: stmt.excluded[k] for k in ("title", "sender", "occurred_at", "body", "raw")},
    ).returning(
        # Postgres trick: xmax is 0 for a freshly inserted row and non-zero for an updated one.
        literal_column("(xmax = 0)")
    )
    is_new = db.execute(stmt).scalar()
    # Commit each email: a first run takes minutes at this quota, and an
    # interrupted run should keep what it already saved.
    db.commit()
    return 1 if is_new else 0


def _parse_date(date_header: str | None, internal_date: str | None) -> datetime | None:
    """The Date header as an aware datetime; falls back to Gmail's receive time."""
    if date_header:
        try:
            parsed = parsedate_to_datetime(date_header)
            if parsed.tzinfo is not None:
                return parsed
        except (TypeError, ValueError):
            pass
    if internal_date:  # milliseconds since the epoch, UTC
        return datetime.fromtimestamp(int(internal_date) / 1000, tz=timezone.utc)
    return None


def _extract_body(payload: dict) -> str:
    """Plain-text body if there is one, otherwise the HTML body with tags removed."""
    # Some senders include a blank text/plain part next to the real HTML body.
    plain = (_find_part(payload, "text/plain") or "").strip()
    if plain:
        return plain
    html = _find_part(payload, "text/html")
    return _strip_html(html) if html else ""


def _find_part(part: dict, mime_type: str) -> str | None:
    """Depth-first search through the MIME tree for the first non-attachment part of a type."""
    data = part.get("body", {}).get("data")
    if part.get("mimeType") == mime_type and data and not part.get("filename"):
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    for child in part.get("parts", []):
        found = _find_part(child, mime_type)
        if found:
            return found
    return None


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.chunks: list[str] = []
        self._skip = 0  # inside <script> or <style>

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in ("br", "p", "div", "tr", "li"):
            self.chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.chunks.append(data)


def _strip_html(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    text = unescape("".join(parser.chunks))
    text = re.sub(r"[ \t\r\f\v\xa0]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()
