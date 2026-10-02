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
    dry_run: bool = Query(False, description="Show the decisions without changing any task."),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """One Claude call over the open tasks: marks repeats "duplicate" and finished ones "done"."""
    return run_cleanup(db, user, dry_run)
