"""Tests for ReconManager and ReconToolHandler.

FILE: tests/recon/test_recon.py
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import snodo.recon as recon_module
from snodo.recon import ReconError, ReconManager, ReconResult, ReconState


def test_call_agent_passes_each_model_key_without_environment_state(monkeypatch):
    """Routed and native providers keep credentials isolated in one process."""
    from snodo.config import ConfigManager

    keys = {
        "custom/model": "custom-secret",
        "gpt-4o": "openai-secret",
    }
    monkeypatch.setattr(ConfigManager, "get_key_for_model", lambda self, model: keys.get(model))
    monkeypatch.setattr(ConfigManager, "resolve_litellm_model", staticmethod(
        lambda model: "openai/model" if model == "custom/model" else model
    ))
    monkeypatch.setattr(ConfigManager, "resolve_api_base", staticmethod(lambda model: None))
    monkeypatch.setattr(ConfigManager, "resolve_extra_headers", staticmethod(lambda model, task_id=None: None))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        return MagicMock(choices=[MagicMock(message=MagicMock(content="answer", tool_calls=[]))])

    with patch("litellm.completion", side_effect=completion):
        recon_module.call_agent(".", "custom/model", "query", [], "custom", max_turns=1)
        recon_module.call_agent(".", "gpt-4o", "query", [], "openai", max_turns=1)

    assert [call["api_key"] for call in calls] == ["custom-secret", "openai-secret"]
    assert "OPENAI_API_KEY" not in os.environ


def test_call_agent_passes_configured_completion_budget(monkeypatch):
    from snodo.config import ConfigManager

    monkeypatch.setattr(ConfigManager, "resolve_litellm_model", staticmethod(lambda model: model))
    monkeypatch.setattr(ConfigManager, "resolve_api_base", staticmethod(lambda model: None))
    monkeypatch.setattr(ConfigManager, "resolve_extra_headers", staticmethod(lambda model, task_id=None: None))
    monkeypatch.setattr(ConfigManager, "get_key_for_model", lambda self, model: None)
    completion = MagicMock(
        return_value=MagicMock(choices=[MagicMock(message=MagicMock(content="answer", tool_calls=[]))])
    )
    with patch("litellm.completion", completion):
        recon_module.call_agent(".", "model", "query", [], "agent", max_turns=17, max_tokens=2300)
    assert completion.call_args.kwargs["max_tokens"] == 2300


def test_recon_uses_shared_read_tools_lines_and_free_repeat(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from snodo.config import ConfigManager

    (tmp_path / "sample.py").write_text("alpha\nneedle\nomega\n")
    monkeypatch.setattr(ConfigManager, "resolve_litellm_model", staticmethod(lambda model: model))
    monkeypatch.setattr(ConfigManager, "resolve_api_base", staticmethod(lambda model: None))
    monkeypatch.setattr(ConfigManager, "resolve_extra_headers", staticmethod(lambda model, task_id=None: None))
    monkeypatch.setattr(ConfigManager, "get_key_for_model", lambda self, model: None)

    def tool_call(call_id, name, args):
        return SimpleNamespace(id=call_id, function=SimpleNamespace(
            name=name, arguments=json.dumps(args),
        ))

    responses = [
        [tool_call("s", "search_string", {"query": "needle"})],
        [tool_call("r", "read_file_lines", {"path": "sample.py", "start": 2, "end": 2})],
        [tool_call("repeat", "read_file_lines", {"path": "sample.py", "start": 2, "end": 2})],
    ]
    requests = []

    def completion(**kwargs):
        requests.append(kwargs)
        calls = responses.pop(0) if responses else []
        content = "answer" if not calls else ""
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=content, tool_calls=calls,
        ))])

    with patch("litellm.completion", side_effect=completion):
        result = recon_module.call_agent(str(tmp_path), "model", "find", [], "agent", max_turns=4)

    offered = {tool["function"]["name"] for tool in requests[0]["tools"]}
    assert {"read_file_lines", "search_string", "search_symbol", "read_files"} <= offered
    assert "run_tests" not in offered and "write_file" not in offered
    contents = [message.get("content", "") for message in requests[-1]["messages"]]
    assert any("needle" in content for content in contents)
    assert any("2: needle" in content for content in contents)
    assert any("already fetched" in content for content in contents)
    assert result.result == "answer"


def test_recon_read_tools_reject_path_traversal(tmp_path):
    from snodo.tools.workspace import WorkspaceMCP
    from snodo.coders.litellm import LiteLLMAdapter

    outside = tmp_path.parent / "outside-recon.txt"
    outside.write_text("secret")
    result = LiteLLMAdapter._execute_tool(
        "read_file", {"path": "../outside-recon.txt"}, WorkspaceMCP(str(tmp_path)),
    )
    assert "path" in result.lower() or "outside" in result.lower()
    assert "secret" not in result


def test_recon_agent_default_budgets_are_sized_for_exploration():
    from snodo.infrastructure.config import ValidatorConfig, ReconConfig

    assert ReconConfig().max_tool_turns == 40
    assert ReconConfig().max_tokens == 16000
    assert ReconConfig().deadline_seconds == 300
    assert ValidatorConfig().max_tool_turns == 6
    assert ValidatorConfig().max_tokens == 1500


def test_recon_deadline_is_loaded_from_config(tmp_path, monkeypatch):
    from snodo.config import ConfigManager
    (tmp_path / ".snodo").mkdir()
    monkeypatch.setattr(ConfigManager, "load", lambda self: {"llm": {"recon": {"deadline_seconds": 19}}})
    from snodo.mcp.recon_handlers import ReconToolHandler

    submitted = {}
    monkeypatch.setattr(ReconManager, "submit", lambda self, *args, **kwargs: submitted.update(kwargs) or "rec_test")
    result = ReconToolHandler(str(tmp_path)).handle_recon({"query": "q", "paths": ["./"]})
    assert result["recon_id"] == "rec_test"
    assert submitted["deadline_seconds"] == 19


def test_credit_billing_exhaustion_is_not_transient():
    from snodo.infrastructure.provider_errors import is_transient_provider_error

    assert not is_transient_provider_error("credit balance exhausted / billing")


@pytest.mark.parametrize(
    ("configured_model", "litellm_model"),
    [
        ("google/gemini/gemini-3.8-flash", "gemini/gemini-3.8-flash"),
        ("openai/gpt-5.6-terra", "openai/gpt-5.6-terra"),
        ("ollama-cloud/deepseek-v4-pro:0813", "ollama-cloud/deepseek-v4-pro:0813"),
    ],
)
def test_recon_routes_resolved_model_to_litellm(
    monkeypatch, configured_model, litellm_model
):
    """Recon hands LiteLLM its routing name, preserving valid model ids."""
    from snodo.config import ConfigManager

    # Exercise the actual shared resolver while keeping provider configuration
    # hermetic and avoiding reads from the user's home directory.
    monkeypatch.setattr(
        ConfigManager,
        "_provider_for_model",
        staticmethod(lambda model: "google" if model.startswith("google/") else None),
    )
    monkeypatch.setattr(
        ConfigManager,
        "get_providers",
        lambda self: {"google": type("Provider", (), {"litellm_provider": "gemini"})()},
    )
    monkeypatch.setattr(ConfigManager, "resolve_api_base", staticmethod(lambda model: None))
    monkeypatch.setattr(ConfigManager, "resolve_extra_headers", staticmethod(lambda model, task_id=None: None))
    monkeypatch.setattr(ConfigManager, "get_key_for_model", lambda self, model: None)

    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        return MagicMock(choices=[MagicMock(message=MagicMock(content="answer", tool_calls=[]))])

    with patch("litellm.completion", side_effect=completion):
        recon_module.call_agent(".", configured_model, "query", [], "recon", max_turns=1)

    assert calls[0]["model"] == litellm_model


def test_failed_failover_chain_keeps_trace_and_partial_answer(monkeypatch):
    traces = [
        ReconResult(agent="agent", model="first", result="Partial finding",
                    error="provider failed", trace={"turns_used": 4,
                    "tools_called": ["search_string"], "ended": "error"}),
        ReconResult(agent="agent", model="second", result="", error="provider unavailable",
                    trace={"turns_used": 0, "tools_called": [], "ended": "error"}),
    ]
    monkeypatch.setattr(recon_module, "call_agent", lambda *args: traces.pop(0))
    monkeypatch.setattr("time.sleep", lambda *_: None)
    result = recon_module.call_agent_chain(".", ["first", "second"], "query", [], "agent")

    assert result.result == "Partial finding"
    assert result.trace == {"turns_used": 4, "tools_called": ["search_string"], "ended": "error"}


def test_failover_chain_keeps_zero_turn_trace(monkeypatch):
    monkeypatch.setattr(
        recon_module, "call_agent",
        lambda *args: ReconResult(agent="agent", model="model", result="",
                                  error="provider unavailable",
                                  trace={"turns_used": 0, "tools_called": [], "ended": "error"}),
    )
    result = recon_module.call_agent_chain(".", ["model"], "query", [], "agent")
    assert result.trace == {"turns_used": 0, "tools_called": [], "ended": "error"}


def test_successful_failover_trace_is_unchanged(monkeypatch):
    trace = {"turns_used": 2, "tools_called": ["read_file"], "ended": "prose"}
    monkeypatch.setattr(
        recon_module, "call_agent",
        lambda *args: ReconResult(agent="agent", model="model", result="answer", trace=trace),
    )
    result = recon_module.call_agent_chain(".", ["model"], "query", [], "agent")
    assert result.trace == trace


@pytest.fixture
def project_with_snodo():
    """Create a temp project with .snodo dir."""
    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp) / "myproject"
        project_root.mkdir()
        (project_root / ".snodo").mkdir()
        yield str(project_root)


@pytest.fixture
def recon_mgr(project_with_snodo, monkeypatch):
    """ReconManager with worker execution isolated from state assertions.

    The production ``submit()`` call saves state, starts a worker thread,
    and returns.  Reading ``state.json`` immediately afterwards is racy unless
    the worker body is isolated; tests below assert the state created by
    ``submit()`` itself, not whether a real worker happened to finish first.
    """
    monkeypatch.setattr(recon_module, "_threads", [])
    monkeypatch.setattr(ReconManager, "_run_recon", lambda *args, **kwargs: None)
    from snodo.infrastructure.audit import AuditLog
    monkeypatch.setattr(
        recon_module, "_audit_log_for_project",
        lambda root: AuditLog(str(Path(root) / ".snodo" / "audit.log")),
    )

    mgr = ReconManager(project_with_snodo)
    yield mgr
    mgr.shutdown()


def _record_recon(mgr, status, results, recon_id="rec_recorded"):
    """Write a recon to disk as the worker would, without running one."""
    recon_dir = Path(mgr.recons_dir) / recon_id
    recon_dir.mkdir()
    state = {
        "recon_id": recon_id,
        "query": "what does this do?",
        "paths": ["./"],
        "agents": [["default"]],
        "status": status,
        "created_at": 1.0,
        "completed_at": 2.0,
    }
    (recon_dir / "state.json").write_text(json.dumps(state))
    (recon_dir / "results.json").write_text(json.dumps(results))
    return recon_id, state


# ------------------------------------------------------------------#
# ReconManager tests
# ------------------------------------------------------------------#

class TestReconManagerConstruction:
    def test_creates_recons_dir(self, project_with_snodo):
        ReconManager(project_with_snodo)
        recons_dir = Path(project_with_snodo) / ".snodo" / "recons"
        assert recons_dir.is_dir()

    def test_fails_without_snodo_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            with pytest.raises(ValueError, match="Not a snodo project"):
                ReconManager(tmp)


class TestReconManagerSubmit:
    def test_submit_creates_state_and_returns_id(self, recon_mgr):
        recon_id = recon_mgr.submit("What does this code do?", ["./"])

        assert recon_id.startswith("rec_")
        recon_dir = Path(recon_mgr.recons_dir) / recon_id
        assert recon_dir.is_dir()

        state_path = recon_dir / "state.json"
        assert state_path.exists()
        state = json.loads(state_path.read_text())
        assert state["recon_id"] == recon_id
        assert state["query"] == "What does this code do?"
        assert state["paths"] == ["./"]
        assert state["agents"] == [["default"]]
        assert state["status"] == "running"
        assert "created_at" in state
        assert state["pid"] == os.getpid()

    def test_submit_forwards_budgets_to_agent_run(self, recon_mgr, monkeypatch):
        budgets = {}

        def run_impl(self, recon_id, query, paths, agents, max_tool_turns, max_tokens, deadline_seconds):
            budgets.update(max_tool_turns=max_tool_turns, max_tokens=max_tokens,
                           deadline_seconds=deadline_seconds)

        monkeypatch.setattr(ReconManager, "_run_recon_impl", run_impl)
        monkeypatch.setattr(
            ReconManager, "_run_recon",
            lambda self, recon_id, query, paths, agents, max_tool_turns=6, max_tokens=1500,
            deadline_seconds=300:
                self._run_recon_impl(recon_id, query, paths, agents, max_tool_turns, max_tokens,
                                     deadline_seconds),
        )
        recon_mgr.submit("q", ["./"], [["model"]], max_tool_turns=23, max_tokens=4200)
        recon_mgr.shutdown()
        assert budgets == {"max_tool_turns": 23, "max_tokens": 4200, "deadline_seconds": 300}

    def test_submit_appends_full_recon_started_event(self, recon_mgr):
        query = "Explain the entire system, including its edge cases."
        agents = [["model-a", "model-b"], ["model-c"]]

        recon_id = recon_mgr.submit(query, ["src/", "docs/"], agents=agents)
        from snodo.infrastructure.audit import _LOCAL_EVENT_DATA_KEYS
        events = recon_module._audit_log_for_project(recon_mgr.project_root).get_history()

        assert events[-1].event_type == "recon_started"
        assert set(events[-1].data) == set(_LOCAL_EVENT_DATA_KEYS["recon_started"])
        assert events[-1].data == {
            "recon_id": recon_id,
            "query": query,
            "paths": ["src/", "docs/"],
            "agent_count": 2,
            "agent_models": agents,
            "session_id": "",
            "created_at": json.loads(
                (Path(recon_mgr.recons_dir) / recon_id / "state.json").read_text()
            )["created_at"],
        }

    def test_submit_custom_agents(self, recon_mgr):
        agents = ["gpt-4", "gemini/gemini-2.0-flash-exp"]
        recon_id = recon_mgr.submit("analyze", ["src/"], agents=agents)

        recon_dir = Path(recon_mgr.recons_dir) / recon_id
        state = json.loads((recon_dir / "state.json").read_text())
        assert state["agents"] == [[agent] for agent in agents]

    def test_submit_starts_background_worker(self, project_with_snodo, monkeypatch):
        class FakeThread:
            def __init__(self, target=None, args=(), kwargs=None):
                self.target = target
                self.args = args
                self.kwargs = kwargs or {}
                self.started = False

            def start(self):
                self.started = True

        fake_threads = []

        def fake_thread(*args, **kwargs):
            thread = FakeThread(*args, **kwargs)
            fake_threads.append(thread)
            return thread

        monkeypatch.setattr(recon_module, "Thread", fake_thread)
        monkeypatch.setattr(recon_module, "_threads", [])
        monkeypatch.setattr(ReconManager, "_run_recon", lambda *args, **kwargs: None)

        mgr = ReconManager(project_with_snodo)
        recon_id = mgr.submit("query", ["./"])

        assert len(fake_threads) == 1
        assert fake_threads[0].started is True
        assert fake_threads[0].args == (recon_id, "query", ["./"], [["default"]])


class TestReconManagerGetStatus:
    def test_get_status_running(self, recon_mgr):
        recon_id = recon_mgr.submit("query", ["./"])
        status = recon_mgr.get_status(recon_id)
        assert status["status"] == "running"
        assert status["recon_id"] == recon_id

    def test_get_status_not_found(self, recon_mgr):
        with pytest.raises(ReconError, match="not found"):
            recon_mgr.get_status("rec_nonexistent")


class TestReconManagerGetResults:
    def test_get_results_before_complete_raises(self, recon_mgr):
        recon_id = recon_mgr.submit("query", ["./"])
        with pytest.raises(ReconError, match="not complete"):
            recon_mgr.get_results(recon_id)


@pytest.mark.parametrize(
    ("result", "error", "status", "succeeded", "failed"),
    [
        ("The answer from recon.", None, "complete", 1, 0),
        ("", "provider failed", "failed", 0, 1),
    ],
)
def test_recon_started_and_completed_events_cover_terminal_outcomes(
    recon_mgr, monkeypatch, result, error, status, succeeded, failed,
):
    def fake_chain(project_root, models, query, paths, agent_label, max_turns=6, max_tokens=1500):
        return ReconResult(agent=agent_label, model=models[0], result=result, error=error)

    monkeypatch.setattr(recon_module, "call_agent_chain", fake_chain)
    recon_id = recon_mgr.submit("What happened?", ["./"], agents=["model-a"])
    recon_mgr._run_recon_impl(recon_id, "What happened?", ["./"], [["model-a"]])

    audit = recon_module._audit_log_for_project(recon_mgr.project_root)
    events = audit.get_history()
    assert [event.event_type for event in events] == ["recon_started", "recon_completed"]
    completed = events[-1].data
    from snodo.infrastructure.audit import _LOCAL_EVENT_DATA_KEYS
    assert set(completed) == set(_LOCAL_EVENT_DATA_KEYS["recon_completed"])
    assert completed["recon_id"] == recon_id
    assert completed["status"] == status
    assert completed["succeeded_agents"] == succeeded
    assert completed["failed_agents"] == failed
    assert completed["duration"] >= 0
    assert completed["completed_at"] is not None
    assert completed["summary"] == result
    assert audit.verify_chain()


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("status", ["complete", "failed"])
def test_recon_completion_starts_cloud_sync_only_when_enabled(
    recon_mgr, monkeypatch, enabled, status,
):
    import threading

    from snodo.infrastructure import cloud_sync

    recon_id = recon_mgr.submit("query", ["./"])
    recon_dir = Path(recon_mgr.recons_dir) / recon_id
    state = json.loads((recon_dir / "state.json").read_text())
    state["status"] = status
    state["completed_at"] = state["created_at"] + 1

    called = threading.Event()
    monkeypatch.setattr(recon_module, "_active_session_id", lambda _root: "")
    monkeypatch.setattr(
        "snodo.config.ConfigManager.load",
        lambda _self: {"cloud": {"sync_enabled": enabled, "api_key": "key"}},
    )
    monkeypatch.setattr(
        cloud_sync.CloudSyncDispatcher, "sync",
        lambda *args, **kwargs: called.set() or {"synced": 0, "failed": False, "pending": 0},
    )
    before = len(cloud_sync._pending_syncs)
    recon_mgr._append_completion_event(state, [])

    if enabled:
        assert called.wait(timeout=1)
    else:
        assert not called.wait(timeout=0.05)
    for thread, *_ in cloud_sync._pending_syncs[before:]:
        thread.join(timeout=1)
    del cloud_sync._pending_syncs[before:]


def test_recon_events_use_the_project_id_of_neighbouring_audit_events(
    project_with_snodo, monkeypatch,
):
    from snodo.infrastructure.audit import AuditLog, reset_global_audit_log

    project_id = "github.com/example/project"
    project_root = Path(project_with_snodo)
    (project_root / ".snodo" / "project.json").write_text(json.dumps({
        "id": project_id,
        "project.id": project_id,
        "scope": "remote",
    }))
    audit_path = project_root / ".snodo" / "audit.log"
    neighboring_event = AuditLog(str(audit_path), project_id=project_id)
    neighboring_event.append_event("dispatch", {"task_id": "task-1"})

    monkeypatch.setattr(recon_module, "_threads", [])
    monkeypatch.setattr(ReconManager, "_run_recon", lambda *args, **kwargs: None)
    reset_global_audit_log()
    try:
        manager = ReconManager(str(project_root))
        recon_id = manager.submit("Inspect the project", ["./"])
        recon_dir = Path(manager.recons_dir) / recon_id
        state = json.loads((recon_dir / "state.json").read_text())
        state["status"] = "complete"
        state["completed_at"] = state["created_at"] + 1
        manager._append_completion_event(state, [])

        events = AuditLog(str(audit_path)).get_history()
        assert [event.event_type for event in events] == [
            "dispatch", "recon_started", "recon_completed",
        ]
        assert {event.project_id for event in events} == {project_id}
    finally:
        reset_global_audit_log()


def test_recon_completion_summary_is_capped_with_truncation_marker(recon_mgr):
    long_answer = "answer " * 400
    recon_id = recon_mgr.submit("Summarize", ["./"])
    recon_dir = Path(recon_mgr.recons_dir) / recon_id
    state = json.loads((recon_dir / "state.json").read_text())
    state["status"] = "complete"
    state["completed_at"] = state["created_at"] + 1
    results = [{"agent": "default", "result": long_answer, "error": None}]
    recon_mgr._save_results(recon_dir, results)
    recon_module.ReconManager._save_state(recon_mgr, recon_dir, state)

    recon_mgr._append_completion_event(state, results)
    event = recon_module._audit_log_for_project(recon_mgr.project_root).get_history()[-1]

    assert event.event_type == "recon_completed"
    assert len(event.data["summary"]) == recon_module._RECON_SUMMARY_LIMIT
    assert event.data["summary"].endswith(recon_module._RECON_SUMMARY_MARKER)


# ------------------------------------------------------------------#
# A terminal recon says why it stopped (Fixes #327)
# ------------------------------------------------------------------#

_FAILURE_REASON = (
    "all models failed; tried m1 (litellm.BadRequestError: "
    "LLM Provider NOT provided)"
)


def _failed_results():
    return [{
        "agent": "default",
        "model": "m1",
        "result": "",
        "error": _FAILURE_REASON,
        "attempts": [{"model": "m1", "error": "LLM Provider NOT provided"}],
    }]


class TestFailedReconCarriesItsReason:
    def test_get_status_hands_over_the_reason(self, recon_mgr):
        recon_id, state = _record_recon(recon_mgr, "failed", _failed_results())

        status = recon_mgr.get_status(recon_id)

        assert status["status"] == "failed"
        assert status["results"] == _failed_results()
        assert _FAILURE_REASON in status["results"][0]["error"]

    def test_get_results_hands_over_the_reason(self, recon_mgr):
        recon_id, state = _record_recon(recon_mgr, "failed", _failed_results())

        out = recon_mgr.get_results(recon_id)

        assert out["status"] == "failed"
        assert out["results"] == _failed_results()
        assert _FAILURE_REASON in out["results"][0]["error"]

    def test_running_recon_reports_persisted_results_and_pending_agents(self, recon_mgr):
        recon_id = recon_mgr.submit("query", ["./"])
        recon_dir = recon_mgr._recon_dir(recon_id)
        state = recon_mgr._load_state(recon_dir)
        state["agents"] = [["done"], ["pending"]]
        recon_mgr._save_state(recon_dir, state)
        results = [{"agent": "done", "result": "finished answer", "error": None}]
        recon_mgr._save_results(recon_dir, results)

        status = recon_mgr.get_status(recon_id)
        assert status["status"] == "running"
        assert status["results"] == results
        assert status["pending_agents"] == ["pending"]
        with pytest.raises(ReconError, match="not complete"):
            recon_mgr.get_results(recon_id)

    def test_complete_recon_returns_exactly_what_it_did(self, recon_mgr):
        results = [{
            "agent": "default",
            "model": "m1",
            "result": "the answer",
            "error": None,
            "attempts": [{"model": "m1", "error": None}],
        }]
        recon_id, state = _record_recon(recon_mgr, "complete", results)

        assert recon_mgr.get_status(recon_id) == {
            **state, "results": results, "pending_agents": [],
        }
        assert recon_mgr.get_results(recon_id) == {
            "recon_id": recon_id,
            "status": "complete",
            "results": results,
        }

    def test_mcp_status_handler_hands_over_the_reason(self, project_with_snodo):
        from snodo.mcp.recon_handlers import ReconToolHandler

        mgr = ReconManager(project_with_snodo)
        recon_id, _ = _record_recon(mgr, "failed", _failed_results())
        handler = ReconToolHandler(project_with_snodo)

        out = handler.handle_get_recon_status({"recon_id": recon_id})

        assert out["results"][0]["error"] == _FAILURE_REASON

    def test_mcp_results_handler_hands_over_the_reason(self, project_with_snodo):
        from snodo.mcp.recon_handlers import ReconToolHandler

        mgr = ReconManager(project_with_snodo)
        recon_id, _ = _record_recon(mgr, "failed", _failed_results())
        handler = ReconToolHandler(project_with_snodo)

        out = handler.handle_get_recon_results({"recon_id": recon_id})

        assert out["results"][0]["error"] == _FAILURE_REASON


class TestReconManagerListRecons:
    def test_list_empty(self, recon_mgr):
        recons = recon_mgr.list_recons()
        assert recons == []

    def test_list_returns_submitted_recons(self, recon_mgr):
        recon_mgr.submit("first query", ["./"])
        recon_mgr.submit("second query", ["src/"])

        recons = recon_mgr.list_recons()
        assert len(recons) == 2
        assert recons[0]["query"] == "second query"
        assert recons[1]["query"] == "first query"

    def test_list_respects_limit(self, recon_mgr):
        for i in range(5):
            recon_mgr.submit(f"query {i}", ["./"])
        recons = recon_mgr.list_recons(limit=2)
        assert len(recons) == 2


# ------------------------------------------------------------------#
# ReconModel tests
# ------------------------------------------------------------------#

class TestReconState:
    def test_creation(self):
        state = ReconState(
            recon_id="rec_test",
            query="q",
            paths=["./"],
            agents=["default"],
            status="running",
            created_at=0.0,
        )
        assert state.recon_id == "rec_test"
        assert state.status == "running"
        assert state.completed_at is None


class TestReconResult:
    def test_success_result(self):
        result = ReconResult(
            agent="default",
            model="gpt-4",
            result="The codebase uses FastAPI.",
        )
        assert result.error is None
        assert result.result == "The codebase uses FastAPI."

    def test_error_result(self):
        result = ReconResult(
            agent="openai",
            model="gpt-4",
            result="",
            error="API key missing",
        )
        assert result.error == "API key missing"
        assert result.result == ""


# ------------------------------------------------------------------#
# ReconToolHandler tests
# ------------------------------------------------------------------#

class TestReconToolHandler:
    def test_handle_recon_requires_query(self, project_with_snodo):
        from snodo.mcp.recon_handlers import ReconToolHandler
        from snodo.mcp.server import MCPError

        handler = ReconToolHandler(project_with_snodo)
        with pytest.raises(MCPError, match="query"):
            handler.handle_recon({})

    def test_handle_recon_requires_paths(self, project_with_snodo):
        from snodo.mcp.recon_handlers import ReconToolHandler
        from snodo.mcp.server import MCPError

        handler = ReconToolHandler(project_with_snodo)
        with pytest.raises(MCPError, match="paths"):
            handler.handle_recon({"query": "q", "paths": []})

    def test_handle_recon_returns_id(self, project_with_snodo, recon_mgr):
        from snodo.mcp.recon_handlers import ReconToolHandler

        handler = ReconToolHandler(project_with_snodo)
        result = handler.handle_recon({
            "query": "What is this project?",
            "paths": ["./"],
            "agents": ["default"],
        })

        assert result["recon_id"].startswith("rec_")
        assert result["status"] == "running"
        assert "default" in result["agents"]

    def test_handle_get_recon_status_requires_id(self, project_with_snodo):
        from snodo.mcp.recon_handlers import ReconToolHandler
        from snodo.mcp.server import MCPError

        handler = ReconToolHandler(project_with_snodo)
        with pytest.raises(MCPError, match="recon_id"):
            handler.handle_get_recon_status({})

    def test_handle_get_recon_results_requires_id(self, project_with_snodo):
        from snodo.mcp.recon_handlers import ReconToolHandler
        from snodo.mcp.server import MCPError

        handler = ReconToolHandler(project_with_snodo)
        with pytest.raises(MCPError, match="recon_id"):
            handler.handle_get_recon_results({})

    def test_handle_get_recon_status_not_found(self, project_with_snodo):
        from snodo.mcp.recon_handlers import ReconToolHandler
        from snodo.mcp.server import MCPError

        handler = ReconToolHandler(project_with_snodo)
        with pytest.raises(MCPError):
            handler.handle_get_recon_status({"recon_id": "rec_nonexistent"})


# ------------------------------------------------------------------#
# Resolve agent model tests
# ------------------------------------------------------------------#

def _set_default_model(configured: str) -> None:
    """Write ``model:`` into the isolated SNODO_HOME config."""
    from snodo.config import ConfigManager

    cfg = ConfigManager()
    data = cfg.load()
    data["model"] = configured
    cfg.save(data)


class TestResolveAgentModel:
    def test_default_resolves_to_configured_model(self):
        from snodo.recon import resolve_agent_model

        _set_default_model("gpt-4")
        assert resolve_agent_model("default") == "gpt-4"

    def test_named_agent_passes_through(self):
        from snodo.recon import resolve_agent_model
        result = resolve_agent_model("gemini/gemini-2.0-flash-exp")
        assert result == "gemini/gemini-2.0-flash-exp"

    def test_default_with_coder_adapter_prefix_is_stripped_to_a_callable_model(
        self, capsys,
    ):
        """A default model namespaced by a subprocess coder (opencode-cli/...)
        is stripped to the provider model the CLI would have used, so the
        adapter-prefixed string never reaches the provider call."""
        from snodo.recon import resolve_agent_model

        _set_default_model("opencode-cli/deepseek/deepseek-chat")
        result = resolve_agent_model("default")

        assert result == "deepseek/deepseek-chat"
        assert "opencode-cli/" not in result
        assert "opencode-cli/deepseek/deepseek-chat" in capsys.readouterr().err

    def test_default_with_legacy_opencode_prefix_is_stripped(self):
        """``opencode`` accepts the legacy ``opencode/`` namespace as well as
        its declared ``opencode-cli/`` prefix, and recon strips it too."""
        from snodo.recon import resolve_agent_model

        _set_default_model("opencode/deepseek/deepseek-chat")
        assert resolve_agent_model("default") == "deepseek/deepseek-chat"

    def test_default_that_is_no_model_at_all_falls_back_to_the_builtin(
        self, capsys,
    ):
        """A configured default that names neither a provider model nor an
        adapter namespace resolves to something this path can call."""
        from snodo.config import DEFAULT_MODEL
        from snodo.recon import resolve_agent_model

        _set_default_model("not-a-provider-model")
        result = resolve_agent_model("default")

        assert result == DEFAULT_MODEL
        assert "not-a-provider-model" in capsys.readouterr().err

    def test_a_default_model_that_reaches_the_provider_call_is_stripped(
        self, project_with_snodo,
    ):
        """End to end: a configured default carrying a coder-adapter prefix
        does not reach litellm.completion."""
        from unittest.mock import MagicMock

        from snodo.recon import call_agent, resolve_agent_model

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "answer"
        mock_response.choices[0].message.tool_calls = None

        _set_default_model("opencode-cli/deepseek/deepseek-chat")
        resolved = resolve_agent_model("default")
        with patch("litellm.completion", return_value=mock_response) as mock_comp:
            res = call_agent(
                project_root=project_with_snodo,
                model=resolved,
                query="q",
                paths=["./"],
                agent_label="agent1",
            )

        assert not res.error
        model_arg = mock_comp.call_args.kwargs["model"]
        assert "opencode-cli/" not in model_arg
        assert model_arg == "deepseek/deepseek-chat"


# ------------------------------------------------------------------#
# Read-only tool surface tests
# ------------------------------------------------------------------#

class TestReadOnlyTools:
    def test_read_file_tool_surface(self, tmp_path):
        from snodo.recon import _LIST_FILES_TOOL, _READ_FILE_TOOL

        assert _READ_FILE_TOOL["function"]["name"] == "read_file"
        assert _LIST_FILES_TOOL["function"]["name"] == "list_files"

        # Verify no write tools in the surface
        tool_names = {
            _READ_FILE_TOOL["function"]["name"],
            _LIST_FILES_TOOL["function"]["name"],
        }
        assert "write_file" not in tool_names
        assert "delete_file" not in tool_names


# ------------------------------------------------------------------#
# Terminal answer tests (Fixes #299)
# ------------------------------------------------------------------#

def _reading_response(narration: str, path: str):
    """A turn where the agent narrates and keeps reading."""
    from types import SimpleNamespace

    tc = SimpleNamespace(
        id=f"call_{path}",
        function=SimpleNamespace(
            name="read_file", arguments=json.dumps({"path": path})
        ),
    )
    msg = SimpleNamespace(content=narration, tool_calls=[tc])
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def _prose_response(text):
    """A turn where the agent answers in prose (no tool calls)."""
    from types import SimpleNamespace

    msg = SimpleNamespace(content=text, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


class TestTerminalAnswer:
    def test_tool_calling_budget_never_returns_raw_tool_output(self, project_with_snodo):
        from types import SimpleNamespace
        from snodo.recon import call_agent

        def tool_response(name, arguments):
            tc = SimpleNamespace(id="read", function=SimpleNamespace(
                name=name, arguments=json.dumps(arguments),
            ))
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content="", tool_calls=[tc]), finish_reason="tool_calls",
            )])

        tool_output = "RAW_TOOL_FIXTURE: repository dump must not become an answer"
        responses = [
            tool_response("read_file", {"path": "facts.txt"}),
            tool_response("read_file", {"path": "facts.txt"}),
            tool_response("read_file", {"path": "facts.txt"}),
        ]
        with patch("snodo.recon._execute_recon_read", return_value=tool_output) as read, \
             patch("litellm.completion", side_effect=responses) as completion:
            result = call_agent(project_with_snodo, "test/model", "query", ["./"], "agent", max_turns=1)

        assert completion.call_count == 3
        assert completion.call_args_list[1].kwargs["tools"][0]["function"]["name"] == "submit_answer"
        assert completion.call_args_list[1].kwargs["tool_choice"]["function"]["name"] == "submit_answer"
        assert result.result == ""
        assert "returned tool calls instead of an answer after" in result.error
        assert result.trace["turns_used"] == 2
        assert result.trace["tools_called"] == ["read_file"]
        assert result.trace["ended"] == "truncated:tool_calls"
        assert tool_output not in result.result
        assert read.call_count == 1

    def test_tool_call_retry_falls_back_to_earlier_model_prose(self, project_with_snodo):
        from snodo.recon import call_agent

        tool_output = "RAW_TOOL_FIXTURE: never returned"
        responses = [
            _reading_response("Earlier useful model-authored analysis.", "facts.txt"),
            _reading_response("", "other.txt"),
            _reading_response("", "third.txt"),
        ]
        with patch("snodo.recon._execute_recon_read", return_value=tool_output), \
             patch("litellm.completion", side_effect=responses):
            result = call_agent(project_with_snodo, "test/model", "query", ["./"], "agent", max_turns=1)

        assert result.result.startswith("Earlier useful model-authored analysis.")
        assert "truncated" in result.error
        assert tool_output not in result.result

    def test_out_of_turns_agent_asked_once_without_tools(self, project_with_snodo):
        """An agent still calling tools on its last budgeted turn is asked
        once more with the read tools withdrawn, and that answer — not the
        narration from earlier turns — is what gets returned (Fixes #299)."""
        from snodo.recon import _ANSWER_ONLY_INSTRUCTION, call_agent

        answer = "The worker dispatches tasks to validators in bounded turns."
        responses = [
            _reading_response("Let me explore the codebase structure first.", "a.py"),
            _reading_response("Now let me read the Worker code.", "b.py"),
            _prose_response(answer),
        ]

        with patch("litellm.completion", side_effect=responses) as mock_comp:
            res = call_agent(
                project_root=project_with_snodo,
                model="test/model",
                query="What does the worker do?",
                paths=["./"],
                agent_label="agent1",
                max_turns=2,
            )

        assert res.error is None
        # The answer is what gets returned, and no narration from earlier
        # turns appears in the result.
        assert res.result == answer
        assert "explore" not in res.result
        assert "Now let me read" not in res.result

        # Reading turns were offered the read-only tools.
        assert mock_comp.call_count == 3
        first_kwargs = mock_comp.call_args_list[0].kwargs
        assert "tools" in first_kwargs

        # The third request is the terminal ask: only submit_answer is offered,
        # instruction appended as the last user turn.
        final_kwargs = mock_comp.call_args.kwargs
        assert final_kwargs["tools"][0]["function"]["name"] == "submit_answer"
        assert final_kwargs["tool_choice"]["function"]["name"] == "submit_answer"
        assert final_kwargs["messages"][-1] == {
            "role": "user",
            "content": _ANSWER_ONLY_INSTRUCTION,
        }

    def test_terminal_ask_retries_empty_answer(
        self, project_with_snodo,
    ):
        """Empty forced submit is retried once and accepts the retry answer."""
        from types import SimpleNamespace
        from snodo.recon import call_agent

        empty = SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content="", tool_calls=None), finish_reason="stop",
        )])
        answer = SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content="A useful partial answer.", tool_calls=None), finish_reason="stop",
        )])
        responses = [
            _reading_response("The worker dispatches bounded jobs to validators.", "a.py"),
            empty,
            answer,
        ]

        with patch("litellm.completion", side_effect=responses) as completion:
            res = call_agent(
                project_root=project_with_snodo,
                model="test/model",
                query="What does the worker do?",
                paths=["./"],
                agent_label="agent1",
                max_turns=1,
            )

        assert res.error is None
        assert res.result == "A useful partial answer."
        assert completion.call_count == 3
        assert "partial answer is still useful" in completion.call_args.kwargs["messages"][-1]["content"]

    def test_retry_empty_fails_honestly_without_narration(self, project_with_snodo):
        from snodo.recon import call_agent

        responses = [
            _reading_response("I'll systematically trace the code.", "a.py"),
            _prose_response(""), _prose_response(""),
        ]
        with patch("litellm.completion", side_effect=responses):
            result = call_agent(project_with_snodo, "test/model", "query", ["./"], "agent", max_turns=1)
        assert result.result == ""
        assert "empty_final" in result.error
        assert "systematically trace" not in result.result

    def test_final_submit_answer_tool_call_is_returned(self, project_with_snodo):
        from types import SimpleNamespace
        from snodo.recon import call_agent

        tc = SimpleNamespace(id="answer", function=SimpleNamespace(
            name="submit_answer", arguments=json.dumps({"answer": "Structured answer."}),
        ))
        response = SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content="", tool_calls=[tc]), finish_reason="tool_calls",
        )])
        with patch("litellm.completion", side_effect=[_reading_response("Found facts.", "a.py"), response]) as completion:
            result = call_agent(project_with_snodo, "test/model", "query", ["./"], "agent", max_turns=1)
        assert result.result == "Structured answer."
        assert completion.call_args.kwargs["tool_choice"]["function"]["name"] == "submit_answer"
        assert result.trace["ended"] == "submitted_answer"

    def test_truncated_final_response_is_reported(self, project_with_snodo):
        from types import SimpleNamespace
        from snodo.recon import call_agent

        tc = SimpleNamespace(id="answer", function=SimpleNamespace(
            name="submit_answer", arguments=json.dumps({"answer": "Some incomplete answer"}),
        ))
        response = SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content="", tool_calls=[tc]), finish_reason="length",
        )])
        with patch("litellm.completion", side_effect=[_reading_response("Read context.", "a.py"), response]):
            result = call_agent(project_with_snodo, "test/model", "query", ["./"], "agent", max_turns=1)
        assert "truncated" in result.result
        assert "finish_reason=length" in result.error
        assert result.trace["ended"] == "truncated:length"

    def test_final_content_without_tool_call_is_accepted(self, project_with_snodo):
        from snodo.recon import call_agent
        with patch("litellm.completion", side_effect=[
            _reading_response("Read context.", "a.py"), _prose_response("Content-only answer."),
        ]):
            result = call_agent(project_with_snodo, "test/model", "query", ["./"], "agent", max_turns=1)
        assert result.result == "Content-only answer."

    def test_natural_conclusion_returns_answer_not_accumulated_narration(
        self, project_with_snodo,
    ):
        """An agent that stops reading on its own is done: its final prose is
        the result; narration from earlier turns is not folded into it."""
        from snodo.recon import call_agent

        answer = "The module defines the recon dispatch contract."
        responses = [
            _reading_response("I'll explore the codebase structure first.", "a.py"),
            _prose_response(answer),
        ]

        with patch("litellm.completion", side_effect=responses) as mock_comp:
            res = call_agent(
                project_root=project_with_snodo,
                model="test/model",
                query="What is in this module?",
                paths=["./"],
                agent_label="agent1",
                max_turns=10,
            )

        assert res.error is None
        assert res.result == answer
        assert "explore" not in res.result
        # Concluded early — no terminal ask was needed.
        assert mock_comp.call_count == 2


