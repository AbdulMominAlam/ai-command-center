from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.google import USERINFO_URL, build_flow, encrypt_token
from app.config import settings
from app.db import get_db
from app.models import OAuthToken, User

router = APIRouter()


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """FastAPI dependency: the signed-in user, or 401."""
    user_id = request.session.get("user_id")
    user = db.get(User, user_id) if user_id else None
    if user is None:
        raise HTTPException(status_code=401, detail="Not signed in")
    return user


@router.get("/auth/google/login")
def google_login(request: Request):
    """Sends the browser to Google's consent screen."""
    flow = build_flow()
    # offline + consent: Google returns a refresh token every time, not just the first.
    auth_url, state = flow.authorization_url(access_type="offline", prompt="consent")
    request.session["oauth_state"] = state
    request.session["oauth_code_verifier"] = flow.code_verifier
    return RedirectResponse(auth_url)


@router.get("/auth/google/callback")
def google_callback(request: Request, db: Session = Depends(get_db)):
    """Google sends the browser back here with a one-time code."""
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

    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email)
        db.add(user)
        db.flush()  # assigns user.id

    if creds.refresh_token:
        scope = flow.oauth2session.token.get("scope")
        scopes = " ".join(scope) if isinstance(scope, list) else scope
        token = db.scalar(
            select(OAuthToken).where(OAuthToken.user_id == user.id, OAuthToken.provider == "google")
        )
        if token is None:
            token = OAuthToken(user_id=user.id, provider="google")
            db.add(token)
        token.encrypted_refresh_token = encrypt_token(creds.refresh_token)
        token.scopes = scopes
    db.commit()

    request.session["user_id"] = user.id
    return RedirectResponse(settings.FRONTEND_URL)


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return {"id": user.id, "email": user.email}
