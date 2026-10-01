import time

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.routes import get_current_user
from app.db import get_db
from app.models import User
from app.sync.gmail import sync_gmail

router = APIRouter(prefix="/sync")


@router.post("/gmail")
def sync_gmail_endpoint(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Pulls new emails from Gmail into items (first 30 days on the first run)."""
    start = time.perf_counter()
    added = sync_gmail(db, user)
    return {"added": added, "elapsed_seconds": round(time.perf_counter() - start, 2)}
