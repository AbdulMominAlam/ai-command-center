from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth.routes import get_current_user
from app.db import get_db
from app.llm.cleanup import run_cleanup
from app.llm.extract import process_unprocessed
from app.models import User
from app.sync.runner import SyncAlreadyRunning, exclusive

router = APIRouter(prefix="/extract")


@router.post("/run")
def run_extraction(
    limit: int = Query(10, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Runs Claude over the oldest unprocessed emails and saves the tasks it finds."""
    try:
        with exclusive():  # not while a sync is extracting the same emails
            return process_unprocessed(db, user, limit)
    except SyncAlreadyRunning as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/cleanup")
def cleanup_tasks(
    dry_run: bool = Query(False, description="Save the decisions as a plan without changing any task."),
    plan_id: int | None = Query(None, description="Apply a saved plan exactly, with no new Claude call."),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Marks open tasks as duplicate, done or expired. A dry run returns a plan_id to apply later."""
    return run_cleanup(db, user, dry_run, plan_id)
