"""Dev-only endpoints for labeling the extraction eval set (Milestone 8).

They read and write an eval set file on this machine: evals/emails.jsonl
(?set=tuning, the default) or evals/test_emails.jsonl (?set=test). Only registered when
DEV_ENDPOINTS_ENABLED is true, only answer requests from localhost, and need
the usual sign-in. They never return the model's summary, tasks or the
export tags (which say whether the model found tasks), so labels stay unbiased.
"""

import threading
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth.routes import get_current_user
from evals.dataset import SETS, Label, load, save

EvalSet = Literal["tuning", "test"]

LOOPBACK = {"127.0.0.1", "::1", "localhost"}
_write_lock = threading.Lock()  # two quick saves must not interleave


def local_only(request: Request) -> None:
    if request.client is None or request.client.host not in LOOPBACK:
        raise HTTPException(status_code=403, detail="Labeling is only available from localhost")


router = APIRouter(prefix="/evals", dependencies=[Depends(local_only), Depends(get_current_user)])


def _rows(eval_set: EvalSet) -> list[dict]:
    path = SETS[eval_set]
    if not path.exists():
        raise HTTPException(status_code=404,
                            detail=f"No {eval_set} set yet. Run: uv run python -m evals.export --set {eval_set}")
    return load(path)


@router.get("/emails")
def list_emails(set: EvalSet = "tuning"):
    """Every email in the eval set with your label so far (null if unlabeled)."""
    keys = ("id", "sent_at", "sender", "subject", "body", "expected")
    return {"set": set, "emails": [{k: r.get(k) for k in keys} for r in _rows(set)]}


def _set_label(eval_set: EvalSet, email_id: int, expected: dict | None) -> dict:
    with _write_lock:
        rows = _rows(eval_set)
        row = next((r for r in rows if r["id"] == email_id), None)
        if row is None:
            raise HTTPException(status_code=404, detail=f"Email not in the {eval_set} set")
        row["expected"] = expected
        save(rows, SETS[eval_set])
    return {"id": email_id, "expected": expected}


@router.put("/emails/{email_id}/label")
def save_label(email_id: int, label: Label, set: EvalSet = "tuning"):
    """Saves your label for one email."""
    return _set_label(set, email_id, label.model_dump(mode="json"))


@router.delete("/emails/{email_id}/label")
def clear_label(email_id: int, set: EvalSet = "tuning"):
    """Marks one email as unlabeled again."""
    return _set_label(set, email_id, None)
