from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from app.auth.google import GoogleReconnectRequired
from app.auth.routes import router as auth_router
from app.config import settings
from app.db import get_db
from app.llm.agent_routes import router as agent_router
from app.llm.client import client
from app.llm.routes import router as extract_router
from app.models import LLMUsage
from app.sync.routes import router as sync_router

app = FastAPI(title="AI Personal Command Center")

if not settings.SESSION_SECRET:
    raise RuntimeError("SESSION_SECRET is not set in backend/.env")

# Signed cookie that remembers the OAuth state during login and the user id after it.
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SESSION_SECRET,
    https_only=not settings.GOOGLE_REDIRECT_URI.startswith("http://"),
)
app.include_router(auth_router)
app.include_router(sync_router)
app.include_router(extract_router)
app.include_router(agent_router)


@app.exception_handler(GoogleReconnectRequired)
def google_reconnect_required(request: Request, exc: GoogleReconnectRequired):
    return JSONResponse(
        status_code=401,
        content={"detail": str(exc), "reconnect_url": "/auth/google/login"},
    )


@app.get("/health")
def health(db: Session = Depends(get_db)):
    """Checks that the API is up and the database answers."""
    db.execute(text("select 1"))
    return {"status": "ok", "database": "ok"}


@app.get("/health/llm")
def health_llm(db: Session = Depends(get_db)):
    """Makes one tiny Claude call (a fraction of a cent) and logs its token usage."""
    msg = client.messages.create(
        model=settings.EXTRACT_MODEL,
        max_tokens=20,
        messages=[{"role": "user", "content": "Reply with just the word: ok"}],
    )
    db.add(
        LLMUsage(
            purpose="health",
            model=settings.EXTRACT_MODEL,
            input_tokens=msg.usage.input_tokens,
            output_tokens=msg.usage.output_tokens,
        )
    )
    db.commit()
    return {
        "reply": msg.content[0].text,
        "model": settings.EXTRACT_MODEL,
        "input_tokens": msg.usage.input_tokens,
        "output_tokens": msg.usage.output_tokens,
    }
