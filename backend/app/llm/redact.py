"""Masks personal numbers in email text before it is sent to Claude.

Every pattern lives in REDACTIONS below: add, remove or reorder entries there.
Each match becomes [REDACTED-<LABEL>]. Patterns run in order, so the
stricter ones (IBAN, CNIC) go before the looser ones (cards, phones).
An optional check function rejects lookalikes, e.g. card numbers must pass
the Luhn checksum and IBANs the mod-97 checksum.

Count matches in your stored emails (counts only):  uv run python -m app.llm.redact
"""

import re
from collections import Counter
from dataclasses import dataclass
from typing import Callable


def luhn_ok(number: str) -> bool:
    """The checksum every payment card number passes."""
    digits = [int(d) for d in re.sub(r"\D", "", number)]
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def iban_ok(iban: str) -> bool:
    """ISO 13616 check: move the first 4 characters to the end, letters become
    numbers (A=10 ... Z=35), and the result mod 97 must be 1."""
    s = re.sub(r"\s", "", iban).upper()
    if not 15 <= len(s) <= 34:
        return False
    return int("".join(str(int(c, 36)) for c in s[4:] + s[:4])) % 97 == 1


def digit_count_between(lo: int, hi: int) -> Callable[[str], bool]:
    return lambda s: lo <= len(re.sub(r"\D", "", s)) <= hi


@dataclass(frozen=True)
class Redaction:
    label: str
    pattern: re.Pattern
    check: Callable[[str], bool] | None = None


REDACTIONS = [
    # IBAN: 2 letters, 2 check digits, then 11-30 letters/digits, maybe in groups of 4.
    # PK36 SCBL 0000 0011 2345 6702
    Redaction("IBAN", re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]){11,30}\b"), iban_ok),
    # Pakistani CNIC: 13 digits, written 12345-1234567-1 or 1234512345671.
    Redaction("CNIC", re.compile(r"(?<![\d+-])\d{5}-?\d{7}-?\d(?![\d-])")),
    # Card: 13-19 digits, maybe in groups split by spaces or dashes; must pass Luhn.
    Redaction("CARD", re.compile(r"(?<![\d+-])\d(?:[ -]?\d){12,18}(?![\d-])"), luhn_ok),
    # Phone with a country code: +92 300 1234567, +90 (532) 123 45 67, +1-555-123-4567
    Redaction("PHONE", re.compile(r"(?<![\w+])\+\d[\d ()./-]{6,20}\d"), digit_count_between(8, 15)),
    # Pakistani mobile without a country code: 0300-1234567, 0300 1234567
    Redaction("PHONE", re.compile(r"(?<![\d-])03\d{2}[ -]?\d{7}(?![\d-])")),
    # Turkish mobile without a country code: 0532 123 45 67, 05321234567
    Redaction("PHONE", re.compile(r"(?<![\d-])05\d{2} ?\d{3} ?\d{2} ?\d{2}(?![\d-])")),
]


def _replace(text: str, counts: Counter | None) -> str:
    for r in REDACTIONS:
        def sub(m: re.Match, r=r) -> str:
            if r.check and not r.check(m.group()):
                return m.group()
            if counts is not None:
                counts[r.label] += 1
            return f"[REDACTED-{r.label}]"
        text = r.pattern.sub(sub, text)
    return text


def redact(text: str | None) -> str | None:
    """The text with every match replaced by [REDACTED-<LABEL>]."""
    return _replace(text, None) if text else text


def count_matches(text: str | None) -> Counter:
    """How many matches of each label the text has."""
    counts: Counter = Counter()
    if text:
        _replace(text, counts)
    return counts


def main() -> None:
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import Item

    totals: Counter = Counter()  # matches per label
    emails: Counter = Counter()  # emails with at least one match, per label
    any_match = n = 0
    with SessionLocal() as db:
        for title, body in db.execute(select(Item.title, Item.body).where(Item.type == "email")):
            n += 1
            c = count_matches(f"{title}\n{body or ''}")
            totals.update(c)
            emails.update(c.keys())
            any_match += bool(c)
    print(f"{any_match} of {n} stored emails contain something to redact")
    for label in sorted(totals):
        print(f"  {label:6} {emails[label]:4} emails, {totals[label]:5} matches")


if __name__ == "__main__":
    main()
