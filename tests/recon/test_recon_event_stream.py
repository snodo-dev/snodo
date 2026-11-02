"""Progress-event integration tests for recon model attempts."""

import json
from types import SimpleNamespace

from snodo.recon import ReconManager
import snodo.recon as recon_module


def _tool_response():
    call = SimpleNamespace(
        id="read-1",
        function=SimpleNamespace(name="search_string", arguments=json.dumps({"query": "needle"})),
    )
    return SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content="", tool_calls=[call]), finish_reason="tool_calls",
    )])


def _answer_response():
    return SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content="Found it.", tool_calls=[]), finish_reason="stop",
    )])


def test_two_lane_recon_writes_ordered_lane_attributed_progress(tmp_path, monkeypatch):
    root = tmp_path / "project"
    (root / ".snodo").mkdir(parents=True)
    manager = ReconManager(str(root))
    recon_id = "rec_event_test"
    recon_dir = manager.recons_dir / recon_id
    recon_dir.mkdir()
    manager._save_state(recon_dir, {
        "recon_id": recon_id, "query": "find needle", "paths": ["./"],
        "agents": [["same-model"], ["same-model"]], "status": "running",
        "created_at": 1.0, "completed_at": None,
    })
    monkeypatch.setattr(recon_module, "resolve_agent_model", lambda model: model)
    from snodo.config import ConfigManager
    monkeypatch.setattr(ConfigManager, "resolve_litellm_model", staticmethod(lambda model: model))
    monkeypatch.setattr(ConfigManager, "resolve_api_base", staticmethod(lambda model: None))
    monkeypatch.setattr(ConfigManager, "resolve_extra_headers", staticmethod(lambda model, task_id=None: None))
    monkeypatch.setattr(ConfigManager, "get_key_for_model", lambda self, model: None)
    def completion(**kwargs):
        if any(message.get("role") == "tool" for message in kwargs["messages"]):
            return _answer_response()
        return _tool_response()
    monkeypatch.setattr("litellm.completion", completion)
    monkeypatch.setattr(recon_module, "_execute_recon_read", lambda *args: "safe result")

    manager._run_recon_impl(recon_id, "find needle", ["./"], [["same-model"], ["same-model"]], max_tool_turns=2)
    from snodo.recon.event_log import ReconEventLog
    events, _ = ReconEventLog(manager._recon_dir(recon_id)).read()
    assert sorted({event["lane"] for event in events}) == [0, 1]
    for lane in (0, 1):
        lane_events = [event for event in events if event["lane"] == lane]
        assert [event["kind"] for event in lane_events] == ["start", "tool_turn", "finish"]
        assert lane_events[1]["detail"]["tools"][0]["name"] == "search_string"
        assert lane_events[1]["detail"]["tools"][0]["arguments"] == {"query": "needle"}


def test_failover_raising_observer_and_default_no_observer(monkeypatch):
    calls = []
    def fake_call(*args, **kwargs):
        calls.append(kwargs.get("observer"))
        if len(calls) == 1:
            return recon_module.ReconResult(agent="agent", model="one", result="", error="provider unavailable")
        return recon_module.ReconResult(agent="agent", model="two", result="answer")
    monkeypatch.setattr(recon_module, "call_agent", fake_call)
    events = []
    result = recon_module.call_agent_chain(".", ["one", "two"], "q", [], "agent", observer=lambda event: events.append(event.kind))
    assert result.result == "answer"
    assert events == ["failover"]
    assert recon_module.call_agent_chain(".", ["one"], "q", [], "agent").result == "answer"
    assert calls[-1] is None

    def raising(_event):
        raise RuntimeError("observer unavailable")
    def observed_call(*args, **kwargs):
        recon_module._notify_recon_observer(kwargs.get("observer"), "one", "agent", "start", {})
        return recon_module.ReconResult(agent="agent", model="one", result="answer")

    monkeypatch.setattr(recon_module, "call_agent", observed_call)
    assert recon_module.call_agent_chain(".", ["one"], "q", [], "agent", observer=raising).result == "answer"