# ------------------------------------------------------------------#
# CLI completion and API base resolution tests
# ------------------------------------------------------------------#

class TestReconDefectFixes:
    def test_recon_completes_through_cli_entry_point(self, project_with_snodo, capsys, monkeypatch):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from snodo.cli.commands.recon_cmd import recon_command

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Codebase looks good"
        mock_response.choices[0].message.tool_calls = None

        monkeypatch.setattr(recon_module, "_threads", [])
        with patch("snodo.infrastructure.paths.require_project_root", return_value=project_with_snodo), \
             patch("litellm.completion", return_value=mock_response):
            args = SimpleNamespace(query="Explore the app architecture", paths=["./"], num_agents=1)
            rc = recon_command(args)

        assert rc == 0
        out = capsys.readouterr().out
        assert "Recon complete:" in out
        assert "Codebase looks good" in out

    def test_api_base_reaches_completion_call_when_configured(self, project_with_snodo):
        from unittest.mock import MagicMock

        from snodo.recon import call_agent

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Analysis complete"
        mock_response.choices[0].message.tool_calls = None

        with patch("snodo.config.ConfigManager.resolve_api_base", return_value="https://custom.endpoint.ai/v1"), \
             patch("snodo.config.ConfigManager._provider_for_model", return_value="custom"), \
             patch("litellm.completion", return_value=mock_response) as mock_comp:
            res = call_agent(
                project_root=project_with_snodo,
                model="custom/my-model",
                query="test query",
                paths=["./"],
                agent_label="agent1",
            )
            assert not res.error
            assert res.result == "Analysis complete"
            mock_comp.assert_called()
            call_kwargs = mock_comp.call_args.kwargs
            assert call_kwargs.get("api_base") == "https://custom.endpoint.ai/v1"


