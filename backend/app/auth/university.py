"""Settings > University senders: who emails your university account, and blocking them.

Your university account's emails go to Claude by default. Blocking a sender
stops their future emails from reaching Claude (extraction marks them
"Skipped: blocked sender"), hides their emails from the chat agent, and marks
their open tasks as done. This list is only ever returned to the frontend;
it is never part of a Claude prompt.
"""

import re
from email.utils import parseaddr

from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.auth.accounts import blocked_addresses, university_account_ids
from app.auth.routes import get_current_user
from app.db import get_db
from app.llm.extract import sender_address
from app.models import Item, Task, UniversityBlockedSender, User

ADDRESS_RE = re.compile(r"^[^@\s]+@[^@\s]+$")

router = APIRouter(prefix="/university")


class SenderAddress(BaseModel):
    address: str

    @field_validator("address")
    @classmethod
    def normalize(cls, v: str) -> str:
        v = v.strip().lower()
        if len(v) > 320 or not ADDRESS_RE.match(v):
            raise ValueError("Not an email address.")
        return v


def university_senders(db: Session, user: User) -> list[dict]:
    """Everyone who emailed the user's university accounts: address, display name
    (from their latest email), email count, latest date and whether they're blocked.
    Most emails first. No subjects or bodies."""
    university = university_account_ids(db, user.id)
    blocked = blocked_addresses(db, user.id)
    senders: dict[str, dict] = {}
    if university:
        rows = db.execute(
            select(Item.sender, Item.occurred_at)
            .where(Item.user_id == user.id, Item.type == "email", Item.account_id.in_(university))
            .order_by(Item.occurred_at.asc().nulls_first())
        ).all()
        for sender, when in rows:
            name, address = parseaddr(sender or "")
            address = address.lower()
            if not address:
                continue
            entry = senders.setdefault(address, {"address": address, "name": None, "email_count": 0,
                                                 "last_email_at": None})
            entry["email_count"] += 1
            entry["name"] = name or entry["name"]  # rows are oldest first, so the latest name wins
            if when is not None:
                entry["last_email_at"] = when
    for address in blocked - senders.keys():  # still listed, so it can be unblocked
        senders[address] = {"address": address, "name": None, "email_count": 0, "last_email_at": None}
    result = sorted(senders.values(), key=lambda s: (-s["email_count"], s["address"]))
    for s in result:
        s["blocked"] = s["address"] in blocked
        s["last_email_at"] = s["last_email_at"].isoformat() if s["last_email_at"] else None
    return result


def block_sender(db: Session, user: User, address: str) -> int:
    """Blocks a sender and marks the open tasks from their university emails as done.
    Returns how many tasks were closed. Does not commit."""
    db.execute(insert(UniversityBlockedSender).values(user_id=user.id, address=address)
               .on_conflict_do_nothing(index_elements=["user_id", "address"]))
    university = university_account_ids(db, user.id)
    if not university:
        return 0
    rows = db.execute(
        select(Task, Item.sender).join(Item, Task.item_id == Item.id)
        .where(Task.user_id == user.id, Task.status == "open", Item.type == "email",
               Item.account_id.in_(university))
    ).all()
    closed = 0
    for task, sender in rows:
        if sender_address(sender) == address:
            task.status = "done"
            closed += 1
    return closed


@router.get("/senders")
def list_senders(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return {"senders": university_senders(db, user)}


@router.post("/senders/block")
def block(body: SenderAddress, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Blocks a sender. Their open tasks are marked done; their emails stay in the
    database but never reach Claude again."""
    closed = block_sender(db, user, body.address)
    db.commit()
    return {"address": body.address, "blocked": True, "tasks_closed": closed}


@router.post("/senders/unblock")
def unblock(body: SenderAddress, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Unblocks a sender. Their future emails go to Claude again; tasks closed when
    blocking stay done, and emails skipped meanwhile are not re-read."""
    db.query(UniversityBlockedSender).filter_by(user_id=user.id, address=body.address).delete()
    db.commit()
    return {"address": body.address, "blocked": False}
