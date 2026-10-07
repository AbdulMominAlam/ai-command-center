"""Tests for masking personal numbers before email text goes to Claude."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.llm.extract import extract, is_sensitive
from app.llm.redact import count_matches, iban_ok, luhn_ok, redact
from app.models import Item


@pytest.mark.parametrize("text, label", [
    ("CNIC 35202-1234567-1 attached", "CNIC"),
    ("CNIC: 3520212345671.", "CNIC"),
    ("Card 4111 1111 1111 1111 was charged", "CARD"),
    ("card 4111-1111-1111-1111", "CARD"),
    ("Amex 378282246310005", "CARD"),
    ("IBAN: PK36SCBL0000001123456702", "IBAN"),
    ("IBAN GB82 WEST 1234 5698 7654 32 please", "IBAN"),
    ("TR33 0006 1005 1978 6457 8413 26", "IBAN"),
    ("Call +92 300 1234567", "PHONE"),
    ("WhatsApp +92-300-1234567 today", "PHONE"),
    ("Tel: +90 (532) 123 45 67", "PHONE"),
    ("Office +1 555 123 4567", "PHONE"),
    ("mobile 0300-1234567", "PHONE"),
    ("mobile 0532 123 45 67", "PHONE"),
])
def test_personal_numbers_are_redacted(text, label):
    out = redact(text)
    assert f"[REDACTED-{label}]" in out
    assert count_matches(text) == {label: 1}
    assert not any(ch.isdigit() for ch in out.split("[REDACTED")[1].split("]")[0])


@pytest.mark.parametrize("text", [
    "Submit by 2026-10-06 23:59",
    "Order #1234567 shipped",
    "Profile ID 2026-123456 marked pending",
    "Room FENS 1040, 14:00-15:30",
    "Fee: 1,234,567 PKR",
    "Card 4111 1111 1111 1112",           # fails Luhn
    "IBAN GB82 WEST 1234 5698 7654 33",   # fails mod 97
    "Tracking 9400111899223817445521",    # 22 digits: too long for a card
    "Version 3.12.4, build 20251001",
    "Score 3520212345 out of 10",
])
def test_lookalikes_are_left_alone(text):
    assert redact(text) == text


def test_several_matches_and_empty_text():
    text = "CNIC 35202-1234567-1, card 4111111111111111, call 0300-1234567 or +92 321 7654321"
    assert count_matches(text) == {"CNIC": 1, "CARD": 1, "PHONE": 2}
    assert redact(None) is None and redact("") == ""


def test_checksums():
    assert luhn_ok("4111 1111 1111 1111") and not luhn_ok("4111 1111 1111 1112")
    assert iban_ok("PK36SCBL0000001123456702") and not iban_ok("PK37SCBL0000001123456702")


def test_cdc_ealerts_and_account_numbers_are_sensitive():
    assert is_sensitive("eAlert - Account Opening: Executed on 02-SEP-2026", "CDC <noreply@cdcpak.com>")
    assert is_sensitive("Your Account No. 123 statement", None)
    assert not is_sensitive("Account notification settings", None)
    assert not is_sensitive("Healert team weekly", None)


def test_extract_sends_redacted_text(monkeypatch):
    sent = {}

    def fake_call_tool(system, user_message, *args, **kwargs):
        sent["message"] = user_message
        raise RuntimeError("stop here")

    monkeypatch.setattr("app.llm.extract.call_tool", fake_call_tool)
    item = Item(title="Refund to card 4111 1111 1111 1111", sender="shop@example.com",
                body="Your CNIC 35202-1234567-1 and phone +92 300 1234567 are on file.", occurred_at=None)
    with pytest.raises(RuntimeError):
        extract(item, today=datetime(2026, 10, 6, tzinfo=ZoneInfo("Europe/Istanbul")), tasks=[])
    msg = "".join(block["text"] for block in sent["message"])
    assert "4111" not in msg and "35202" not in msg and "1234567" not in msg
    assert "[REDACTED-CARD]" in msg and "[REDACTED-CNIC]" in msg and "[REDACTED-PHONE]" in msg