# ------------------------------------------------------------------#
# Model-list failover: the configured order means what it says
# ------------------------------------------------------------------#

class TestResolveReconAgentsPriority:
    def test_single_agent_lane_carries_the_whole_list_in_order(self):
        from snodo.recon import resolve_recon_agents

        lanes = resolve_recon_agents(
            recon_models=["m1", "m2", "m3"], recon_default_n=1,
        )
        assert lanes == [["m1", "m2", "m3"]]

    def test_more_models_than_agents_warns_which_tail_is_unused(self, capsys):
        from snodo.recon import resolve_recon_agents

        lanes = resolve_recon_agents(
            recon_models=["m1", "m2", "m3"], recon_default_n=2,
        )
        assert lanes == [["m1"], ["m2"]]
        err = capsys.readouterr().err
        assert "unused" in err and "m3" in err

    def test_fewer_models_than_agents_warns_once_not_per_slot(self, capsys):
        from snodo.recon import resolve_recon_agents_with_notice

        lanes, notice = resolve_recon_agents_with_notice(
            requested_n=5, recon_models=["m1", "m2"], recon_default_n=1,
        )
        assert lanes == [["m1"], ["m2"]]
        err = capsys.readouterr().err
        assert err.count("Warning:") == 1
        assert "only 2 recon model(s)" in notice
        assert "Requested 5 agents, but 2 ran" in notice

    def test_no_models_and_n_gt_1_warns_and_uses_default_once(self, capsys):
        from snodo.recon import resolve_recon_agents_with_notice

        lanes, notice = resolve_recon_agents_with_notice(
            requested_n=3, recon_models=[], recon_default_n=1,
        )
        assert lanes == [["default"]]
        assert "no recon models are configured" in capsys.readouterr().err
        assert "Requested 3 agents, but 1 ran" in notice
        assert "llm.recon.models" in notice

    def test_cli_reports_reduced_agent_count(self, monkeypatch, capsys):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from snodo.cli.commands import recon_cmd

        manager = MagicMock()
        manager.get_status.return_value = {"results": [{
            "agent": "default", "model": "default", "result": "answer",
            "error": None, "attempts": [],
        }]}
        with patch("snodo.infrastructure.paths.require_project_root", return_value="/tmp/project"), \
             patch("snodo.config.ConfigManager") as config_manager, \
             patch("snodo.recon.ReconManager", return_value=manager), \
             patch("snodo.recon.resolve_recon_agents_with_notice", return_value=(
                 [["default"]],
                 "Requested 3 agents, but 1 ran because no recon models are configured. Set llm.recon.models to enable fan-out.",
             )):
            config_manager.return_value.load.return_value = {"llm": {"recon": {"models": []}}}
            assert recon_cmd.recon_command(SimpleNamespace(
                query="q", paths=["./"], num_agents=3,
            )) == 0
        out = capsys.readouterr().out
        assert "Agent count: 1" in out
        assert "llm.recon.models" in out

    def test_explicit_agents_is_fan_out_of_single_model_lanes(self):
        from snodo.recon import resolve_recon_agents

        lanes = resolve_recon_agents(
            requested_n=2, recon_models=["m1", "m2", "m3"],
            explicit_agents=["x", "y"],
        )
        assert lanes == [["x"], ["y"]]

    def test_normalize_accepts_flat_and_lane_forms(self):
        from snodo.recon import normalize_recon_agents

        assert normalize_recon_agents(["m1", "m2"]) == [["m1"], ["m2"]]
        assert normalize_recon_agents([["m1", "m2"], ["m3"]]) == [["m1", "m2"], ["m3"]]
        assert normalize_recon_agents([]) == [["default"]]


