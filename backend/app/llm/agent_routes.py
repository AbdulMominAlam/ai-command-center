from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.routes import get_current_user
from app.db import get_db
from app.llm.agent import action_view, run_agent
from app.models import PendingAction, Task, User

router = APIRouter()


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=50)


@router.post("/chat")
def chat(body: ChatRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """One chat turn. Send the whole conversation so far, ending with your question."""
    if body.messages[0].role != "user" or body.messages[-1].role != "user":
        raise HTTPException(status_code=400, detail="The conversation must start and end with a user message.")
    return run_agent(db, user, [m.model_dump() for m in body.messages])


def _pending_action(db: Session, user: User, action_id: int) -> PendingAction:
    # Row lock, so two clicks on "confirm" can't create the task twice.
    action = db.scalar(
        select(PendingAction)
        .where(PendingAction.id == action_id, PendingAction.user_id == user.id)
        .with_for_update()
    )
    if action is None:
        raise HTTPException(status_code=404, detail="Action not found.")
    if action.status != "pending":
        raise HTTPException(status_code=409, detail=f"Action is already {action.status}.")
    return action


@router.post("/actions/{action_id}/confirm")
def confirm_action(action_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Carries out a pending action. For create_task, this creates the task."""
    action = _pending_action(db, user, action_id)
    p = action.payload
    task = Task(user_id=user.id, title=p["title"], priority=p["priority"], created_by="agent",
                due_at=datetime.fromisoformat(p["due_at"]) if p["due_at"] else None)
    db.add(task)
    db.flush()
    action.status = "confirmed"
    action.task_id = task.id
    action.resolved_at = datetime.now(timezone.utc)
    db.commit()
    return {**action_view(action), "task_id": task.id}


@router.post("/actions/{action_id}/cancel")
def cancel_action(action_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Drops a pending action without doing anything."""
    action = _pending_action(db, user, action_id)
    action.status = "cancelled"
    action.resolved_at = datetime.now(timezone.utc)
    db.commit()
    return action_view(action)
