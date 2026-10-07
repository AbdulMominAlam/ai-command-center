"""Dry run of the email filters on a linked account's recent mail. Writes nothing.

Run from backend/:  uv run python -m app.sync.dryrun --email you@sabanciuniv.edu [--days 14]

Lists the account's Gmail for the last N days with the same query as the first
sync, fetches each message and runs the same filters extraction would: sensitive,
noise and (for a university account) course admin. Prints only counts: emails per
sender domain and how many emails each filter and each pattern would skip, plus a
cost estimate. Never prints subjects, bodies or addresses. Saves nothing to the
database and makes no Claude calls. Gmail fetches are throttled, ~100 emails a minute.
"""

import argparse
import sys
from collections import Counter
from email.utils import parseaddr

from googleapiclient.discovery import build
from sqlalchemy import func, select

from app.auth.accounts import is_university
from app.auth.google import GoogleReconnectRequired, get_google_credentials
from app.config import settings
from app.db import SessionLocal
from app.llm.extract import course_admin_matches, is_noise, is_sensitive
from app.llm.pricing import estimate_cost
from app.models import GoogleAccount, LLMUsage
from app.sync.gmail import fetch_message, list_message_ids, message_fields
from app.sync.runner import FIRST_SYNC_COST_CAP_USD


def sender_domain(sender: str | None) -> str:
    address = parseaddr(sender or "")[1]
    return address.rsplit("@", 1)[-1].lower() if "@" in address else "(none)"


def classify(fields: dict, university: bool) -> tuple[str, list[str]]:
    """The filter that would skip an email (same order as extraction), and every
    course-admin pattern it matches."""
    patterns = course_admin_matches(fields["title"], fields["body"], fields["sender"]) if university else []
    if is_sensitive(fields["title"], fields["sender"]):
        return "sensitive", patterns
    if is_noise(fields["sender"]):
        return "noise", patterns
    if patterns:
        return "course admin", patterns
    return "to Claude", patterns


def average_extraction_cost(db) -> float | None:
    """Average estimated cost of one extraction call so far, from llm_usage."""
    row = db.execute(
        select(func.count(), func.sum(LLMUsage.input_tokens), func.sum(LLMUsage.output_tokens),
               func.sum(LLMUsage.cache_creation_input_tokens), func.sum(LLMUsage.cache_read_input_tokens))
        .where(LLMUsage.purpose == "extraction", LLMUsage.model == settings.EXTRACT_MODEL)
    ).one()
    calls, *tokens = row
    if not calls:
        return None
    total = estimate_cost(settings.EXTRACT_MODEL, *(t or 0 for t in tokens))
    return None if total is None else total / calls


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--email", required=True, help="The linked Google account to check")
    parser.add_argument("--days", type=int, default=14)
    args = parser.parse_args()

    with SessionLocal() as db:
        account = db.scalar(select(GoogleAccount).where(func.lower(GoogleAccount.email) == args.email.lower()))
        if account is None:
            sys.exit("That Google account is not linked. Link it first (Settings > Link another Google account).")
        avg_cost = average_extraction_cost(db)
        try:
            gmail = build("gmail", "v1", credentials=get_google_credentials(account), cache_discovery=False)
        except GoogleReconnectRequired as e:
            sys.exit(str(e))
        university = is_university(account.email)

        window = f"newer_than:{args.days}d"
        excluded = sum(1 for _ in list_message_ids(gmail, f"{window} (category:promotions OR category:social)"))
        ids = list(list_message_ids(gmail, f"{window} -category:promotions -category:social"))
        print(f"Account {account.id} ({'university' if university else 'personal'}), last {args.days} days: "
              f"{len(ids)} emails to check, {excluded} promotions/social left out by the query. Fetching...")

        domains, outcomes, patterns = Counter(), Counter(), Counter()
        for message_id in ids:
            msg = fetch_message(gmail, message_id)
            if msg is None:
                continue
            fields = message_fields(msg)
            outcome, hits = classify(fields, university)
            domains[sender_domain(fields["sender"])] += 1
            outcomes[outcome] += 1
            patterns.update(hits)

    print("\nEmails per sender domain:")
    for domain, n in domains.most_common():
        print(f"  {n:5}  {domain}")
    print("\nWhat extraction would do (first matching filter wins):")
    for outcome in ("sensitive", "noise", "course admin", "to Claude"):
        print(f"  {outcome:13} {outcomes[outcome]:5}")
    if university:
        print("\nCourse-admin pattern hits (an email can match several, including ones skipped by an earlier filter):")
        for pattern, n in patterns.most_common():
            print(f"  {n:5}  {pattern}")
        if not patterns:
            print("  none")
    sent = outcomes["to Claude"]
    if avg_cost is not None:
        print(f"\nEstimated extraction cost: ~${sent * avg_cost:.2f} for {sent} emails "
              f"(average ${avg_cost:.4f} per email so far); the first sync stops at ${FIRST_SYNC_COST_CAP_USD:.2f}.")


if __name__ == "__main__":
    main()
