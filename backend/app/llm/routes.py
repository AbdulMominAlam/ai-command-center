from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.auth.routes import get_current_user
from app.db import get_db
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
