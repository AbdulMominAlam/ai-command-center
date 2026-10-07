"""Runs extraction on every labeled email and writes a Markdown report.

Run from backend/:  uv run python -m evals.run [--set tuning|test] [--file evals/sample.jsonl] [--name note]

--set tuning (default) runs evals/emails.jsonl, the set you improve the prompt
on. --set test runs the held-out evals/test_emails.jsonl: only run it on a
final prompt version, and never change the prompt because of its mistakes.

Each email is extracted as if it were read the moment it was sent ("today" is
its sent time) with no open tasks, so the input never depends on the date you
run it or on what is in your database. Nothing is written to the database.
The report in evals/results/ lists subjects of mistakes, never bodies.
"""

import argparse
import hashlib
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import anthropic

from app.config import settings
from app.llm.extract import (PROMPT_VERSION, SAVE_TOOL, SYSTEM_PROMPT, ExtractionFailed, extract,
                             is_noise, is_sensitive)
from app.llm.client import CreditTooLow
from app.llm.pricing import Usage, estimate_cost
from app.models import Item
from evals.dataset import RESULTS_DIR, SETS, Label, load
from evals.scoring import (MATCH_THRESHOLD, TZ, EmailResult, Predicted, Scores,
                           ratio, score)


def prompt_hash() -> str:
    """Short fingerprint of the prompt and tool schema, in case PROMPT_VERSION wasn't bumped."""
    text = SYSTEM_PROMPT + json.dumps(SAVE_TOOL, sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()[:8]


def run_one(row: dict) -> tuple[EmailResult, Usage]:
    """Extracts one email. Returns (result, usage)."""
    sent = datetime.fromisoformat(row["sent_at"]) if row.get("sent_at") else datetime.now(TZ)
    item = Item(title=row["subject"], body=row.get("body"), sender=row.get("sender"), occurred_at=sent)
    expected = Label.model_validate(row["expected"])
    try:
        extraction, usage = extract(item, today=sent, tasks=[])
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError, CreditTooLow):
        raise  # a bad key or no credit fails every email; stop instead of scoring zeros
    except ExtractionFailed as e:
        # Only the kind of failure, since the message can quote the model's reply.
        return EmailResult(row["id"], row["subject"], expected, None, str(e).split(":")[0]), e.usage
    except Exception as e:
        return EmailResult(row["id"], row["subject"], expected, None, type(e).__name__), Usage()
    predicted = Predicted(extraction.is_actionable, [(t.title, t.due_at) for t in extraction.tasks])
    return EmailResult(row["id"], row["subject"], expected, predicted), usage


def pct(n: int, d: int) -> str:
    r = ratio(n, d)
    return "n/a" if r is None else f"{r:.0%} ({n}/{d})"


def one_line(text: str) -> str:
    """A subject safe for a committed Markdown report: one line, with email
    addresses and long numbers (account, transaction or profile ids) masked."""
    text = " ".join((text or "(no subject)").split())
    text = re.sub(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", "[email]", text)
    text = re.sub(r"\d{5,}", "•••", text)
    return text.replace("|", "\\|").replace("`", "'")


def report(s: Scores, source: str, started: datetime, cost: float | None, tokens: Usage,
           filtered: int = 0, set_name: str = "tuning") -> str:
    cost_text = f"${cost:.4f}" if cost is not None else "unknown (model missing from app/llm/pricing.py)"
    lines = [
        f"# Extraction eval, {set_name} set, {started:%Y-%m-%d %H:%M} (Istanbul)",
        "",
        f"- Model: `{settings.EXTRACT_MODEL}`",
        f"- Prompt: {PROMPT_VERSION} (sha `{prompt_hash()}`)",
        f"- Eval set: `{source}`, {s.emails} labeled emails, {s.failed} failed calls"
        + (f", {filtered} skipped as sensitive or noise" if filtered else ""),
        f"- Cost: {cost_text}, {tokens.input_tokens:,} input + {tokens.output_tokens:,} output tokens"
        + (f" (cache: {tokens.cache_creation_tokens:,} written, {tokens.cache_read_tokens:,} read)"
           if tokens.cache_creation_tokens or tokens.cache_read_tokens else ""),
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
    parser.add_argument("--set", choices=sorted(SETS), default="tuning", dest="eval_set",
                        help="tuning (default) or the held-out test set")
    parser.add_argument("--file", type=Path, help="Any other jsonl eval set; its file name becomes the set name")
    parser.add_argument("--name", default="", help="Extra note for the report file name, e.g. 'baseline'")
    parser.add_argument("--workers", type=int, default=4, help="Parallel Claude calls")
    args = parser.parse_args()
    file = args.file or SETS[args.eval_set]
    set_name = file.stem if args.file else args.eval_set

    if not file.exists():
        sys.exit(f"{file} not found. Run: uv run python -m evals.export --set {args.eval_set}")
    rows = [r for r in load(file) if r.get("expected")]
    # Like the app, never send sensitive or noise emails to Claude, even if the
    # patterns were added after the eval set was exported.
    kept = [r for r in rows if not is_sensitive(r["subject"], r.get("sender")) and not is_noise(r.get("sender"))]
    filtered, rows = len(rows) - len(kept), kept
    if not rows:
        sys.exit(f"No labeled emails in {file}. Label some at http://localhost:5173/#/label"
                 + ("?set=test" if set_name == "test" else ""))

    started = datetime.now(TZ)
    print(f"Extracting {len(rows)} labeled emails from the {set_name} set with {settings.EXTRACT_MODEL} (prompt {PROMPT_VERSION})"
          f"{f', {filtered} skipped as sensitive or noise' if filtered else ''}...")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        outcomes = list(pool.map(run_one, rows))

    results = [o[0] for o in outcomes]
    tokens = Usage()
    for _, usage in outcomes:
        tokens = tokens.plus(usage)
    cost = estimate_cost(settings.EXTRACT_MODEL, *tokens)
    s = score(results)

    RESULTS_DIR.mkdir(exist_ok=True)
    suffix = f"_{args.name}" if args.name else ""
    path = RESULTS_DIR / f"{started:%Y-%m-%d_%H%M}_{PROMPT_VERSION}_{settings.EXTRACT_MODEL}_{set_name}{suffix}.md"
    try:
        source = str(file.resolve().relative_to(Path.cwd()))
    except ValueError:
        source = file.name
    path.write_text(report(s, source, started, cost, tokens, filtered, set_name), encoding="utf-8")

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
