"""Tests for the shared sync run (sync + extraction), its lock and the background job."""

import logging
from datetime import timedelta
from types import SimpleNamespace

import anthropic
import httpx
import pytest
from fastapi.testclient import TestClient

from app import main, scheduler
from app.auth.google import GoogleReconnectRequired
from app.auth.routes import get_current_user
from app.db import get_db
from app.sync import runner
from app.sync.sucourse import SucourseNotConfigured

USER = SimpleNamespace(id=1)
SUCOURSE = {"events": 4, "added": 1, "updated": 3, "tasks_created": 1, "tasks_updated": 3, "priorities_changed": 0}
EXTRACTION = {"processed": 12, "skipped_sensitive": 2, "skipped_noise": 3, "failed": 0, "tasks_created": 3,
              "task_titles": ["Submit HW2", "Reply to Prof. Demir", "Pay dorm fee"], "duplicates_skipped": 1,
              "tasks_resolved": 1, "resolved_titles": ["Register for midterm"], "input_tokens": 30_000,
              "output_tokens": 2_000, "cache_creation_tokens": 4_200, "cache_read_tokens": 180_000,
              "estimated_cost_usd": 0.04}


class FakeDB:
    def __init__(self):
        self.rollbacks = 0

    def rollback(self):
        self.rollbacks += 1


@pytest.fixture
def syncs(monkeypatch):
    """Replaces the real syncs and extraction; records the extraction limit."""
    seen = {}

    def fake_extract(db, user, limit):
        seen["limit"] = limit
        return dict(EXTRACTION)

    monkeypatch.setattr(runner, "sync_gmail", lambda db, user: 5)
    monkeypatch.setattr(runner, "sync_calendar", lambda db, user: {"added": 1, "updated": 2, "deleted": 0})
    monkeypatch.setattr(runner, "sync_sucourse", lambda db, user: dict(SUCOURSE))
    monkeypatch.setattr(runner, "process_unprocessed", fake_extract)
    return seen


# --- run_full_sync ------------------------------------------------------------

def test_full_sync_runs_extraction_with_the_configured_limit(syncs, monkeypatch):
    monkeypatch.setattr(runner.settings, "EXTRACT_ON_SYNC_LIMIT", 7)

    result = runner.run_full_sync(FakeDB(), USER)

    assert syncs["limit"] == 7
    assert result["gmail"] == {"added": 5}
    assert result["sucourse"]["tasks_created"] == 1
    ext = result["extraction"]
    assert (ext["processed"], ext["tasks_created"], ext["estimated_cost_usd"]) == (12, 3, 0.04)
    # Counts only: task titles stay out of the sync response.
    assert "task_titles" not in ext and "resolved_titles" not in ext


def test_default_extract_limit_is_50():
    assert type(runner.settings).model_fields["EXTRACT_ON_SYNC_LIMIT"].default == 50


def test_missing_sucourse_url_is_skipped_and_extraction_still_runs(syncs, monkeypatch):
    def not_configured(db, user):
        raise SucourseNotConfigured("No SUCourse calendar URL saved.")

    monkeypatch.setattr(runner, "sync_sucourse", not_configured)

    result = runner.run_full_sync(FakeDB(), USER)

    assert "skipped" in result["sucourse"]
    assert result["extraction"]["tasks_created"] == 3


