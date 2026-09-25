"""Provider accounting, compaction and budget stops in the OpenRouter loop."""
import pytest

from evalbase.harness import attempt as att
from evalbase.harness.privacy import Redactor
from evalbase.harness.provider import assistant_message, usage


def response(message, cost=0.01, tokens=(10, 5), model="m/x"):
    return {"model": model, "provider": "p",
            "usage": {"prompt_tokens": tokens[0], "completion_tokens": tokens[1], "cost": cost},
            "choices": [{"finish_reason": "tool_calls" if message.get("tool_calls") else "stop", "message": message}]}


def test_missing_usage_is_an_accounting_failure():
    assert usage(response({"content": "hi"})) == (10, 5, 0.01)
    with pytest.raises(RuntimeError, match="token usage"):
        usage({"usage": {"completion_tokens": 1, "cost": 0}})
    with pytest.raises(RuntimeError, match="usage.cost"):
        usage({"usage": {"prompt_tokens": 1, "completion_tokens": 1}})
    with pytest.raises(RuntimeError, match="token usage"):
        usage({})


def test_malformed_tool_calls_are_rejected():
    ok, incomplete = assistant_message(response({"content": None, "tool_calls": [
        {"id": "a", "type": "function", "function": {"name": "shell", "arguments": "{}"}}]}))
    assert ok["tool_calls"][0]["id"] == "a" and not incomplete
    for bad in ([{"id": "a", "type": "function", "function": {"name": "shell"}}],
                [{"id": "a", "type": "custom", "function": {"name": "s", "arguments": "{}"}}],
                [{"id": "a", "type": "function", "function": {"name": "s", "arguments": "{}"}},
                 {"id": "a", "type": "function", "function": {"name": "s", "arguments": "{}"}}]):
        with pytest.raises(RuntimeError):
            assistant_message(response({"content": None, "tool_calls": bad}))
    with pytest.raises(RuntimeError, match="provider error"):
        assistant_message({"error": {"message": "nope"}})


def test_budget_stops_are_named_and_ordered():
    from evalbase.harness.openrouter import Runner
    options = att.Options(harness="openrouter", model="sim", budget_hours=1.0, max_cost_usd=1.0, max_tokens=100)
    self = Runner.__new__(Runner)
    self.options = options
    self.frozen_elapsed = 0.0
    self.state = {"manifest": {"usage": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}, "requests": 0}}
    assert self.budget_stop() is None
    self.manifest["usage"]["cost_usd"] = 1.0
    assert self.budget_stop() == "budget_cost"
    self.manifest["usage"]["cost_usd"] = 0.0
    self.manifest["usage"]["input_tokens"] = 100
    assert self.budget_stop() == "budget_tokens"
    self.manifest["usage"]["input_tokens"] = 0
    self.frozen_elapsed = 3600.0
    assert self.budget_stop() == "budget_wall"


def test_live_attempt_requires_a_key_and_a_cost_cap(tmp_path, monkeypatch, toy):
    from evalbase.harness.openrouter import Runner
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    options = att.Options(harness="openrouter", model="sim", max_cost_usd=1.0)
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        Runner(toy, options, tmp_path / "a")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-" + "b" * 40)
    with pytest.raises(RuntimeError, match="max-cost-usd"):
        Runner(toy, att.Options(harness="openrouter", model="sim"), tmp_path / "b")
    assert not (tmp_path / "a").exists() and not (tmp_path / "b").exists()


def test_compaction_replaces_context_and_is_recorded(tmp_path):
    from evalbase.harness.openrouter import Runner
    self = Runner.__new__(Runner)
    self.options = att.Options(harness="openrouter", model="sim")
    self.run_dir = tmp_path
    self.frozen_elapsed = 5.0
    self.redactor = Redactor()
    self.state = {"phase": "ready", "messages": [{"role": "system", "content": "sys"},
                                                 {"role": "user", "content": "old"},
                                                 {"role": "assistant", "content": "older"}],
                  "manifest": {"compaction_events": [], "requests": 3, "checkpoints": []}, "checkpoints": []}
    self.save = lambda: None
    self.log = lambda *a, **k: None
    self.consume_compaction(response({"content": "HANDOFF: architecture and next steps"}))
    assert len(self.state["messages"]) == 2 and "HANDOFF" in self.state["messages"][1]["content"]
    assert self.manifest["compaction_events"][0]["summary_chars"] > 0
    with pytest.raises(RuntimeError, match="usable handoff"):
        self.consume_compaction(response({"content": "", "tool_calls": []}))


def test_a_final_answer_does_not_end_a_budget_attempt():
    from evalbase.harness.openrouter import Runner
    self = Runner.__new__(Runner)
    self.options = att.Options(harness="openrouter", model="sim")
    self.state = {"messages": [], "pending": [], "phase": "ready", "manifest": {"model_final_events": 0}}
    self.save = lambda: None
    self.consume_response(response({"content": "I am done."}))
    assert self.manifest["model_final_events"] == 1
    assert self.state["messages"][-1]["role"] == "user"
    assert "budget has not ended" in self.state["messages"][-1]["content"]
