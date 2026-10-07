"""Linked Google accounts (Milestone 9): one user, several Google accounts.

Signing in with any linked account opens the same user. GET /auth/google/link
adds another account to the user who is already signed in.
"""

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.auth.google import encrypt_token
from app.models import CleanupPlan, GoogleAccount, Item, PendingAction, SyncState, Task, UniversityBlockedSender, User

# Accounts on these domains are university accounts: their emails go through the
# course-admin filter in app/llm/extract.py and get the "Sabancı" tag.
UNIVERSITY_DOMAINS = ("sabanciuniv.edu",)


class AccountTaken(Exception):
    """The Google account is already linked to a different user."""


def is_university(email: str | None) -> bool:
    domain = (email or "").rsplit("@", 1)[-1].lower()
    return any(domain == d or domain.endswith("." + d) for d in UNIVERSITY_DOMAINS)


def account_label(email: str | None) -> str | None:
    """The short tag the frontend shows: "Sabancı" or "Personal" (None without an account)."""
    if email is None:
        return None
    return "Sabancı" if is_university(email) else "Personal"


def linked_accounts(db: Session, user: User) -> list[GoogleAccount]:
    """The user's Google accounts, oldest first (the first is the one they signed up with)."""
    return list(db.scalars(
        select(GoogleAccount).where(GoogleAccount.user_id == user.id).order_by(GoogleAccount.id)
    ))


def university_account_ids(db: Session, user_id: int) -> set[int]:
    accounts = db.scalars(select(GoogleAccount).where(GoogleAccount.user_id == user_id))
    return {a.id for a in accounts if is_university(a.email)}


def blocked_addresses(db: Session, user_id: int) -> set[str]:
    """Lowercased addresses the user blocked in Settings > University senders."""
    return set(db.scalars(select(UniversityBlockedSender.address).where(UniversityBlockedSender.user_id == user_id)))


def user_for_sign_in(db: Session, email: str) -> User:
    """The user a Google sign-in opens: the owner of that linked account, else the
    user with that email, else a new user."""
    account = db.scalar(select(GoogleAccount).where(func.lower(GoogleAccount.email) == email.lower()))
    if account is not None:
        return db.get(User, account.user_id)
    user = db.scalar(select(User).where(func.lower(User.email) == email.lower()))
    if user is None:
        user = User(email=email)
        db.add(user)
        db.flush()  # assigns user.id
    return user


def save_account(db: Session, user: User, email: str, refresh_token: str | None,
                 scopes: str | None) -> GoogleAccount:
    """Links a Google account to `user`, or refreshes its token if already linked.

    Raises AccountTaken if another user already has it. Without a refresh token
    (Google only sends one with prompt=consent) an existing account is left as is.
    """
    account = db.scalar(select(GoogleAccount).where(func.lower(GoogleAccount.email) == email.lower()))
    if account is not None and account.user_id != user.id:
        raise AccountTaken(f"{email} is already linked to another user.")
    if account is None:
        if not refresh_token:
            raise ValueError("Google did not return a refresh token for a new account.")
        account = GoogleAccount(user_id=user.id, email=email)
        db.add(account)
    if refresh_token:
        account.encrypted_refresh_token = encrypt_token(refresh_token)
        account.scopes = scopes
    db.flush()
    return account


def merge_users(db: Session, source_id: int, target_id: int) -> int:
    """Moves the Google accounts of user `source_id` to `target_id` and deletes the
    source user. Refuses if the source has any items, tasks or other data, so
    nothing is lost. Returns how many accounts moved. Does not commit."""
    if source_id == target_id:
        raise ValueError("Source and target are the same user.")
    if db.get(User, source_id) is None or db.get(User, target_id) is None:
        raise ValueError("Both users must exist.")
    for model in (Item, Task, SyncState, CleanupPlan, PendingAction, UniversityBlockedSender):
        if db.scalar(select(func.count()).select_from(model).where(model.user_id == source_id)):
            raise ValueError(f"User {source_id} still has {model.__tablename__}; not merging.")
    moved = db.execute(
        update(GoogleAccount).where(GoogleAccount.user_id == source_id).values(user_id=target_id)
    ).rowcount
    db.execute(delete(User).where(User.id == source_id))
    return moved


def main() -> None:
    """uv run python -m app.auth.accounts merge <source_user_id> <target_user_id>"""
    import sys

    from app.db import SessionLocal

    if len(sys.argv) != 4 or sys.argv[1] != "merge":
        sys.exit(main.__doc__)
    source_id, target_id = int(sys.argv[2]), int(sys.argv[3])
    with SessionLocal() as db:
        try:
            moved = merge_users(db, source_id, target_id)
        except ValueError as e:
            sys.exit(str(e))
        db.commit()
    print(f"Moved {moved} Google account(s) from user {source_id} to user {target_id} and deleted user {source_id}.")


if __name__ == "__main__":
    main()