class TestCallAgentChain:
    def _ok(self, model, result="answer"):
        from snodo.recon import ReconResult
        return ReconResult(agent="a", model=model, result=result)

    def _fault(self, model):
        from snodo.recon import ReconResult
        return ReconResult(agent="a", model=model, result="", error="boom")

    def test_first_model_returning_nothing_hands_off_to_the_second(self):
        from snodo.recon import call_agent_chain

        calls = []

        def fake_call(project_root, model, query, paths, agent_label, max_turns=6, max_tokens=1500):
            calls.append(model)
            if model == "m1":
                return self._fault("m1")
            return self._ok("m2", result="second answer")

        with patch("snodo.recon.call_agent", fake_call):
            result = call_agent_chain("/tmp", ["m1", "m2"], "q", ["./"], "a")

        assert calls == ["m1", "m2"]
        assert result.result == "second answer"
        assert result.error is None
        assert result.model == "m2"
        assert [(a.model, a.error) for a in result.attempts] == [
            ("m1", "boom"), ("m2", None),
        ]

    def test_first_model_answering_means_the_second_is_never_called(self):
        from snodo.recon import call_agent_chain

        calls = []

        def fake_call(project_root, model, query, paths, agent_label, max_turns=6, max_tokens=1500):
            calls.append(model)
            return self._ok(model, result="first answer")

        with patch("snodo.recon.call_agent", fake_call):
            result = call_agent_chain("/tmp", ["m1", "m2"], "q", ["./"], "a")

        assert calls == ["m1"]
        assert result.result == "first answer"
        assert result.model == "m1"

    def test_a_poor_answer_is_an_answer_and_is_not_retried(self):
        from snodo.recon import call_agent_chain

        calls = []

        def fake_call(project_root, model, query, paths, agent_label, max_turns=6, max_tokens=1500):
            calls.append(model)
            return self._ok(model, result="i have no idea")

        with patch("snodo.recon.call_agent", fake_call):
            result = call_agent_chain("/tmp", ["m1", "m2"], "q", ["./"], "a")

        assert calls == ["m1"]
        assert result.result == "i have no idea"

    def test_every_model_failing_reports_which_were_tried_and_why(self):
        from snodo.recon import call_agent_chain

        def fake_call(project_root, model, query, paths, agent_label, max_turns=6, max_tokens=1500):
            return self._fault(model)

        with patch("snodo.recon.call_agent", fake_call):
            result = call_agent_chain("/tmp", ["m1", "m2"], "q", ["./"], "a")

        assert result.error is not None
        assert "m1 (boom)" in result.error
        assert "m2 (boom)" in result.error

    def test_failover_records_usage_for_failed_and_successful_models(self, project_with_snodo):
        from types import SimpleNamespace

        from snodo.recon import call_agent_chain

        answer = SimpleNamespace(
            model="served-m2", response_cost=0.004,
            usage=SimpleNamespace(prompt_tokens=8, completion_tokens=3,
                                  cache_read_input_tokens=2,
                                  cache_creation_input_tokens=1),
            choices=[SimpleNamespace(message=SimpleNamespace(content="answer", tool_calls=None))],
        )
        with patch("litellm.completion", side_effect=[RuntimeError("offline"), answer]):
            result = call_agent_chain(project_with_snodo, ["m1", "m2"], "q", [], "agent")

        assert [(item["model"], item["outcome"]) for item in result.usage] == [
            ("m1", "failed"), ("m2", "succeeded"),
        ]
        success = result.usage[1]
        assert success["served_model"] == "served-m2"
        assert success["input_tokens"] == 8
        assert success["output_tokens"] == 3
        assert success["cache_read_tokens"] == 2
        assert success["cache_write_tokens"] == 1
        assert success["cost_usd"] == 0.004
        assert all(item["duration_ms"] is not None for item in result.usage)
        assert result.usage[0]["input_tokens"] is None

    def test_transient_failure_retries_once_and_records_both_attempts(self, monkeypatch):
        from snodo.recon import ReconResult, call_agent_chain

        outcomes = [
            ReconResult(agent="a", model="m1", result="", error='429 RESOURCE_EXHAUSTED {"retryDelay":"52s"}'),
            ReconResult(agent="a", model="m1", result="answer"),
        ]
        sleeps = []
        monkeypatch.setattr("snodo.recon.call_agent", lambda *args, **kwargs: outcomes.pop(0))
        monkeypatch.setattr("snodo.recon.time.sleep", sleeps.append)
        result = call_agent_chain("/tmp", ["m1"], "q", [], "a")
        assert result.result == "answer"
        assert sleeps == [30.0]
        assert [(item.model, item.error) for item in result.attempts] == [
            ("m1", '429 RESOURCE_EXHAUSTED {"retryDelay":"52s"}'), ("m1", None),
        ]

    def test_body_read_failure_retries_once(self, monkeypatch):
        from snodo.recon import ReconResult, call_agent_chain

        outcomes = [ReconResult(agent="a", model="m", result="", error="failed to read request body"),
                    ReconResult(agent="a", model="m", result="ok")]
        calls = []
        monkeypatch.setattr("snodo.recon.call_agent", lambda *args, **kwargs: (calls.append(1), outcomes.pop(0))[1])
        monkeypatch.setattr("snodo.recon.time.sleep", lambda _: None)
        result = call_agent_chain("/tmp", ["m"], "q", [], "a")
        assert len(calls) == 2
        assert len(result.attempts) == 2

    def test_authentication_failure_is_not_retried(self, monkeypatch):
        from snodo.recon import ReconResult, call_agent_chain

        calls = []
        monkeypatch.setattr("snodo.recon.call_agent", lambda *args, **kwargs: (
            calls.append(1), ReconResult(agent="a", model="m", result="", error="401 invalid API key")
        )[1])
        result = call_agent_chain("/tmp", ["m"], "q", [], "a")
        assert len(calls) == 1
        assert len(result.attempts) == 1


