"""One full sync: Gmail and Calendar for every linked account, SUCourse, then
extraction on the new emails.

Used by POST /sync/all and by the background job. A process-wide lock makes sure
only one run happens at a time, so a manual "Sync now" and a scheduled run can
never extract the same emails twice.
"""

import threading
import time
from contextlib import contextmanager

import anthropic
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth.accounts import linked_accounts
from app.auth.google import GoogleReconnectRequired
from app.config import settings
from app.db import engine
from app.llm.client import CreditTooLow
from app.llm.extract import process_unprocessed
from app.models import User
from app.sync.calendar import sync_calendar
from app.sync.gmail import is_first_sync, sync_gmail
from app.sync.sucourse import SucourseFetchFailed, SucourseNotConfigured, sync_sucourse

_lock = threading.Lock()
# Postgres advisory lock id (any fixed number). The thread lock above only covers
# this process; this one also covers scripts like app.sync.backfill that run
# next to the API and its background job.
DB_LOCK_KEY = 7_201_001

# Extraction counts returned to the frontend (task titles stay out of the summary).
EXTRACTION_KEYS = ("processed", "tasks_created", "tasks_resolved", "duplicates_skipped",
                   "skipped_sensitive", "skipped_noise", "skipped_university_personal", "skipped_course_admin", "skipped_over_cap", "failed",
                   "input_tokens", "output_tokens", "cache_creation_tokens", "cache_read_tokens",
                   "estimated_cost_usd")


# A newly linked account's first sync pulls 14 days of mail (FIRST_RUN_DAYS in
# gmail.py). Its extraction runs newest first and stops at this estimated cost;
# emails it can't afford are marked "Skipped: first-sync cost cap".
FIRST_SYNC_COST_CAP_USD = 0.30
FIRST_SYNC_EXTRACT_LIMIT = 2000


class SyncAlreadyRunning(Exception):
    """Another sync (manual or background) is still running."""


@contextmanager
def exclusive():
    """Holds the sync lock for the block, or raises SyncAlreadyRunning without waiting."""
    if not _lock.acquire(blocking=False):
        raise SyncAlreadyRunning("A sync is already running. Try again in a minute.")
    try:
        with _db_lock():
            yield
    finally:
        _lock.release()


@contextmanager
def _db_lock():
    """Holds a Postgres advisory lock on its own connection, or raises SyncAlreadyRunning."""
    with engine.connect() as conn:
        if not conn.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": DB_LOCK_KEY}):
            raise SyncAlreadyRunning("A sync is already running in another process. Try again in a minute.")
        try:
            yield
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": DB_LOCK_KEY})


def run_full_sync(db: Session, user: User) -> dict:
    """Runs every sync, then extraction on up to EXTRACT_ON_SYNC_LIMIT unprocessed emails.

    A missing SUCourse URL, a SUCourse download error or a rejected Claude API key
    is reported in the result instead of failing the whole run (other Claude errors
    are recorded per email by extraction). An account that needs reconnecting is
    skipped and listed in result["reconnect_needed"]; GoogleReconnectRequired is
    raised only when every linked account needs it.
    """
    with exclusive():
        start = time.perf_counter()
        accounts = linked_accounts(db, user)
        if not accounts:
            raise GoogleReconnectRequired("No Google account connected. Reconnect Google.")
        gmail_added, calendar = 0, {"added": 0, "updated": 0, "deleted": 0}
        first_syncs, reconnect = [], []
        for account in accounts:
            try:
                first = is_first_sync(db, user, account)
                gmail_added += sync_gmail(db, user, account)
                if first:
                    first_syncs.append(account)
                for key, n in sync_calendar(db, user, account).items():
                    calendar[key] += n
            except GoogleReconnectRequired:
                db.rollback()
                reconnect.append(account.email)
        if len(reconnect) == len(accounts):
            raise GoogleReconnectRequired("Google access was revoked or expired. Reconnect Google.")
        result = {"gmail": {"added": gmail_added}, "calendar": calendar}
        if reconnect:
            result["reconnect_needed"] = reconnect
        try:
            result["sucourse"] = sync_sucourse(db, user)
        except SucourseNotConfigured as e:
            result["sucourse"] = {"skipped": str(e)}
        except SucourseFetchFailed as e:
            result["sucourse"] = {"error": str(e)}

        try:
            runs = [process_unprocessed(db, user, FIRST_SYNC_EXTRACT_LIMIT, max_cost_usd=FIRST_SYNC_COST_CAP_USD,
                                        account_id=account.id, skip_over_cap=True)
                    for account in first_syncs]
            runs.append(process_unprocessed(db, user, settings.EXTRACT_ON_SYNC_LIMIT))
            result["extraction"] = combine(runs)
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            # Emails already handled stay processed; the rest wait for the next run.
            db.rollback()
            result["extraction"] = {"error": f"Claude rejected the API key ({type(e).__name__})."}
        except CreditTooLow as e:
            # Same as a bad key: stop, and leave the remaining emails unprocessed.
            db.rollback()
            result["extraction"] = {"error": str(e), "credit_low": True}

        result["elapsed_seconds"] = round(time.perf_counter() - start, 2)
        return result


def combine(runs: list[dict]) -> dict:
    """Adds up the counts of several extraction runs (cost is unknown if any run's is)."""
    out = {}
    for key in EXTRACTION_KEYS:
        values = [r.get(key, 0) for r in runs]
        out[key] = None if None in values else (round(sum(values), 6) if key == "estimated_cost_usd" else sum(values))
    return out
