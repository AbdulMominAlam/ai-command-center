import pytest

from app.llm.extract import is_sensitive


@pytest.mark.parametrize(
    "subject, sender",
    [
        ("Your OTP is ready", "noreply@bank.com"),
        ("Your verification code", "accounts@example.com"),
        ("Your pass code for login", None),
        ("Passcode for sign-in", None),
        ("Your login credentials", "it@university.edu"),
        ("Reset your PASSWORD", "security@example.com"),
        ("Funds Transfer Confirmation", "alerts@bank.com"),
        ("Transaction alert", "HBL Alerts <alerts@hbl.com>"),
    ],
)
def test_sensitive_emails_are_detected(subject, sender):
    assert is_sensitive(subject, sender)


@pytest.mark.parametrize(
    "subject, sender",
    [
        ("Homework 2 released", "CS204 Course <cs204@university.edu>"),
        ("Hotpot night on Friday", "clubs@university.edu"),  # "otp" inside a word
        ("Project sync", "Ayse Demir <ayse.demir@university.edu>"),
        (None, None),
    ],
)
def test_normal_emails_are_not_flagged(subject, sender):
    assert not is_sensitive(subject, sender)
