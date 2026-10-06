"""Runs extraction on every labeled email and writes a Markdown report.

Run from backend/:  uv run python -m evals.run [--file evals/sample.jsonl] [--name sample]

Each email is extracted as if it were read the moment it was sent ("today" is
its sent time) with no open tasks, so the input never depends on the date you
run it or on what is in your database. Nothing is written to the database.
The report in evals/results/ lists subjects of mistakes, never bodies.
"""

import argparse
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import anthropic

from app.config import settings
from app.llm.extract import PROMPT_VERSION, SAVE_TOOL, SYSTEM_PROMPT, ExtractionFailed, extract
from app.llm.pricing import estimate_cost
from app.models import Item
from evals.dataset import EMAILS_FILE, RESULTS_DIR, Label, load
from evals.scoring import (MATCH_THRESHOLD, TZ, EmailResult, Predicted, Scores,
                           ratio, score)


def prompt_hash() -> str:
    """Short fingerprint of the prompt and tool schema, in case PROMPT_VERSION wasn't bumped."""
    text = SYSTEM_PROMPT + json.dumps(SAVE_TOOL, sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()[:8]


def run_one(row: dict) -> tuple[EmailResult, int, int]:
    """Extracts one email. Returns (result, input_tokens, output_tokens)."""
    sent = datetime.fromisoformat(row["sent_at"]) if row.get("sent_at") else datetime.now(TZ)
    item = Item(title=row["subject"], body=row.get("body"), sender=row.get("sender"), occurred_at=sent)
    expected = Label.model_validate(row["expected"])
    try:
        extraction, inp, out = extract(item, today=sent, tasks=[])
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
        raise  # a bad key fails every email; stop instead of scoring zeros
    except ExtractionFailed as e:
        # Only the kind of failure, since the message can quote the model's reply.
        return EmailResult(row["id"], row["subject"], expected, None, str(e).split(":")[0]), e.input_tokens, e.output_tokens
    except Exception as e:
        return EmailResult(row["id"], row["subject"], expected, None, type(e).__name__), 0, 0
    predicted = Predicted(extraction.is_actionable, [(t.title, t.due_at) for t in extraction.tasks])
    return EmailResult(row["id"], row["subject"], expected, predicted), inp, out


def pct(n: int, d: int) -> str:
    r = ratio(n, d)
    return "n/a" if r is None else f"{r:.0%} ({n}/{d})"


def one_line(text: str) -> str:
    """A subject safe for a Markdown list item."""
    return " ".join((text or "(no subject)").split()).replace("|", "\\|").replace("`", "'")


def report(s: Scores, source: str, started: datetime, cost: float | None, tokens: tuple[int, int]) -> str:
    cost_text = f"${cost:.4f}" if cost is not None else "unknown (model missing from app/llm/pricing.py)"
    lines = [
        f"# Extraction eval, {started:%Y-%m-%d %H:%M} (Istanbul)",
        "",
        f"- Model: `{settings.EXTRACT_MODEL}`",
        f"- Prompt: {PROMPT_VERSION} (sha `{prompt_hash()}`)",
        f"- Eval set: `{source}`, {s.emails} labeled emails, {s.failed} failed calls",
        f"- Cost: {cost_text}, {tokens[0]:,} input + {tokens[1]:,} output tokens",
        "",
        "## Metrics",
        "",
        "| Metric | Score |",
        "| --- | --- |",
        f"| Actionable accuracy | {pct(s.actionable_correct, s.emails)} |",
        f"| Actionable precision (said yes, was yes) | {pct(s.tp, s.tp + s.fp)} |",
        f"| Actionable recall (was yes, said yes) | {pct(s.tp, s.tp + s.fn)} |",
        f"| Task recall (expected tasks found) | {pct(s.matched_tasks, s.expected_tasks)} |",
        f"| Task precision (predicted tasks that were right) | {pct(s.matched_tasks, s.predicted_tasks)} |",
        f"| Due date, same day (matched tasks) | {pct(s.date_correct, s.date_checked)} |",
        f"| Due time within 1 hour (tasks you gave a time) | {pct(s.time_correct, s.time_checked)} |",
        "",
        f"Actionable confusion: {s.tp} true yes, {s.tn} true no, {s.fp} false yes, {s.fn} false no.",
        "",
        f"Titles match when their word overlap (F1 over words, stopwords removed) is at least "
        f"{MATCH_THRESHOLD}. A failed call counts as \"not actionable, no tasks\".",
        "",
        f"## Mistakes ({len(s.mistakes)} emails)",
        "",
    ]
    if not s.mistakes:
        lines.append("None.")
    for subject, problems in s.mistakes:
        lines.append(f"- **{one_line(subject)}**: {'; '.join(problems)}")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file", type=Path, default=EMAILS_FILE, help="jsonl eval set (default: evals/emails.jsonl)")
    parser.add_argument("--name", default="", help="Added to the report file name, e.g. 'sample'")
    parser.add_argument("--workers", type=int, default=4, help="Parallel Claude calls")
    args = parser.parse_args()

    if not args.file.exists():
        sys.exit(f"{args.file} not found. Run: uv run python -m evals.export")
    rows = [r for r in load(args.file) if r.get("expected")]
    if not rows:
        sys.exit(f"No labeled emails in {args.file}. Label some at http://localhost:5173/#/label")

    started = datetime.now(TZ)
    print(f"Extracting {len(rows)} labeled emails with {settings.EXTRACT_MODEL} (prompt {PROMPT_VERSION})...")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        outcomes = list(pool.map(run_one, rows))

    results = [o[0] for o in outcomes]
    tokens = (sum(o[1] for o in outcomes), sum(o[2] for o in outcomes))
    cost = estimate_cost(settings.EXTRACT_MODEL, *tokens)
    s = score(results)

    RESULTS_DIR.mkdir(exist_ok=True)
    suffix = f"_{args.name}" if args.name else ""
    path = RESULTS_DIR / f"{started:%Y-%m-%d_%H%M}_{PROMPT_VERSION}_{settings.EXTRACT_MODEL}{suffix}.md"
    try:
        source = str(args.file.resolve().relative_to(Path.cwd()))
    except ValueError:
        source = args.file.name
    path.write_text(report(s, source, started, cost, tokens), encoding="utf-8")

    print(f"Actionable accuracy {pct(s.actionable_correct, s.emails)}, "
          f"task recall {pct(s.matched_tasks, s.expected_tasks)}, "
          f"task precision {pct(s.matched_tasks, s.predicted_tasks)}, "
          f"same-day due {pct(s.date_correct, s.date_checked)}, "
          f"time within 1h {pct(s.time_correct, s.time_checked)}")
    print(f"{len(s.mistakes)} emails with mistakes, {s.failed} failed calls, cost "
          f"{f'${cost:.4f}' if cost is not None else 'unknown'}")
    print(f"Report: {path.relative_to(Path.cwd()) if path.is_relative_to(Path.cwd()) else path}")


if __name__ == "__main__":
    main()