def test_two_agent_recon_persists_usage_and_unknowns_as_null(project_with_snodo, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(recon_module, "_threads", [])
    manager = ReconManager(project_with_snodo)
    monkeypatch.setattr(recon_module, "resolve_agent_model", lambda model: model)

    def completion(**kwargs):
        model = kwargs["model"]
        usage = (SimpleNamespace(prompt_tokens=11, completion_tokens=4,
                                 prompt_tokens_details=SimpleNamespace(cached_tokens=3))
                 if model == "m1" else None)
        return SimpleNamespace(
            model=f"served-{model}", usage=usage,
            choices=[SimpleNamespace(message=SimpleNamespace(content="answer", tool_calls=None))],
        )

    with patch("litellm.completion", side_effect=completion):
        recon_id = manager.submit("q", ["./"], agents=[["m1"], ["m2"]])
        manager.shutdown()

    records = manager.get_results(recon_id)["results"]
    by_agent = {item["agent"]: item["usage"][0] for item in records}
    assert by_agent["m1"]["input_tokens"] == 11
    assert by_agent["m1"]["output_tokens"] == 4
    assert by_agent["m1"]["cache_read_tokens"] == 3
    assert by_agent["m1"]["duration_ms"] is not None
    assert by_agent["m2"]["input_tokens"] is None
    assert by_agent["m2"]["output_tokens"] is None
    assert by_agent["m2"]["cost_usd"] is None


class TestReconManagerFailover:
    def test_agent_failure_does_not_cancel_other_agents(
        self, project_with_snodo, monkeypatch
    ):
        from snodo.recon import ReconResult

        monkeypatch.setattr(recon_module, "_threads", [])
        mgr = ReconManager(project_with_snodo)

        def fake_chain(project_root, models, query, paths, agent_label, max_turns=6, max_tokens=1500):
            if models[0] == "broken":
                raise RuntimeError("provider failed")
            return ReconResult(agent=agent_label, model=models[0], result="answer",
                               trace={"ended": "submitted_answer"})

        monkeypatch.setattr(recon_module, "call_agent_chain", fake_chain)
        recon_id = mgr.submit("q", ["./"], agents=[["broken"], ["healthy"]])
        mgr.shutdown()

        results = mgr.get_results(recon_id)["results"]
        assert {item["agent"]: item["error"] for item in results} == {
            "broken": "provider failed", "healthy": None,
        }
        assert next(item for item in results if item["agent"] == "healthy")["result"] == "answer"

    def test_completed_agent_trace_is_durable_while_another_agent_runs(
        self, project_with_snodo, monkeypatch
    ):
        from threading import Event, Thread
        from snodo.recon import ReconResult

        monkeypatch.setattr(recon_module, "_threads", [])
        mgr = ReconManager(project_with_snodo)
        slow_started, release_slow, quick_saved = Event(), Event(), Event()

        def fake_chain(project_root, models, query, paths, agent_label, max_turns=6, max_tokens=1500):
            if models[0] == "slow":
                slow_started.set()
                release_slow.wait(timeout=5)
            return ReconResult(agent=agent_label, model=models[0], result=models[0],
                               trace={"ended": "completed", "turns_used": 2})

        monkeypatch.setattr(recon_module, "call_agent_chain", fake_chain)
        save_results = mgr._save_results

        def track_saved(directory, results):
            save_results(directory, results)
            if results:
                quick_saved.set()

        monkeypatch.setattr(mgr, "_save_results", track_saved)
        recon_id = mgr._generate_id()
        directory = mgr.recons_dir / recon_id
        directory.mkdir()
        mgr._save_state(directory, {"recon_id": recon_id, "query": "q", "paths": ["./"],
                                    "agents": [["quick"], ["slow"]], "status": "running",
                                    "created_at": 0, "completed_at": None})
        runner = Thread(target=mgr._run_recon_impl,
                        args=(recon_id, "q", ["./"], [["quick"], ["slow"]]))
        runner.start()
        assert slow_started.wait(timeout=2)
        try:
            assert quick_saved.wait(timeout=2)
            records = mgr._load_results(directory)
            assert len(records) == 1
            assert records[0]["trace"] == {"ended": "completed", "turns_used": 2}
        finally:
            release_slow.set()
            runner.join(timeout=5)

    def test_lane_failover_is_called_and_first_answer_wins(
        self, project_with_snodo, monkeypatch
    ):
        monkeypatch.setattr(recon_module, "_threads", [])
        mgr = ReconManager(project_with_snodo)
        calls = []

        def fake_chain(project_root, models, query, paths, agent_label, max_turns=6, max_tokens=1500):
            from snodo.recon import ReconResult
            calls.append(list(models))
            return ReconResult(agent=agent_label, model=models[-1], result="ok")

        monkeypatch.setattr(recon_module, "call_agent_chain", fake_chain)
        recon_id = mgr.submit("q", ["./"], agents=[["m1", "m2"]])
        mgr.shutdown()

        assert calls == [["m1", "m2"]]
        results = mgr.get_results(recon_id)["results"]
        assert results[0]["model"] == "m2"
        assert results[0]["result"] == "ok"

    def test_explicit_fan_out_calls_every_requested_agent(
        self, project_with_snodo, monkeypatch
    ):
        monkeypatch.setattr(recon_module, "_threads", [])
        mgr = ReconManager(project_with_snodo)
        calls = []

        def fake_chain(project_root, models, query, paths, agent_label, max_turns=6, max_tokens=1500):
            from snodo.recon import ReconResult
            calls.append(list(models))
            return ReconResult(agent=agent_label, model=models[0], result="ok")

        monkeypatch.setattr(recon_module, "call_agent_chain", fake_chain)
        recon_id = mgr.submit("q", ["./"], agents=[["m1"], ["m2"], ["m3"]])
        mgr.shutdown()

        assert sorted(calls) == [["m1"], ["m2"], ["m3"]]
        results = mgr.get_results(recon_id)["results"]
        assert len(results) == 3
