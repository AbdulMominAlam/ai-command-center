"""Tests for stopping on an empty credit balance, and for prompt-caching markers."""

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import anthropic
import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from app.auth.routes import get_current_user
from app.db import get_db
from app.llm import extract as ex
from app.llm import routes as extract_routes
from app.llm.client import CreditTooLow, create_message, is_credit_error
from app.models import Item, LLMUsage
from app.sync import runner

TZ = ZoneInfo("Europe/Istanbul")
USER = SimpleNamespace(id=1)


def api_error(cls, status, message, error_type="invalid_request_error"):
    response = httpx.Response(status, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
    return cls(message, response=response, body={"type": "error", "error": {"type": error_type, "message": message}})


CREDIT_400 = api_error(anthropic.BadRequestError, 400,
                       "Your credit balance is too low to access the Anthropic API. "
                       "Please go to Plans & Billing to upgrade or purchase credits.")
BILLING_402 = api_error(anthropic.APIStatusError, 402, "Payment required", "billing_error")


# --- recognising billing errors ---


@pytest.mark.parametrize("error", [CREDIT_400, BILLING_402])
def test_billing_errors_are_recognised(error):
    assert is_credit_error(error)


@pytest.mark.parametrize("error", [
    api_error(anthropic.BadRequestError, 400, "max_tokens: field required"),
    api_error(anthropic.RateLimitError, 429, "rate limited", "rate_limit_error"),
    ValueError("credit balance"),  # not an API error
])
def test_other_errors_are_not_credit_errors(error):
    assert not is_credit_error(error)


class RaisingLLM:
    def __init__(self, error):
        self.error = error
        self.messages = self

    def create(self, **kwargs):
        raise self.error


def test_create_message_turns_billing_errors_into_credit_too_low():
    with pytest.raises(CreditTooLow, match="API credit is low"):
        create_message(RaisingLLM(CREDIT_400), model="m", max_tokens=1, messages=[])
    other = api_error(anthropic.BadRequestError, 400, "bad request")
    with pytest.raises(anthropic.BadRequestError):
        create_message(RaisingLLM(other), model="m", max_tokens=1, messages=[])


# --- extraction stops without marking emails ---


class FakeDB:
    def __init__(self, items):
        self.items, self.added, self.commits = items, [], 0

    def scalars(self, stmt):
        return SimpleNamespace(all=lambda: list(self.items))

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commits += 1


def email(id):
    return Item(id=id, title=f"Email {id}", sender="someone@example.com", body="Please reply.",
                occurred_at=datetime(2026, 10, 6, 9, 0, tzinfo=TZ), processed=False)


def test_extraction_stops_on_credit_error_and_leaves_emails_unprocessed(monkeypatch):
    items = [email(1), email(2), email(3)]
    calls = []

    def fake_extract(item, today, tasks):
        calls.append(item.id)
        if item.id == 2:
            raise CreditTooLow()
        return ex.Extraction(is_actionable=False, tasks=[], summary="FYI."), ex.Usage(100, 10, 0, 4000)

    monkeypatch.setattr(ex, "extract", fake_extract)
    monkeypatch.setattr(ex, "open_tasks", lambda db, user, limit: [])
    db = FakeDB(items)

    with pytest.raises(CreditTooLow):
        ex.process_unprocessed(db, USER, limit=10)

    assert calls == [1, 2]  # stopped right away, email 3 never sent
    assert items[0].processed is True
    assert items[1].processed is False and items[1].process_error is None  # not marked failed
    assert items[2].processed is False
    [usage] = [o for o in db.added if isinstance(o, LLMUsage)]
    assert (usage.cache_read_input_tokens, usage.cache_creation_input_tokens) == (4000, 0)


def test_sync_reports_credit_low(monkeypatch):
    def no_credit(db, user, limit):
        raise CreditTooLow()

    monkeypatch.setattr(runner, "sync_gmail", lambda db, user: 2)
    monkeypatch.setattr(runner, "sync_calendar", lambda db, user: {"added": 0, "updated": 0, "deleted": 0})
    monkeypatch.setattr(runner, "sync_sucourse", lambda db, user: {"tasks_created": 0})
    monkeypatch.setattr(runner, "process_unprocessed", no_credit)
    db = SimpleNamespace(rollbacks=0)
    db.rollback = lambda: setattr(db, "rollbacks", db.rollbacks + 1)

    result = runner.run_full_sync(db, USER)

    assert result["extraction"]["credit_low"] is True
    assert "API credit is low" in result["extraction"]["error"]
    assert result["gmail"] == {"added": 2} and db.rollbacks == 1


def test_extract_endpoint_returns_402_with_credit_low(monkeypatch):
    def no_credit(db, user, limit):
        raise CreditTooLow()

    monkeypatch.setattr(extract_routes, "process_unprocessed", no_credit)
    main.app.dependency_overrides[get_current_user] = lambda: USER
    main.app.dependency_overrides[get_db] = lambda: None
    try:
        resp = TestClient(main.app).post("/extract/run?limit=3")
    finally:
        main.app.dependency_overrides.clear()
    assert resp.status_code == 402
    assert resp.json() == {"detail": "API credit is low, so Claude calls are paused. "
                                     "Add credit in the Anthropic Console, then sync again.", "credit_low": True}


# --- prompt caching markers ---


def test_extract_marks_system_and_context_for_caching(monkeypatch):
    seen = {}

    def fake_create(**kwargs):
        seen.update(kwargs)
        tool_use = SimpleNamespace(type="tool_use", input={"is_actionable": False, "tasks": [], "summary": "s"})
        usage = SimpleNamespace(input_tokens=300, output_tokens=40,
                                cache_creation_input_tokens=0, cache_read_input_tokens=4100)
        return SimpleNamespace(stop_reason="tool_use", content=[tool_use], usage=usage)

    monkeypatch.setattr(ex, "create_message", fake_create)
    today = datetime(2026, 10, 7, 12, 0, tzinfo=TZ)
    _, usage = ex.extract(email(1), today, [])

    [system] = seen["system"]
    assert system["text"] == ex.SYSTEM_PROMPT and system["cache_control"] == {"type": "ephemeral"}
    context, body = seen["messages"][0]["content"]
    assert context["text"].startswith("Today: Wednesday 2026-10-07 12:00") and "cache_control" in context
    assert body["text"].startswith("Sent: ") and "cache_control" not in body  # the email itself is never cached
    assert usage == ex.Usage(300, 40, 0, 4100)
