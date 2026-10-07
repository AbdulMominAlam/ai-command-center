"""Tests for the eval set: labeling endpoints and the runner's scoring."""

import json
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.auth.routes import get_current_user
from app.dev import routes as dev_routes
from app.main import app
from evals.dataset import SAMPLE_FILE, Label, load
from evals.run import one_line
from evals.scoring import EmailResult, Predicted, match_tasks, score, similarity, words

TZ = ZoneInfo("Europe/Istanbul")

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
    test_path = tmp_path / "test_emails.jsonl"
    test_path.write_text(json.dumps({**ROWS[0], "id": 7}) + "\n")
    monkeypatch.setattr(dev_routes, "SETS", {"tuning": path, "test": test_path})
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


def test_set_param_picks_the_file(client, tmp_path):
    c, path = client
    assert [e["id"] for e in c.get("/evals/emails?set=test").json()["emails"]] == [7]
    assert c.put("/evals/emails/7/label?set=test", json={"is_actionable": False, "tasks": []}).status_code == 200
    assert json.loads((tmp_path / "test_emails.jsonl").read_text())["expected"]["is_actionable"] is False
    assert all(json.loads(l)["expected"] is None for l in path.read_text().splitlines())  # tuning untouched
    assert c.put("/evals/emails/7/label", json={"is_actionable": False}).status_code == 404  # not in tuning
    assert c.get("/evals/emails?set=other").status_code == 422


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


# --- scoring ---


def test_similarity_ignores_case_stopwords_and_plurals():
    assert similarity("Submit CS204 Homework 2", "submit the cs204 homework 2") == 1.0
    assert similarity("Fill in forms", "Fill in the form") == 1.0
    assert similarity("Submit CS204 homework 2", "Submit CS204 HW 2") == 0.75
    assert similarity("Pay rent", "Attend career fair") == 0.0
    assert similarity("", "Pay rent") == 0.0


def test_punctuation_is_stripped_before_matching():
    assert words("Send the report.") == {"send", "report"}
    assert words("CS204: submit (HW2)!") == {"cs204", "submit", "hw2"}
    assert words("Reply to Ayşe's e-mail") == {"reply", "ayşe", "e", "mail"}
    assert words("Check Amazon giriş denemesi") == {"check", "amazon", "giriş", "denemesi"}
    assert similarity("Send report.", "send report") == 1.0
    assert similarity("Reply to Sarah's message", "Reply to Sarah message") == 1.0


def test_report_subjects_mask_ids_and_addresses():
    assert one_line("Account No. 06452-454323 opened") == "Account No. •••-••• opened"
    assert one_line("Talk (someone.name+x@gmail.com)\n on 29 Sep 2026") == "Talk ([email]) on 29 Sep 2026"
    assert one_line("a | b") == "a \\| b"


def test_match_tasks_is_one_to_one_best_first():
    expected = ["Submit CS204 homework 2", "Attend career fair"]
    predicted = ["Go to the career fair", "Submit homework 2 for CS204", "Submit homework 2 again"]
    assert match_tasks(expected, predicted) == [(0, 1), (1, 0)]
    assert match_tasks(["Pay rent"], ["Read newsletter"]) == []


def label(actionable, *tasks):
    return Label(is_actionable=actionable, tasks=[
        {"title": t, "due_date": d, "due_time": tm} for t, d, tm in tasks])


def at(day, hour=23, minute=59):
    return datetime(2026, 3, day, hour, minute, tzinfo=TZ)


def test_score_counts_everything():
    results = [
        # right: one task, same day, time within an hour
        EmailResult(1, "HW", label(True, ("Submit CS204 HW3", "2026-03-06", "23:55")),
                    Predicted(True, [("Submit CS204 HW3", at(6, 23, 30))])),
        # right actionable, but wrong day, plus an extra task
        EmailResult(2, "Call", label(True, ("Join intro call", "2026-03-10", "11:00")),
                    Predicted(True, [("Join intro call", at(17, 11, 0)), ("Prepare questions", None)])),
        # said actionable for a newsletter: tasks count as extra
        EmailResult(3, "News", label(False), Predicted(True, [("Read newsletter", None)])),
        # missed an actionable email
        EmailResult(4, "Survey", label(True, ("Fill survey", "2026-03-20", None)), Predicted(False, [])),
        # failed call
        EmailResult(5, "Broken", label(False), None, "invalid tool input"),
    ]
    s = score(results)
    assert (s.emails, s.failed, s.actionable_correct) == (5, 1, 3)
    assert (s.tp, s.fp, s.fn, s.tn) == (2, 1, 1, 1)
    assert (s.expected_tasks, s.predicted_tasks, s.matched_tasks) == (3, 4, 2)
    assert (s.date_checked, s.date_correct) == (2, 1)
    assert (s.time_checked, s.time_correct) == (2, 1)
    assert [m[0] for m in s.mistakes] == ["Call", "News", "Survey", "Broken"]
    assert "due date: expected Tue 10 Mar, got Tue 17 Mar" in s.mistakes[0][1]


def test_time_tolerance_and_no_due_date():
    s = score([EmailResult(1, "x", label(True, ("Meet Ayse", "2026-03-06", "14:00"), ("Read paper", None, None)),
                           Predicted(True, [("Meet Ayse", at(6, 15, 1)), ("Read paper", None)]))])
    assert (s.date_correct, s.date_checked) == (2, 2)
    assert (s.time_correct, s.time_checked) == (0, 1)  # 61 minutes late
    utc = datetime(2026, 3, 6, 11, 30, tzinfo=ZoneInfo("UTC"))  # 14:30 in Istanbul
    s = score([EmailResult(1, "x", label(True, ("Meet Ayse", "2026-03-06", "14:00")),
                           Predicted(True, [("Meet Ayse", utc)]))])
    assert (s.time_correct, s.mistakes) == (1, [])


def test_sample_file_is_valid():
    rows = load(SAMPLE_FILE)
    assert len(rows) >= 3
    for r in rows:
        Label.model_validate(r["expected"])
        assert datetime.fromisoformat(r["sent_at"]).tzinfo is not None
