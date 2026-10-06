from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.routes import get_current_user
from app.dashboard.today import TZ, build_today, task_view
from app.db import get_db
from app.models import Item, Task, User

router = APIRouter()

TASK_LIST_LIMIT = 200


def now() -> datetime:
    """The current time in Istanbul. A function so tests can freeze it."""
    return datetime.now(TZ)


@router.get("/today")
def today(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Everything the Today page shows, already grouped and sorted."""
    return build_today(db, user, now())


@router.get("/tasks")
def list_tasks(
    status: Literal["open", "done", "expired"] = "open",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Open tasks soonest first; done and expired ones most recent first. Undated go last."""
    order = Task.due_at.asc() if status == "open" else Task.due_at.desc()
    rows = db.execute(
        select(Task, Item.source)
        .outerjoin(Item, Task.item_id == Item.id)
        .where(Task.user_id == user.id, Task.status == status)
        .order_by(order.nulls_last(), Task.id.desc())
        .limit(TASK_LIST_LIMIT)
    ).all()
    return {"tasks": [task_view(t, source) for t, source in rows]}


class TaskUpdate(BaseModel):
    status: Literal["open", "done", "expired"] | None = None
    priority: Literal["low", "medium", "high"] | None = None

    @model_validator(mode="after")
    def at_least_one(self):
        if self.status is None and self.priority is None:
            raise ValueError("Send status, priority or both.")
        return self


@router.patch("/tasks/{task_id}")
def update_task(
    task_id: int,
    body: TaskUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Changes a task's status (e.g. mark it done) and/or priority."""
    row = db.execute(
        select(Task, Item.source)
        .outerjoin(Item, Task.item_id == Item.id)
        .where(Task.id == task_id, Task.user_id == user.id)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    task, source = row
    if body.status is not None:
        task.status = body.status
    if body.priority is not None:
        task.priority = body.priority
    db.commit()
    return task_view(task, source)
