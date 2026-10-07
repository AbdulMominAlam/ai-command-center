"""Picks a varied sample of processed emails and writes them to an eval set file.

Run from backend/:  uv run python -m evals.export [--set tuning|test] [--n N] [--seed 8] [--force]

--set tuning (default) writes 60 emails to evals/emails.jsonl, --set test writes
25 to evals/test_emails.jsonl. Each set leaves out every email in the other,
so the held-out test set never contains an email you tuned the prompt on.

Each email gets tags from simple patterns (university, internship, newsletter,
relative dates, ...) and the sample takes turns drawing from each tag, so no
single kind of email fills it up. Emails that produced tasks are drawn twice
per turn, because they are rare and the eval needs actionable examples.

Sensitive and noise emails are left out (they never reach Claude anyway), and
the model's summary and tasks are not exported, so labels stay unbiased.
Prints counts only, never subjects or bodies.
"""

import argparse
import random
import re
import sys

from sqlalchemy import select

from app.db import SessionLocal
from app.llm.extract import NOISE_SUMMARY, SKIPPED_SUMMARY, is_noise, is_sensitive
from app.models import Item, Task
from evals.dataset import SETS, load, save

UNIVERSITY = re.compile(r"sabanciuniv\.edu|sucourse", re.I)
INTERNSHIP = re.compile(r"\bintern(ship)?s?\b|career", re.I)
NEWSLETTER = re.compile(r"newsletter|digest|weekly|bulletin|news@|substack|beehiiv|mailchimp", re.I)
NOTIFICATION = re.compile(r"no-?reply|notification|notify|alert", re.I)
ACTION_WORDS = re.compile(
    r"deadline|\bdue\b|submit|apply|register|rsvp|reminder|please (complete|fill|confirm|reply)", re.I
)
WEEKDAY = r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
RELATIVE_DATE = re.compile(
    rf"\b(tomorrow|tonight|today|next week|this week|end of (the )?week"
    rf"|(this|next|on) {WEEKDAY}|in \d+ days)\b",
    re.I,
)

# Tags drawn twice per turn get roughly twice the share of the sample.
WEIGHTS = {"had_tasks": 2}


def tags_for(item: Item, had_tasks: bool) -> list[str]:
    text = f"{item.title}\n{item.body or ''}"
    tags = ["had_tasks" if had_tasks else "no_tasks"]
    if UNIVERSITY.search(item.sender or ""):
        tags.append("university")
    if INTERNSHIP.search(text):
        tags.append("internship")
    if NEWSLETTER.search(f"{item.sender}\n{item.title}"):
        tags.append("newsletter")
    if NOTIFICATION.search(item.sender or ""):
        tags.append("notification")
    if ACTION_WORDS.search(text):
        tags.append("action_words")
    if RELATIVE_DATE.search(text):
        tags.append("relative_dates")
    return tags


DEFAULT_N = {"tuning": 60, "test": 25}


def eligible_emails(db) -> list[Item]:
    items = db.scalars(
        select(Item)
        .where(Item.type == "email", Item.processed.is_(True), Item.process_error.is_(None))
        .order_by(Item.id)
    ).all()
    # Check the filters again too, in case the patterns changed since processing.
    return [
        i for i in items
        if i.summary not in (SKIPPED_SUMMARY, NOISE_SUMMARY)
        and not is_sensitive(i.title, i.sender)
        and not is_noise(i.sender)
    ]


def stratified_sample(tagged: dict[int, list[str]], n: int, seed: int) -> list[int]:
    """Takes turns over the tags, drawing a random unpicked email from each."""
    rng = random.Random(seed)
    buckets: dict[str, list[int]] = {}
    for item_id, tags in tagged.items():
        for tag in tags:
            buckets.setdefault(tag, []).append(item_id)
    for ids in buckets.values():
        rng.shuffle(ids)

    picked: list[int] = []
    chosen: set[int] = set()
    order = sorted(buckets)
    while len(picked) < n:
        progress = False
        for tag in order:
            for _ in range(WEIGHTS.get(tag, 1)):
                ids = buckets[tag]
                while ids and ids[-1] in chosen:
                    ids.pop()
                if ids and len(picked) < n:
                    picked.append(ids.pop())
                    chosen.add(picked[-1])
                    progress = True
        if not progress:  # every bucket is empty
            break
    return picked


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--set", choices=sorted(SETS), default="tuning", dest="eval_set",
                        help="Which eval set to write (default: tuning)")
    parser.add_argument("--n", type=int, help="Emails to pick (default: 60 for tuning, 25 for test)")
    parser.add_argument("--seed", type=int, default=8)
    parser.add_argument("--force", action="store_true",
                        help="Replace an existing file (labels of emails still in the sample are kept).")
    args = parser.parse_args()
    path = SETS[args.eval_set]
    n = args.n or DEFAULT_N[args.eval_set]

    old_labels = {}
    if path.exists():
        if not args.force:
            sys.exit(f"{path.name} already exists. Use --force to replace it.")
        old_labels = {r["id"]: r["expected"] for r in load(path) if r.get("expected")}

    # Emails in the other sets are off limits, so tuning and test never overlap.
    other_files = [p for name, p in SETS.items() if name != args.eval_set]
    if args.eval_set == "test" and not SETS["tuning"].exists():
        sys.exit("Export the tuning set first, so the test set can leave its emails out.")
    excluded = {r["id"] for p in other_files if p.exists() for r in load(p)}

    db = SessionLocal()
    try:
        items = {i.id: i for i in eligible_emails(db) if i.id not in excluded}
        with_tasks = set(db.scalars(
            select(Task.item_id).where(Task.created_by == "extraction", Task.item_id.is_not(None))
        ))
        tagged = {i.id: tags_for(i, i.id in with_tasks) for i in items.values()}
        picked = stratified_sample(tagged, n, args.seed)

        rows = []
        for item_id in sorted(picked, key=lambda i: items[i].occurred_at or items[i].created_at):
            item = items[item_id]
            rows.append({
                "id": item.id,
                "sent_at": item.occurred_at.isoformat() if item.occurred_at else None,
                "sender": item.sender,
                "subject": item.title,
                "body": item.body,
                "tags": tagged[item_id],
                "expected": old_labels.get(item.id),
            })
        save(rows, path)
    finally:
        db.close()

    counts: dict[str, int] = {}
    for r in rows:
        for t in r["tags"]:
            counts[t] = counts.get(t, 0) + 1
    print(f"Wrote {len(rows)} of {len(items)} eligible emails to evals/{path.name}"
          + (f" ({len(excluded)} in the other set left out)" if excluded else ""))
    print("Emails per tag (an email can have several):")
    for tag, c in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {tag:15} {c}")
    if old_labels:
        print(f"Kept {sum(1 for r in rows if r['expected'])} existing labels.")


if __name__ == "__main__":
    main()
