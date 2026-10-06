"""The eval set file: one email per line in evals/emails.jsonl.

Each line is {"id", "sent_at", "sender", "subject", "body", "tags", "expected"}.
"expected" is null until you label the email, then a Label (see below).
The export script, the labeling endpoints and the runner all read and write
the file through this module, so they agree on its shape.
"""

import json
import os
from datetime import date, time
from pathlib import Path

from pydantic import BaseModel, Field, field_validator, model_validator

EVALS_DIR = Path(__file__).resolve().parent
EMAILS_FILE = EVALS_DIR / "emails.jsonl"  # your real emails: gitignored, never commit
SAMPLE_FILE = EVALS_DIR / "sample.jsonl"  # made-up emails, safe to commit
RESULTS_DIR = EVALS_DIR / "results"


class LabelTask(BaseModel):
    title: str = Field(min_length=1)
    due_date: date | None = None  # Europe/Istanbul calendar day
    due_time: time | None = None  # only when the email gives a time

    @field_validator("title")
    @classmethod
    def strip_title(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("title is empty")
        return v

    @model_validator(mode="after")
    def time_needs_date(self):
        if self.due_time is not None and self.due_date is None:
            raise ValueError("a due time needs a due date")
        return self


class Label(BaseModel):
    """What the right extraction is, written by you on the labeling page."""

    is_actionable: bool
    tasks: list[LabelTask] = []

    @model_validator(mode="after")
    def no_tasks_unless_actionable(self):
        if not self.is_actionable and self.tasks:
            raise ValueError("a non-actionable email has no tasks")
        return self


def load(path: Path = EMAILS_FILE) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def save(rows: list[dict], path: Path = EMAILS_FILE) -> None:
    """Writes to a temp file first, so a crash mid-write never leaves half a file."""
    tmp = path.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(tmp, path)
