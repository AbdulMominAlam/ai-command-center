import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.accounts import linked_accounts
from app.auth.routes import get_current_user
from app.db import get_db
from app.models import User
from app.sync.calendar import sync_calendar
from app.sync.gmail import sync_gmail
from app.sync.runner import SyncAlreadyRunning, run_full_sync
from app.sync.sucourse import (
    SucourseFetchFailed,
    SucourseNotConfigured,
    save_url,
    sync_sucourse,
)

router = APIRouter(prefix="/sync")


class SucourseUrl(BaseModel):
    # A plain str on purpose: a stricter type such as HttpUrl would put the
    # rejected URL (and its authtoken) into FastAPI's 422 error response.
    url: str


@router.post("/gmail")
def sync_gmail_endpoint(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Pulls new emails from every linked Gmail account into items (14 days on an account's first run)."""
    start = time.perf_counter()
    added = sum(sync_gmail(db, user, account) for account in linked_accounts(db, user))
    return {"added": added, "elapsed_seconds": round(time.perf_counter() - start, 2)}


@router.post("/calendar")
def sync_calendar_endpoint(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Mirrors the next 60 days of every linked account's primary Google Calendar into items."""
    start = time.perf_counter()
    counts = {"added": 0, "updated": 0, "deleted": 0}
    for account in linked_accounts(db, user):
        for key, n in sync_calendar(db, user, account).items():
            counts[key] += n
    return {**counts, "elapsed_seconds": round(time.perf_counter() - start, 2)}


@router.post("/sucourse/url")
def set_sucourse_url(
    body: SucourseUrl, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Saves the Moodle calendar export URL. The URL is never sent back."""
    try:
        save_url(db, user, body.url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"saved": True}


@router.post("/sucourse")
def sync_sucourse_endpoint(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Downloads the SUCourse .ics and upserts assignments and their tasks."""
    start = time.perf_counter()
    try:
        counts = sync_sucourse(db, user)
    except SucourseNotConfigured as e:
        raise HTTPException(status_code=400, detail=str(e))
    except SucourseFetchFailed as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {**counts, "elapsed_seconds": round(time.perf_counter() - start, 2)}


@router.post("/all")
def sync_all(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Runs every sync, then extracts tasks from up to EXTRACT_ON_SYNC_LIMIT new emails."""
    try:
        return run_full_sync(db, user)
    except SyncAlreadyRunning as e:
        raise HTTPException(status_code=409, detail=str(e))
