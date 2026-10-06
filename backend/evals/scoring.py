"""Scores extraction results against your labels. No LLM judge: titles match
by word overlap, and due dates are compared in Europe/Istanbul time.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from evals.dataset import Label

TZ = ZoneInfo("Europe/Istanbul")

# Two titles match when their word overlap (see similarity) is at least this.
MATCH_THRESHOLD = 0.5
TIME_TOLERANCE = timedelta(hours=1)

STOPWORDS = {"a", "an", "the", "to", "for", "of", "on", "in", "at", "by", "and", "or",
             "with", "from", "your", "my", "about", "before", "via"}


def words(title: str) -> set[str]:
    """Lowercase words without stopwords, with a plural "s" dropped ("forms" = "form")."""
    out = set()
    for w in re.findall(r"[a-z0-9]+", title.lower()):
        if w in STOPWORDS:
            continue
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.add(w)
    return out


def similarity(a: str, b: str) -> float:
    """Word-overlap F1: 2 * shared words / (words in a + words in b), from 0 to 1.
    "Submit CS204 homework 2" vs "Submit CS204 HW 2" shares 3 of 4 + 4 words = 0.75."""
    wa, wb = words(a), words(b)
    if not wa or not wb:
        return 0.0
    return 2 * len(wa & wb) / (len(wa) + len(wb))


def match_tasks(expected: list[str], predicted: list[str]) -> list[tuple[int, int]]:
    """Pairs expected and predicted titles one to one, best overlap first.
    Returns (expected index, predicted index) pairs that pass MATCH_THRESHOLD."""
    scored = sorted(
        ((similarity(e, p), i, j) for i, e in enumerate(expected) for j, p in enumerate(predicted)),
        reverse=True,
    )
    used_e, used_p, pairs = set(), set(), []
    for score, i, j in scored:
        if score < MATCH_THRESHOLD:
            break
        if i not in used_e and j not in used_p:
            used_e.add(i)
            used_p.add(j)
            pairs.append((i, j))
    return sorted(pairs)


@dataclass
class Predicted:
    is_actionable: bool
    tasks: list[tuple[str, datetime | None]]  # (title, due_at)


@dataclass
class EmailResult:
    id: int
    subject: str
    expected: Label
    predicted: Predicted | None  # None when the extraction call failed
    error: str | None = None


@dataclass
class Scores:
    emails: int = 0
    failed: int = 0
    actionable_correct: int = 0
    # actionable confusion: true/false positives/negatives
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0
    expected_tasks: int = 0
    predicted_tasks: int = 0
    matched_tasks: int = 0
    date_checked: int = 0
    date_correct: int = 0
    time_checked: int = 0
    time_correct: int = 0
    mistakes: list[tuple[str, list[str]]] = field(default_factory=list)  # (subject, problems)


def ratio(n: int, d: int) -> float | None:
    return n / d if d else None


def _day(d) -> str:
    return d.strftime("%a %d %b")


def score(results: list[EmailResult]) -> Scores:
    s = Scores()
    for r in results:
        s.emails += 1
        problems: list[str] = []
        exp = r.expected
        pred = r.predicted
        if pred is None:
            s.failed += 1
            problems.append(f"extraction failed ({r.error})")
            pred = Predicted(is_actionable=False, tasks=[])  # a failure counts as "found nothing"

        # Actionable or not
        if pred.is_actionable == exp.is_actionable:
            s.actionable_correct += 1
        elif r.predicted is not None:
            problems.append(f"actionable: expected {'yes' if exp.is_actionable else 'no'}, "
                            f"got {'yes' if pred.is_actionable else 'no'}")
        s.tp += exp.is_actionable and pred.is_actionable
        s.fp += (not exp.is_actionable) and pred.is_actionable
        s.fn += exp.is_actionable and not pred.is_actionable
        s.tn += (not exp.is_actionable) and not pred.is_actionable

        # Tasks: the app ignores tasks on a non-actionable extraction, so the eval does too.
        pred_tasks = pred.tasks if pred.is_actionable else []
        pairs = match_tasks([t.title for t in exp.tasks], [t[0] for t in pred_tasks])
        s.expected_tasks += len(exp.tasks)
        s.predicted_tasks += len(pred_tasks)
        s.matched_tasks += len(pairs)
        if missed := len(exp.tasks) - len(pairs):
            problems.append(f"missed {missed} of {len(exp.tasks)} task(s)")
        if extra := len(pred_tasks) - len(pairs):
            problems.append(f"{extra} extra task(s)")

        # Due dates of matched tasks
        for i, j in pairs:
            want, (_, got) = exp.tasks[i], pred_tasks[j]
            got_local = got.astimezone(TZ) if got else None
            s.date_checked += 1
            if want.due_date is None and got_local is None:
                s.date_correct += 1
            elif want.due_date and got_local and got_local.date() == want.due_date:
                s.date_correct += 1
            else:
                problems.append(f"due date: expected {_day(want.due_date) if want.due_date else 'none'}, "
                                f"got {_day(got_local) if got_local else 'none'}")
            if want.due_time is not None:
                s.time_checked += 1
                want_dt = datetime.combine(want.due_date, want.due_time, TZ)
                if got_local and abs(got_local - want_dt) <= TIME_TOLERANCE:
                    s.time_correct += 1
                else:
                    problems.append(f"due time: expected {want_dt:%a %H:%M}, "
                                    f"got {f'{got_local:%a %H:%M}' if got_local else 'none'}")

        if problems:
            s.mistakes.append((r.subject, problems))
    return s
