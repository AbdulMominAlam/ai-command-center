from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.auth.routes import get_current_user
from app.db import get_db
from app.llm.cleanup import run_cleanup
from app.llm.extract import process_unprocessed
from app.models import User

router = APIRouter(prefix="/extract")


@router.post("/run")
def run_extraction(
    limit: int = Query(10, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Runs Claude over the oldest unprocessed emails and saves the tasks it finds."""
    return process_unprocessed(db, user, limit)


@router.post("/cleanup")
def cleanup_tasks(
    dry_run: bool = Query(False, description="Save the decisions as a plan without changing any task."),
    plan_id: int | None = Query(None, description="Apply a saved plan exactly, with no new Claude call."),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Marks open tasks as duplicate, done or expired. A dry run returns a plan_id to apply later."""
    return run_cleanup(db, user, dry_run, plan_id)
