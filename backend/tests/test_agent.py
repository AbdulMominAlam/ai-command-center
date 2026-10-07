"""Chat agent tests with a fake Anthropic client and a fake database session."""

import copy
import json
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.llm import agent
from app.models import LLMUsage, PendingAction

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=ZoneInfo("Europe/Istanbul"))
USER = SimpleNamespace(id=1)


def text(t):
    return SimpleNamespace(type="text", text=t)


def tool_use(id, name, input):
    return SimpleNamespace(type="tool_use", id=id, name=name, input=input)


def response(stop_reason, *content, input_tokens=100, output_tokens=20):
    return SimpleNamespace(stop_reason=stop_reason, content=list(content),
                           usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens))


class FakeLLM:
    """Returns the given responses in order and records every request."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        return self.responses.pop(0)


class FakeDB:
    def __init__(self):
        self.added = []
        self.commits = 0

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        for i, obj in enumerate(self.added, start=1):
            if getattr(obj, "id", None) is None:
                obj.id = i

    def commit(self):
        self.commits += 1


def tool_results(call) -> list[dict]:
    return call["messages"][-1]["content"]


def test_read_only_question(monkeypatch):
    seen = {}

    def fake_list_tasks(db, user, args):
        seen["args"] = args
        return {"tasks": [{"cite": "task 7", "title": "Submit CS204 HW2", "due": "2026-10-09T23:59+03:00 (Friday)",
                           "priority": "high", "status": "open", "source": "sucourse", "from_item": 90}],
                "truncated": False}

    monkeypatch.setitem(agent.READ_TOOLS, "list_tasks", (agent.ListTasksInput, fake_list_tasks))
    llm = FakeLLM(
        response("tool_use", text("Let me check."),
                 tool_use("t1", "list_tasks", {"due_before": "2026-10-09T23:59:00+03:00"})),
        response("end_turn", text("You need to submit CS204 HW2 by Friday [task 7].")),
    )
    db = FakeDB()

    result = agent.run_agent(db, USER, [{"role": "user", "content": "What's due before Friday?"}],
                             llm=llm, now=NOW)

    assert result["reply"] == "You need to submit CS204 HW2 by Friday [task 7]."
    assert result["pending_actions"] == []
    assert result["tool_calls"] == ["list_tasks"]
    assert result["rounds"] == 2
    assert (result["input_tokens"], result["output_tokens"]) == (200, 40)
    assert result["estimated_cost_usd"] == agent.estimate_cost(agent.settings.AGENT_MODEL, 200, 40)

    # The tool got the parsed filter, and its result went back to Claude.
    assert seen["args"].status == "open"
    assert seen["args"].due_before.isoformat() == "2026-10-09T23:59:00+03:00"
    [res] = tool_results(llm.calls[1])
    assert res["tool_use_id"] == "t1" and not res["is_error"]
    assert json.loads(res["content"])["tasks"][0]["cite"] == "task 7"

    # The static system prompt is cached; today's date comes after it, so it
    # doesn't break the cache. Every call is logged as agent usage.
    static, now_block = llm.calls[0]["system"]
    assert static["text"] == agent.SYSTEM_PROMPT and static["cache_control"] == {"type": "ephemeral"}
    assert "Tuesday 2026-10-06 12:00" in now_block["text"] and "cache_control" not in now_block
    assert "{now}" not in agent.SYSTEM_PROMPT
    assert llm.calls[0]["cache_control"] == {"type": "ephemeral"}  # caches the conversation between rounds
    usage = [o for o in db.added if isinstance(o, LLMUsage)]
    assert [u.purpose for u in usage] == ["agent", "agent"]
    assert db.commits == 1


def test_create_task_is_saved_as_pending_not_created():
    llm = FakeLLM(
        response("tool_use", tool_use("t1", "create_task", {
            "title": "Email Prof. Demir about the make-up exam",
            "due_at": "2026-10-08T17:00:00+03:00",
            "priority": "medium",
        })),
        response("end_turn", text("I proposed the task. Please confirm it.")),
    )
    db = FakeDB()

    result = agent.run_agent(db, USER, [{"role": "user", "content": "Remind me to email Prof. Demir by Thursday 5pm"}],
                             llm=llm, now=NOW)

    [action] = [o for o in db.added if isinstance(o, PendingAction)]
    assert action.kind == "create_task" and action.status == "pending"
    assert action.payload == {"title": "Email Prof. Demir about the make-up exam",
                              "due_at": "2026-10-08T17:00:00+03:00", "priority": "medium"}
    assert result["pending_actions"] == [{"id": action.id, "kind": "create_task", "status": "pending",
                                          **action.payload}]
    # No Task row was added: only the pending action and the usage rows.
    assert {type(o) for o in db.added} == {PendingAction, LLMUsage}
    [res] = tool_results(llm.calls[1])
    assert json.loads(res["content"])["status"] == "pending"


def test_invalid_tool_input_is_returned_as_an_error():
    llm = FakeLLM(
        response("tool_use", tool_use("t1", "create_task", {"title": "X", "due_at": "Friday", "priority": "urgent"})),
        response("end_turn", text("Sorry, I could not create that task.")),
    )
    db = FakeDB()

    result = agent.run_agent(db, USER, [{"role": "user", "content": "Add X"}], llm=llm, now=NOW)

    [res] = tool_results(llm.calls[1])
    assert res["is_error"]
    assert "due_at" in res["content"] and "priority" in res["content"]
    assert result["pending_actions"] == []


def test_loop_stops_after_five_rounds(monkeypatch):
    monkeypatch.setitem(agent.READ_TOOLS, "search_items",
                        (agent.SearchItemsInput, lambda db, user, args: {"results": []}))
    llm = FakeLLM(*[
        response("tool_use", tool_use(f"t{i}", "search_items", {"query": f"try {i}"}))
        for i in range(10)
    ])
    db = FakeDB()

    result = agent.run_agent(db, USER, [{"role": "user", "content": "Find it"}], llm=llm, now=NOW)

    assert len(llm.calls) == agent.MAX_ROUNDS == 5
    assert result["rounds"] == 5
    assert result["stopped_early"]
    assert "stopped after 5 steps" in result["reply"]
    assert result["tool_calls"] == ["search_items"] * 4  # the 5th call's tool is never run
    assert result["input_tokens"] == 500
    assert len([o for o in db.added if isinstance(o, LLMUsage)]) == 5


def test_like_pattern_escapes_wildcards():
    assert agent._like("50%_off") == r"%50\%\_off%"


def test_sensitive_emails_are_hidden_from_search():
    def email(title, sender=None, summary=None):
        return SimpleNamespace(type="email", title=title, sender=sender, summary=summary)

    assert agent._hidden_email(email("Your OTP is 123456"))
    assert agent._hidden_email(email("Receipt", "NayaPay <no-reply@nayapay.com>",
                                     "NayaPay receipt confirming a money transfer."))
    assert not agent._hidden_email(email("Homework 2 released", "cs204@university.edu",
                                         "CS204 Homework 2 is due Friday."))


def test_unknown_agent_model_reports_no_cost(monkeypatch):
    monkeypatch.setattr(agent.settings, "AGENT_MODEL", "claude-some-future-model")
    llm = FakeLLM(response("end_turn", text("Hello.")))

    result = agent.run_agent(FakeDB(), USER, [{"role": "user", "content": "Hi"}], llm=llm, now=NOW)

    assert result["estimated_cost_usd"] is None
    assert result["input_tokens"] == 100  # tokens are still counted


class QueryDB:
    """Answers execute() and scalars() with canned rows, in call order."""

    def __init__(self, task_rows, undated):
        self.task_rows, self.undated = task_rows, undated
        self.scalar_calls = 0

    def execute(self, stmt):
        return SimpleNamespace(all=lambda: self.task_rows)

    def scalars(self, stmt):
        self.scalar_calls += 1
        return SimpleNamespace(all=lambda: self.undated)


def _task(id, title, due_at=None):
    return SimpleNamespace(id=id, title=title, due_at=due_at, priority="medium",
                           status="open", created_by="user", item_id=None)


def test_date_filtered_list_tasks_also_returns_undated_open_tasks():
    db = QueryDB([(_task(1, "Submit HW2", NOW), "sucourse")], [_task(3, "Renew library card")])
    args = agent.ListTasksInput(due_before="2026-10-09T23:59:00+03:00")

    result = agent.list_tasks(db, USER, args)

    assert [t["cite"] for t in result["tasks"]] == ["task 1"]
    assert result["open_without_due_date"] == [
        {"cite": "task 3", "title": "Renew library card", "priority": "medium"}]
    assert "open_without_due_date" in agent.SYSTEM_PROMPT


def test_unfiltered_list_tasks_skips_the_undated_query():
    db = QueryDB([(_task(3, "Renew library card"), None)], [])

    result = agent.list_tasks(db, USER, agent.ListTasksInput())

    assert "open_without_due_date" not in result
    assert db.scalar_calls == 0
