"""Tests for GET /today, GET /tasks and PATCH /tasks/{id} with a fake database."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.auth.routes import get_current_user
from app.dashboard import routes, today
from app.db import get_db
from app.main import app
from app.models import Task

TZ = ZoneInfo("Europe/Istanbul")
NOW = datetime(2026, 10, 6, 14, 0, tzinfo=TZ)  # Tuesday afternoon
USER = SimpleNamespace(id=1)


def task(id, due_at, priority="medium", created_by="extraction", status="open", item_id=None):
    return Task(id=id, title=f"Task {id}", due_at=due_at, priority=priority,
                status=status, created_by=created_by, item_id=item_id)


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None


class FakeDB:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.statements = []
        self.commits = 0

    def execute(self, stmt):
        self.statements.append(stmt)
        return FakeResult(self.rows)

    def commit(self):
        self.commits += 1


@pytest.fixture
def client():
    db = FakeDB()
    app.dependency_overrides[get_current_user] = lambda: USER
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app), db
    app.dependency_overrides.clear()


def sql(stmt) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


# --- grouping -----------------------------------------------------------------

def test_group_tasks_splits_overdue_today_and_this_week():
    rows = [
        (task(1, NOW - timedelta(days=2)), "gmail"),
        (task(2, NOW - timedelta(hours=1)), None),               # earlier today: overdue
        (task(3, NOW + timedelta(hours=9, minutes=59)), "sucourse"),  # 23:59 today
        (task(4, datetime(2026, 10, 7, 0, 0, tzinfo=TZ)), None),  # tomorrow midnight
        (task(5, datetime(2026, 10, 13, 23, 59, tzinfo=TZ)), None),  # 7th day after today
        (task(6, datetime(2026, 10, 14, 0, 0, tzinfo=TZ)), None),   # too far
        (task(7, None), None),                                   # undated
    ]
    groups = today.group_tasks(rows, NOW)
    assert [t["id"] for t in groups["overdue"]] == [1, 2]
    assert [t["id"] for t in groups["today"]] == [3]
    assert [t["id"] for t in groups["this_week"]] == [4, 5]


def test_group_tasks_sorts_by_due_then_priority():
    due = NOW + timedelta(days=2)
    rows = [(task(1, due, "low"), None), (task(2, due, "high"), None),
            (task(3, due - timedelta(hours=1), "low"), None)]
    assert [t["id"] for t in today.group_tasks(rows, NOW)["this_week"]] == [3, 2, 1]


def test_task_view_source_and_istanbul_time():
    utc_due = datetime(2026, 10, 9, 20, 59, tzinfo=ZoneInfo("UTC"))
    assert today.task_view(task(1, utc_due), "gmail")["source"] == "gmail"
    view = today.task_view(task(2, utc_due, created_by="agent"), None)
    assert view["source"] == "agent"
    assert view["due_at"] == "2026-10-09T23:59:00+03:00"


# --- GET /today ---------------------------------------------------------------

def test_today_endpoint(client, monkeypatch):
    http, _ = client
    seen = {}
    event = SimpleNamespace(id=50, title="CS204 lecture", raw={"location": "FENS G077", "all_day": False},
                            occurred_at=NOW + timedelta(hours=1), due_at=NOW + timedelta(hours=3))
    hw = SimpleNamespace(id=90, title="Homework 2 is due", raw={"course": "CS204"},
                         due_at=NOW + timedelta(days=3))

    def fake_tasks(db, user, end):
        seen["end"] = end
        return [(task(1, NOW - timedelta(days=1), "high"), "gmail"),
                (task(2, NOW + timedelta(days=3), created_by="sucourse", item_id=90), "sucourse")]

    def fake_events(db, user, start, end):
        seen["events"] = (start, end)
        return [event]

    monkeypatch.setattr(routes, "now", lambda: NOW)
    monkeypatch.setattr(today, "open_tasks_due_before", fake_tasks)
    monkeypatch.setattr(today, "count_undated_open_tasks", lambda db, user: 4)
    monkeypatch.setattr(today, "events_between", fake_events)
    monkeypatch.setattr(today, "next_sucourse_items", lambda db, user, now: [(hw, task(2, hw.due_at))])

    resp = http.get("/today")

    assert resp.status_code == 200
    body = resp.json()
    assert body["date"] == "2026-10-06"
    assert [t["id"] for t in body["overdue"]] == [1]
    assert body["today"] == []
    assert [t["id"] for t in body["this_week"]] == [2]
    assert body["undated_count"] == 4
    assert body["events"] == [{"id": 50, "title": "CS204 lecture", "start": "2026-10-06T15:00:00+03:00",
                               "end": "2026-10-06T17:00:00+03:00", "all_day": False, "location": "FENS G077"}]
    assert body["sucourse"] == [{"id": 90, "title": "Homework 2 is due", "due_at": "2026-10-09T14:00:00+03:00",
                                 "course": "CS204", "task_id": 2, "task_status": "open"}]
    # Tasks are fetched up to the end of the 7th day after today; events for today only.
    assert seen["end"] == datetime(2026, 10, 14, 0, 0, tzinfo=TZ)
    assert seen["events"] == (datetime(2026, 10, 6, tzinfo=TZ), datetime(2026, 10, 7, tzinfo=TZ))


def test_endpoints_need_login():
    app.dependency_overrides[get_db] = lambda: FakeDB()
    try:
        http = TestClient(app)
        assert http.get("/today").status_code == 401
        assert http.get("/tasks").status_code == 401
        assert http.patch("/tasks/1", json={"status": "done"}).status_code == 401
    finally:
        app.dependency_overrides.clear()


# --- GET /tasks ---------------------------------------------------------------

def test_list_tasks_filters_by_status(client):
    http, db = client
    db.rows = [(task(1, NOW, status="done"), "gmail")]

    resp = http.get("/tasks", params={"status": "done"})

    assert resp.status_code == 200
    assert [t["id"] for t in resp.json()["tasks"]] == [1]
    query = sql(db.statements[0])
    assert "tasks.status = 'done'" in query and "tasks.user_id = 1" in query
    assert "DESC NULLS LAST" in query  # done: most recent first


def test_list_tasks_defaults_to_open_soonest_first(client):
    http, db = client
    assert http.get("/tasks").status_code == 200
    query = sql(db.statements[0])
    assert "tasks.status = 'open'" in query and "tasks.due_at ASC NULLS LAST" in query


def test_list_tasks_rejects_unknown_status(client):
    http, _ = client
    assert http.get("/tasks", params={"status": "duplicate"}).status_code == 422


# --- PATCH /tasks/{id} --------------------------------------------------------

def test_patch_marks_done_and_changes_priority(client):
    http, db = client
    t = task(7, NOW, priority="low")
    db.rows = [(t, "sucourse")]

    resp = http.patch("/tasks/7", json={"status": "done", "priority": "high"})

    assert resp.status_code == 200
    assert resp.json()["status"] == "done" and resp.json()["priority"] == "high"
    assert (t.status, t.priority) == ("done", "high")
    assert t.priority_set_by_user  # so SUCourse syncs keep it
    assert db.commits == 1
    query = sql(db.statements[0])
    assert "tasks.id = 7" in query and "tasks.user_id = 1" in query  # only your own tasks


def test_patch_only_priority_keeps_status(client):
    http, db = client
    t = task(7, NOW)
    db.rows = [(t, None)]
    assert http.patch("/tasks/7", json={"priority": "low"}).status_code == 200
    assert (t.status, t.priority) == ("open", "low")
    assert t.priority_set_by_user


def test_patch_only_status_does_not_mark_priority_as_yours(client):
    http, db = client
    t = task(7, NOW)
    db.rows = [(t, "sucourse")]
    assert http.patch("/tasks/7", json={"status": "done"}).status_code == 200
    assert not t.priority_set_by_user


def test_patch_unknown_task_is_404(client):
    http, db = client
    assert http.patch("/tasks/999", json={"status": "done"}).status_code == 404
    assert db.commits == 0


@pytest.mark.parametrize("body", [{}, {"status": "duplicate"}, {"priority": "urgent"}])
def test_patch_rejects_bad_bodies(client, body):
    http, db = client
    assert http.patch("/tasks/7", json=body).status_code == 422
    assert db.statements == []
