from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.llm.client import client
from app.models import LLMUsage

app = FastAPI(title="AI Personal Command Center")


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
