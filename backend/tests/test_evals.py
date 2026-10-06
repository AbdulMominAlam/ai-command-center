"""Tests for the eval set: labeling endpoints and the runner's scoring."""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.auth.routes import get_current_user
from app.dev import routes as dev_routes
from app.main import app

ROWS = [
    {"id": 1, "sent_at": "2026-03-02T10:15:00+03:00", "sender": "cs204@university.edu",
     "subject": "Homework 2 released", "body": "Submit by Friday 23:55.", "tags": ["had_tasks"],
     "expected": None},
    {"id": 2, "sent_at": "2026-03-03T09:00:00+03:00", "sender": "news@university.edu",
     "subject": "This week on campus", "body": "Photos are up.", "tags": ["no_tasks"],
     "expected": None},
]


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = tmp_path / "emails.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in ROWS))
    monkeypatch.setattr(dev_routes, "EMAILS_FILE", path)
    monkeypatch.setattr(dev_routes, "load", lambda: [json.loads(l) for l in path.read_text().splitlines()])
    monkeypatch.setattr(dev_routes, "save", lambda rows: path.write_text("".join(json.dumps(r) + "\n" for r in rows)))
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
    app.dependency_overrides[dev_routes.local_only] = lambda: None
    yield TestClient(app), path
    app.dependency_overrides.clear()


def test_list_hides_tags(client):
    c, _ = client
    emails = c.get("/evals/emails").json()["emails"]
    assert [e["id"] for e in emails] == [1, 2]
    assert "tags" not in emails[0]  # tags say whether the model found tasks


def test_save_label_writes_the_file(client):
    c, path = client
    label = {"is_actionable": True, "tasks": [{"title": " Submit  CS204 HW2 ", "due_date": "2026-03-06", "due_time": "23:55"}]}
    r = c.put("/evals/emails/1/label", json=label)
    assert r.status_code == 200
    saved = json.loads(path.read_text().splitlines()[0])["expected"]
    assert saved == {"is_actionable": True, "tasks": [{"title": "Submit CS204 HW2", "due_date": "2026-03-06", "due_time": "23:55:00"}]}
    assert json.loads(path.read_text().splitlines()[0])["tags"] == ["had_tasks"]  # other fields untouched


def test_clear_label_and_unknown_id(client):
    c, path = client
    c.put("/evals/emails/2/label", json={"is_actionable": False, "tasks": []})
    assert c.delete("/evals/emails/2/label").json()["expected"] is None
    assert json.loads(path.read_text().splitlines()[1])["expected"] is None
    assert c.put("/evals/emails/99/label", json={"is_actionable": False}).status_code == 404


@pytest.mark.parametrize("label", [
    {"is_actionable": False, "tasks": [{"title": "x"}]},           # tasks on a non-actionable email
    {"is_actionable": True, "tasks": [{"title": "x", "due_time": "10:00"}]},  # time without a date
    {"is_actionable": True, "tasks": [{"title": "   "}]},          # empty title
])
def test_invalid_labels_are_rejected(client, label):
    c, _ = client
    assert c.put("/evals/emails/1/label", json=label).status_code == 422


def test_requests_from_other_hosts_are_refused():
    request = SimpleNamespace(client=SimpleNamespace(host="203.0.113.5"))
    with pytest.raises(Exception) as e:
        dev_routes.local_only(request)
    assert e.value.status_code == 403
    dev_routes.local_only(SimpleNamespace(client=SimpleNamespace(host="127.0.0.1")))
