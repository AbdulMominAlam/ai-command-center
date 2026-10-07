"""One-time Gmail backfill: imports emails 30 to 60 days old, then extracts them with a cost cap.

Run from backend/:  uv run python -m app.sync.backfill [--max-cost 0.60]

Uses the same filters as the normal sync (promotions and social left out by the
Gmail query; sensitive and noise emails saved but never sent to Claude), and
never touches the stored historyId. Extraction uses prompt caching like the
app does, and stops before the estimated cost would pass --max-cost.
Holds the sync lock, so it never runs at the same time as a sync in the API.
Prints counts and cost only, never subjects, bodies or task titles.
"""

import argparse
import sys

import anthropic
from sqlalchemy import func, select

from app.auth.accounts import linked_accounts
from app.auth.google import GoogleReconnectRequired
from app.db import SessionLocal
from app.llm.client import CreditTooLow
from app.llm.extract import process_unprocessed
from app.models import GoogleAccount, Item, User
from app.sync.gmail import BACKFILL_QUERY, backfill_gmail
from app.sync.runner import SyncAlreadyRunning, exclusive

COUNT_KEYS = ("processed", "skipped_sensitive", "skipped_noise", "skipped_blocked_sender", "skipped_course_admin", "failed", "tasks_created",
              "tasks_resolved", "duplicates_skipped", "input_tokens", "output_tokens",
              "cache_creation_tokens", "cache_read_tokens")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--max-cost", type=float, default=0.60, help="Stop extraction before passing this many USD")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        users = db.scalars(
            select(User).where(User.id.in_(select(GoogleAccount.user_id))).order_by(User.id)
        ).all()
        if not users:
            sys.exit("No user with a Google connection.")
        with exclusive():
            for user in users:
                run_for(db, user, args.max_cost)
    except SyncAlreadyRunning as e:
        sys.exit(str(e))
    finally:
        db.close()


def run_for(db, user: User, max_cost: float) -> None:
    print(f"User {user.id}: listing Gmail with '{BACKFILL_QUERY}' (throttled, a few minutes)...")
    for account in linked_accounts(db, user):
        try:
            gmail = backfill_gmail(db, user, account)
        except GoogleReconnectRequired as e:
            print(f"  account {account.id} skipped: {e}")
            continue
        print(f"  account {account.id}: emails listed: {gmail['listed']}, new: {gmail['added']}")

    try:
        stats = process_unprocessed(db, user, limit=10_000, max_cost_usd=max_cost)
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError, CreditTooLow) as e:
        db.rollback()
        print(f"  extraction stopped: {type(e).__name__}")
        return

    for key in COUNT_KEYS:
        print(f"  {key:22} {stats[key]:,}")
    cost = stats["estimated_cost_usd"]
    print(f"  {'estimated_cost_usd':22} {f'${cost:.4f}' if cost is not None else 'unknown'}")
    if stats["stopped_at_cost_cap"]:
        print(f"  stopped before passing ${max_cost:.2f}")
    left = db.scalar(select(func.count()).select_from(Item).where(
        Item.user_id == user.id, Item.type == "email", Item.processed.is_(False)))
    print(f"  unprocessed emails left: {left}")


if __name__ == "__main__":
    main()
