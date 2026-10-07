"""Tests for Google Tasks sync, the granted-scopes refresh, and upcoming events on Today."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from googleapiclient.errors import HttpError

from app.auth import accounts
from app.auth import google as google_auth
from app.dashboard import today
from app.models import Item, Task, User
from app.sync import google_tasks as gt
from app.sync import runner

TZ = ZoneInfo("Europe/Istanbul")
TASKS = "https://www.googleapis.com/auth/tasks.readonly"


class FakeTasksService:
    """Two task lists; `lists` maps list title -> list of Google Task dicts."""

    def __init__(self, lists=None, error=None):
        self.lists, self.error = lists or {}, error

    def tasklists(self):
        return SimpleNamespace(list=lambda **kw: self._run(
            {"items": [{"id": name, "title": name} for name in self.lists]}))

    def tasks(self):
        return SimpleNamespace(list=lambda tasklist, **kw: self._run({"items": self.lists[tasklist]}))

    def _run(self, payload):
        def execute(num_retries=0):
            if self.error:
                raise self.error
            return payload
        return SimpleNamespace(execute=execute)


def task(id, title, status="needsAction", due=None, deleted=False):
    return {"id": id, "title": title, "status": status, "due": due, "deleted": deleted, "notes": "private note"}


@pytest.fixture
def setup(db, monkeypatch):
    user = User(email="me@gmail.com")
    db.add(user)
    db.flush()
    account = accounts.save_account(db, user, "me@gmail.com", "r", f"openid {TASKS}")
    service = FakeTasksService()
    monkeypatch.setattr(gt, "get_google_credentials", lambda account: None)
    monkeypatch.setattr(gt, "build", lambda *a, **k: service)
    db.commit = db.flush  # stay inside the test's transaction
    return db, user, account, service


def our_tasks(db, user):
    return {t.title: t for t in db.query(Task).filter_by(user_id=user.id, created_by="google_tasks")}


def test_google_tasks_are_created_kept_up_to_date_and_closed(setup):
    db, user, account, service = setup

    # 1. First sync: two open tasks become ours; one already completed is skipped.
    service.lists = {"My Tasks": [task("a", "Buy lab goggles", due="2026-10-09T00:00:00.000Z"),
                                  task("b", "Call the registrar"),
                                  task("c", "Old done thing", status="completed")]}
    stats = gt.sync_google_tasks(db, user, account)
    assert (stats["created"], stats["seen"]) == (2, 3)
    mine = our_tasks(db, user)
    assert set(mine) == {"Buy lab goggles", "Call the registrar"}
    # A date-only due becomes 23:59 Istanbul time that day.
    assert mine["Buy lab goggles"].due_at == datetime(2026, 10, 9, 23, 59, tzinfo=TZ)
    assert mine["Call the registrar"].due_at is None
    item = db.query(Item).filter_by(source="google_tasks", external_id="a").one()
    assert item.account_id == account.id and "private note" not in str(item.raw)  # notes aren't stored

    # 2. a completed and renamed, b deleted in Google, d new; you mark d done here.
    service.lists = {"My Tasks": [task("a", "Buy lab goggles (2 pairs)", status="completed"),
                                  task("b", "Call the registrar", deleted=True),
                                  task("d", "Return library book")]}
    stats = gt.sync_google_tasks(db, user, account)
    mine = our_tasks(db, user)
    assert mine["Buy lab goggles (2 pairs)"].status == "done"
    assert mine["Call the registrar"].status == "done"  # deleted in Google
    assert mine["Return library book"].status == "open"
    assert (stats["created"], stats["completed"]) == (1, 2)
    mine["Return library book"].status = "done"

    # 3. a reopened in Google: reopened here. d is still open in Google but you closed it: stays done.
    service.lists = {"My Tasks": [task("a", "Buy lab goggles (2 pairs)"), task("d", "Return library book")]}
    stats = gt.sync_google_tasks(db, user, account)
    mine = our_tasks(db, user)
    assert mine["Buy lab goggles (2 pairs)"].status == "open"
    assert mine["Return library book"].status == "done"
    assert stats["reopened"] == 1
    assert len(mine) == 3  # no duplicates across syncs


def test_accounts_without_the_tasks_scope_are_skipped_before_any_call(setup, monkeypatch):
    db, user, account, _ = setup
    account.scopes = "openid https://www.googleapis.com/auth/gmail.readonly"
    monkeypatch.setattr(gt, "build", lambda *a, **k: pytest.fail("no API call without the scope"))
    with pytest.raises(gt.TasksNotAvailable, match="Reconnect"):
        gt.sync_google_tasks(db, user, account)


def test_a_403_from_google_means_tasks_not_available(setup):
    db, user, account, service = setup
    service.error = HttpError(SimpleNamespace(status=403, reason="Forbidden"),
                              b'{"error": {"message": "Tasks API has not been used in project"}}')
    with pytest.raises(gt.TasksNotAvailable):
        gt.sync_google_tasks(db, user, account)


def test_refresh_asks_only_for_the_scopes_the_account_granted(monkeypatch):
    seen = {}

    class FakeCredentials:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def refresh(self, request):
            pass

    monkeypatch.setattr(google_auth, "Credentials", FakeCredentials)
    monkeypatch.setattr(google_auth, "decrypt_token", lambda t: "refresh")
    old = SimpleNamespace(email="me@gmail.com", encrypted_refresh_token="x",
                          scopes="openid https://www.googleapis.com/auth/gmail.readonly")
    google_auth.get_google_credentials(old)
    assert seen["scopes"] == ["openid", "https://www.googleapis.com/auth/gmail.readonly"]  # no Tasks yet
    assert TASKS in google_auth.SCOPES  # but new consents ask for it


def test_full_sync_lists_accounts_that_need_consent_for_tasks(monkeypatch):
    personal = SimpleNamespace(id=1, email="me@gmail.com")
    sabanci = SimpleNamespace(id=2, email="me@sabanciuniv.edu")

    def fake_tasks(db, user, account):
        if account is sabanci:
            raise gt.TasksNotAvailable("Reconnect this account to allow Google Tasks.")
        return {"seen": 3, "created": 2, "updated": 1, "completed": 0, "reopened": 0}

    monkeypatch.setattr(runner, "linked_accounts", lambda db, user: [personal, sabanci])
    monkeypatch.setattr(runner, "is_first_sync", lambda db, user, account: False)
    monkeypatch.setattr(runner, "sync_gmail", lambda db, user, account: 0)
    monkeypatch.setattr(runner, "sync_calendar", lambda db, user, account: {"added": 0, "updated": 0, "deleted": 0})
    monkeypatch.setattr(runner, "sync_google_tasks", fake_tasks)
    monkeypatch.setattr(runner, "sync_sucourse", lambda db, user: {"tasks_created": 0})
    monkeypatch.setattr(runner, "process_unprocessed", lambda db, user, limit, **kw: {})

    result = runner.run_full_sync(SimpleNamespace(rollback=lambda: None), SimpleNamespace(id=1))

    assert result["google_tasks"] == {"created": 2, "updated": 1, "completed": 0, "reopened": 0,
                                      "needs_consent": ["me@sabanciuniv.edu"]}


# --- upcoming events on Today ------------------------------------------------------------

def test_upcoming_events_start_tomorrow_and_cover_seven_days(db):
    user = User(email="me@gmail.com")
    db.add(user)
    db.flush()
    account = accounts.save_account(db, user, "me@gmail.com", "r", "openid")
    now = datetime(2026, 10, 7, 15, 0, tzinfo=TZ)  # Wednesday
    tomorrow = datetime(2026, 10, 8, tzinfo=TZ)

    def event(ext, title, start, hours=1):
        db.add(Item(user_id=user.id, account_id=account.id, source="calendar", external_id=ext, type="event",
                    title=title, occurred_at=start, due_at=start + timedelta(hours=hours)))

    event("1", "Lecture today", now + timedelta(hours=1))
    event("2", "Started yesterday, ends Friday", now - timedelta(days=1), hours=60)
    event("3", "Add Drop", tomorrow + timedelta(hours=9))
    event("4", "Club meeting", tomorrow + timedelta(days=6, hours=20))
    event("5", "Too far", tomorrow + timedelta(days=7, hours=1))
    db.flush()

    result = today.build_today(db, user, now)

    assert [e["title"] for e in result["upcoming_events"]] == ["Add Drop", "Club meeting"]
    assert result["upcoming_events"][0]["account"] == "Personal"
    assert [e["title"] for e in result["events"]] == ["Started yesterday, ends Friday", "Lecture today"]
