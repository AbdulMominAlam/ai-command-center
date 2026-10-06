"""Background sync: every 30 minutes, the same run as "Sync now" for each connected user.

APScheduler runs the job in a thread inside the FastAPI process. max_instances=1
means a slow run is never started again while it's still going, and the sync
lock in app.sync.runner keeps it from overlapping a manual "Sync now".
"""

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select

from app.auth.google import GoogleReconnectRequired
from app.db import SessionLocal
from app.models import OAuthToken, User
from app.sync.runner import SyncAlreadyRunning, run_full_sync

log = logging.getLogger(__name__)

INTERVAL_MINUTES = 30
JOB_ID = "background_sync"


def summarize(result: dict) -> str:
    """One log line of counts and cost. No titles, senders or URLs."""
    cal = result["calendar"]
    parts = [
        f"gmail +{result['gmail']['added']} emails",
        f"calendar +{cal['added']} ~{cal['updated']} -{cal['deleted']}",
    ]

    su = result["sucourse"]
    if "skipped" in su:
        parts.append("sucourse not set up")
    elif "error" in su:
        parts.append("sucourse error")
    else:
        parts.append(f"sucourse +{su['tasks_created']} tasks")

    ext = result["extraction"]
    if "error" in ext:
        parts.append(f"extraction error: {ext['error']}")
    else:
        cost = ext["estimated_cost_usd"]
        parts.append(f"extraction {ext['processed']} emails -> +{ext['tasks_created']} tasks, "
                     f"{ext['tasks_resolved']} resolved, {ext['failed']} failed")
        parts.append("cost unknown" if cost is None else f"cost ${cost:.4f}")

    parts.append(f"{result['elapsed_seconds']}s")
    return ", ".join(parts)


def run_background_sync(session_factory=SessionLocal) -> None:
    """Syncs every user with a stored Google token. Errors are logged, never raised."""
    try:
        _sync_connected_users(session_factory)
    except Exception:
        log.exception("Background sync failed.")


def _sync_connected_users(session_factory) -> None:
    with session_factory() as db:
        users = db.scalars(
            select(User).join(OAuthToken, OAuthToken.user_id == User.id)
            .where(OAuthToken.provider == "google")
        ).all()
        if not users:
            log.debug("Background sync skipped: no connected Google account.")
            return
        for user in users:
            try:
                result = run_full_sync(db, user)
            except GoogleReconnectRequired:
                db.rollback()
                log.info("Background sync skipped for user %s: Google needs reconnecting.", user.id)
            except SyncAlreadyRunning:
                log.info("Background sync skipped: another sync is running.")
                return
            except Exception:
                db.rollback()
                log.exception("Background sync failed for user %s.", user.id)
            else:
                log.info("Background sync for user %s: %s", user.id, summarize(result))


def create_scheduler() -> BackgroundScheduler:
    """The first run is INTERVAL_MINUTES after startup, not at startup, so restarting
    the dev server (uvicorn --reload) doesn't trigger a sync and Claude calls each time."""
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(run_background_sync, "interval", minutes=INTERVAL_MINUTES, id=JOB_ID,
                      max_instances=1, coalesce=True)
    return scheduler
