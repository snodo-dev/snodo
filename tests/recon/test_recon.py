"""Tests for ReconManager and ReconToolHandler.

FILE: tests/recon/test_recon.py
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import snodo.recon as recon_module
from snodo.recon import ReconError, ReconManager, ReconResult, ReconState


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

    def test_running_recon_still_has_nothing_to_report(self, recon_mgr):
        recon_id = recon_mgr.submit("query", ["./"])

        assert recon_mgr.get_status(recon_id)["results"] == []
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

        assert recon_mgr.get_status(recon_id) == {**state, "results": results}
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

        # The third request is the terminal ask: no tools offered, and the
        # instruction appended as the last user turn.
        final_kwargs = mock_comp.call_args.kwargs
        assert "tools" not in final_kwargs
        assert final_kwargs["messages"][-1] == {
            "role": "user",
            "content": _ANSWER_ONLY_INSTRUCTION,
        }

    def test_terminal_ask_with_empty_answer_keeps_empty_result_error(
        self, project_with_snodo,
    ):
        """An agent that still produces nothing after being asked directly
        keeps the existing empty-result error path (Fixes #299)."""
        from snodo.recon import call_agent

        responses = [
            _reading_response("Let me check the specs.", "a.py"),
            _prose_response(""),
        ]

        with patch("litellm.completion", side_effect=responses):
            res = call_agent(
                project_root=project_with_snodo,
                model="test/model",
                query="What does the worker do?",
                paths=["./"],
                agent_label="agent1",
                max_turns=1,
            )

        assert res.error == "Agent returned empty result"
        assert res.result == ""

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
        from snodo.recon import resolve_recon_agents

        lanes = resolve_recon_agents(
            requested_n=5, recon_models=["m1", "m2"], recon_default_n=1,
        )
        assert lanes == [["m1"], ["m2"]]
        err = capsys.readouterr().err
        assert err.count("Warning:") == 1
        assert "only 2 recon model(s)" in err

    def test_no_models_and_n_gt_1_warns_and_uses_default_once(self, capsys):
        from snodo.recon import resolve_recon_agents

        lanes = resolve_recon_agents(requested_n=3, recon_models=[], recon_default_n=1)
        assert lanes == [["default"]]
        assert "no recon models configured" in capsys.readouterr().err

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

        def fake_call(project_root, model, query, paths, agent_label, max_turns=10):
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

        def fake_call(project_root, model, query, paths, agent_label, max_turns=10):
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

        def fake_call(project_root, model, query, paths, agent_label, max_turns=10):
            calls.append(model)
            return self._ok(model, result="i have no idea")

        with patch("snodo.recon.call_agent", fake_call):
            result = call_agent_chain("/tmp", ["m1", "m2"], "q", ["./"], "a")

        assert calls == ["m1"]
        assert result.result == "i have no idea"

    def test_every_model_failing_reports_which_were_tried_and_why(self):
        from snodo.recon import call_agent_chain

        def fake_call(project_root, model, query, paths, agent_label, max_turns=10):
            return self._fault(model)

        with patch("snodo.recon.call_agent", fake_call):
            result = call_agent_chain("/tmp", ["m1", "m2"], "q", ["./"], "a")

        assert result.error is not None
        assert "m1 (boom)" in result.error
        assert "m2 (boom)" in result.error


class TestReconManagerFailover:
    def test_lane_failover_is_called_and_first_answer_wins(
        self, project_with_snodo, monkeypatch
    ):
        monkeypatch.setattr(recon_module, "_threads", [])
        mgr = ReconManager(project_with_snodo)
        calls = []

        def fake_chain(project_root, models, query, paths, agent_label, max_turns=10):
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

        def fake_chain(project_root, models, query, paths, agent_label, max_turns=10):
            from snodo.recon import ReconResult
            calls.append(list(models))
            return ReconResult(agent=agent_label, model=models[0], result="ok")

        monkeypatch.setattr(recon_module, "call_agent_chain", fake_chain)
        recon_id = mgr.submit("q", ["./"], agents=[["m1"], ["m2"], ["m3"]])
        mgr.shutdown()

        assert sorted(calls) == [["m1"], ["m2"], ["m3"]]
        results = mgr.get_results(recon_id)["results"]
        assert len(results) == 3
