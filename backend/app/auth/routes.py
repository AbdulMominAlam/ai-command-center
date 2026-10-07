from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.accounts import AccountTaken, account_label, linked_accounts, save_account, user_for_sign_in
from app.auth.google import TASKS_SCOPE, USERINFO_URL, build_flow, has_scope
from app.config import settings
from app.db import get_db
from app.models import SyncState, User

router = APIRouter()


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """FastAPI dependency: the signed-in user, or 401."""
    user_id = request.session.get("user_id")
    user = db.get(User, user_id) if user_id else None
    if user is None:
        raise HTTPException(status_code=401, detail="Not signed in")
    return user


def _start_google_flow(request: Request, prompt: str) -> RedirectResponse:
    flow = build_flow()
    # offline + consent: Google returns a refresh token every time, not just the first.
    auth_url, state = flow.authorization_url(access_type="offline", prompt=prompt)
    request.session["oauth_state"] = state
    request.session["oauth_code_verifier"] = flow.code_verifier
    return RedirectResponse(auth_url)


@router.get("/auth/google/login")
def google_login(request: Request):
    """Sends the browser to Google's consent screen to sign in."""
    request.session.pop("oauth_link_user_id", None)
    return _start_google_flow(request, "consent")


@router.get("/auth/google/link")
def google_link(request: Request, user: User = Depends(get_current_user)):
    """Signed in already: adds another Google account to this user instead of signing in.
    select_account makes Google ask which account, instead of reusing the current one."""
    request.session["oauth_link_user_id"] = user.id
    return _start_google_flow(request, "consent select_account")


@router.get("/auth/google/callback")
def google_callback(request: Request, db: Session = Depends(get_db)):
    """Google sends the browser back here with a one-time code."""
    link_user_id = request.session.pop("oauth_link_user_id", None)
    if error := request.query_params.get("error"):
        raise HTTPException(status_code=400, detail=f"Google sign-in failed: {error}")

    state = request.session.pop("oauth_state", None)
    code_verifier = request.session.pop("oauth_code_verifier", None)
    if state is None or state != request.query_params.get("state"):
        raise HTTPException(status_code=400, detail="Invalid OAuth state. Start again at /auth/google/login")

    flow = build_flow(state=state, code_verifier=code_verifier)
    flow.fetch_token(authorization_response=str(request.url))
    creds = flow.credentials

    userinfo = flow.authorized_session().get(USERINFO_URL).json()
    email = userinfo.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="Google did not return an email address")
    scope = flow.oauth2session.token.get("scope")
    scopes = " ".join(scope) if isinstance(scope, list) else scope

    return finish_sign_in(request, db, email, creds.refresh_token, scopes, link_user_id)


def finish_sign_in(request: Request, db: Session, email: str, refresh_token: str | None,
                   scopes: str | None, link_user_id: int | None) -> RedirectResponse:
    """Links the account to the signed-in user (link flow) or signs in as its owner."""
    linking = link_user_id is not None and request.session.get("user_id") == link_user_id
    user = db.get(User, link_user_id) if linking else user_for_sign_in(db, email)
    if user is None:
        raise HTTPException(status_code=400, detail="Sign in again before linking an account.")
    try:
        save_account(db, user, email, refresh_token, scopes)
    except AccountTaken as e:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"{e} Try again.")
    db.commit()

    request.session["user_id"] = user.id
    return RedirectResponse(settings.FRONTEND_URL + ("#/settings" if linking else ""))


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return {"id": user.id, "email": user.email}


@router.get("/accounts")
def list_accounts(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """The user's linked Google accounts and when each last synced Gmail. No tokens."""
    synced = dict(db.execute(
        select(SyncState.account_id, SyncState.last_synced_at)
        .where(SyncState.user_id == user.id, SyncState.source == "gmail")
    ).all())
    return {"accounts": [
        {"id": a.id, "email": a.email, "label": account_label(a.email),
         "linked_at": a.created_at.isoformat() if a.created_at else None,
         "tasks_enabled": has_scope(a, TASKS_SCOPE),  # False until the account re-consents
         "last_synced_at": synced[a.id].isoformat() if synced.get(a.id) else None}
        for a in linked_accounts(db, user)
    ]}