def test_rejected_api_key_is_reported_not_raised(syncs, monkeypatch):
    def bad_key(db, user, limit):
        response = httpx.Response(401, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
        raise anthropic.AuthenticationError("invalid x-api-key", response=response, body=None)

    monkeypatch.setattr(runner, "process_unprocessed", bad_key)
    db = FakeDB()

    result = runner.run_full_sync(db, USER)

    assert "API key" in result["extraction"]["error"]
    assert result["gmail"] == {"added": 5}
    assert db.rollbacks == 1


def test_only_one_sync_at_a_time(syncs):
    with runner.exclusive():
        with pytest.raises(runner.SyncAlreadyRunning):
            runner.run_full_sync(FakeDB(), USER)
    # The lock is released afterwards, also after an error.
    with pytest.raises(GoogleReconnectRequired):
        with runner.exclusive():
            raise GoogleReconnectRequired("Reconnect Google.")
    assert runner.run_full_sync(FakeDB(), USER)["gmail"] == {"added": 5}


# --- endpoints ----------------------------------------------------------------

@pytest.fixture
def client():
    main.app.dependency_overrides[get_current_user] = lambda: USER
    main.app.dependency_overrides[get_db] = lambda: FakeDB()
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


def test_sync_all_returns_extraction_counts(client, syncs):
    resp = client.post("/sync/all")
    assert resp.status_code == 200
    assert resp.json()["extraction"]["tasks_created"] == 3


@pytest.mark.parametrize("path", ["/sync/all", "/extract/run"])
def test_busy_sync_returns_409(client, syncs, path):
    with runner.exclusive():
        resp = client.post(path)
    assert resp.status_code == 409
    assert "already running" in resp.json()["detail"]


# --- background job -----------------------------------------------------------

class SessionDB:
    """Stands in for SessionLocal(): a context manager whose query returns `users`."""

    def __init__(self, users):
        self.users = users
        self.rollbacks = 0

    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def scalars(self, stmt):
        return SimpleNamespace(all=lambda: self.users)

    def rollback(self):
        self.rollbacks += 1


def full_result():
    return {"gmail": {"added": 5}, "calendar": {"added": 1, "updated": 2, "deleted": 0},
            "sucourse": dict(SUCOURSE),
            "extraction": {k: EXTRACTION[k] for k in runner.EXTRACTION_KEYS}, "elapsed_seconds": 41.2}


def test_background_sync_skips_quietly_without_a_connected_user(monkeypatch, caplog):
    calls = []
    monkeypatch.setattr(scheduler, "run_full_sync", lambda db, user: calls.append(user))

    with caplog.at_level(logging.INFO, logger="app"):
        scheduler.run_background_sync(SessionDB([]))

    assert calls == []
    assert caplog.records == []


def test_background_sync_skips_users_who_must_reconnect(monkeypatch, caplog):
    def fake_sync(db, user):
        if user.id == 1:
            raise GoogleReconnectRequired("Google access was revoked or expired.")
        return full_result()

    monkeypatch.setattr(scheduler, "run_full_sync", fake_sync)
    db = SessionDB([SimpleNamespace(id=1), SimpleNamespace(id=2)])

    with caplog.at_level(logging.INFO, logger="app"):
        scheduler.run_background_sync(db)

    messages = [r.getMessage() for r in caplog.records]
    assert any("user 1" in m and "reconnecting" in m for m in messages)
    assert any("user 2" in m and "+3 tasks" in m and "$0.0400" in m for m in messages)
    assert all(r.levelno < logging.ERROR for r in caplog.records)
    assert db.rollbacks == 1


def test_background_sync_never_raises(monkeypatch, caplog):
    def broken(db, user):
        raise RuntimeError("database went away")

    monkeypatch.setattr(scheduler, "run_full_sync", broken)

    with caplog.at_level(logging.INFO, logger="app"):
        scheduler.run_background_sync(SessionDB([USER]))

    assert any(r.levelno == logging.ERROR for r in caplog.records)


def test_summary_has_counts_and_cost_but_no_titles():
    line = scheduler.summarize(full_result())
    assert "gmail +5 emails" in line
    assert "extraction 12 emails -> +3 tasks" in line
    assert "cost $0.0400" in line
    assert "Submit HW2" not in line


def test_scheduler_job_runs_every_30_minutes_without_overlap():
    job = scheduler.create_scheduler().get_job(scheduler.JOB_ID)
    assert job.trigger.interval == timedelta(minutes=30)
    assert job.max_instances == 1
    assert job.func is scheduler.run_background_sync


@pytest.mark.parametrize("enabled", [True, False])
def test_background_sync_setting_controls_the_scheduler(monkeypatch, enabled):
    started = []
    real_create = scheduler.create_scheduler

    def spy():
        s = real_create()
        started.append(s)
        return s

    monkeypatch.setattr(main.settings, "BACKGROUND_SYNC_ENABLED", enabled)
    monkeypatch.setattr(main, "create_scheduler", spy)

    with TestClient(main.app):  # runs the lifespan: startup, then shutdown
        if enabled:
            assert started[0].running

    assert len(started) == (1 if enabled else 0)
