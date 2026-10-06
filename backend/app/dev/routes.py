"""Dev-only endpoints for labeling the extraction eval set (Milestone 8).

They read and write evals/emails.jsonl on this machine. Only registered when
DEV_ENDPOINTS_ENABLED is true, only answer requests from localhost, and need
the usual sign-in. They never return the model's summary, tasks or the
export tags (which say whether the model found tasks), so labels stay unbiased.
"""

import threading

from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth.routes import get_current_user
from evals.dataset import EMAILS_FILE, Label, load, save

LOOPBACK = {"127.0.0.1", "::1", "localhost"}
_write_lock = threading.Lock()  # two quick saves must not interleave


def local_only(request: Request) -> None:
    if request.client is None or request.client.host not in LOOPBACK:
        raise HTTPException(status_code=403, detail="Labeling is only available from localhost")


router = APIRouter(prefix="/evals", dependencies=[Depends(local_only), Depends(get_current_user)])


def _rows() -> list[dict]:
    if not EMAILS_FILE.exists():
        raise HTTPException(status_code=404, detail="No eval set yet. Run: uv run python -m evals.export")
    return load()


@router.get("/emails")
def list_emails():
    """Every email in the eval set with your label so far (null if unlabeled)."""
    keys = ("id", "sent_at", "sender", "subject", "body", "expected")
    return {"emails": [{k: r.get(k) for k in keys} for r in _rows()]}


def _set_label(email_id: int, expected: dict | None) -> dict:
    with _write_lock:
        rows = _rows()
        row = next((r for r in rows if r["id"] == email_id), None)
        if row is None:
            raise HTTPException(status_code=404, detail="Email not in the eval set")
        row["expected"] = expected
        save(rows)
    return {"id": email_id, "expected": expected}


@router.put("/emails/{email_id}/label")
def save_label(email_id: int, label: Label):
    """Saves your label for one email."""
    return _set_label(email_id, label.model_dump(mode="json"))


@router.delete("/emails/{email_id}/label")
def clear_label(email_id: int):
    """Marks one email as unlabeled again."""
    return _set_label(email_id, None)
