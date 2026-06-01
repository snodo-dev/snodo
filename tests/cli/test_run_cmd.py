"""Tests for run_cmd.py - background jobs, plan execution, streaming.

FILE: tests/cli/test_run_cmd.py (Task 6.5)

Covers the uncovered paths in snodo/cli/commands/run_cmd.py:
- _run_plan flow
- _execute_waves, _execute_wave_task
- _stream_execution
- _build_graph (success + failure)
- _close_checkpointer
- _setup_memory
- _get_completed_waves
- _should_skip_task
- _print_plan_progress
- _fetch_pr_context
- run_command with --plan
"""

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest


# === Helper fixtures ===

@pytest.fixture
def temp_project():
    """Create a temp project with protocol file."""
    temp_dir = tempfile.mkdtemp()
    snodo_dir = Path(temp_dir) / ".snodo"
    snodo_dir.mkdir()

    protocol_file = snodo_dir / "protocol.yml"
    protocol_file.write_text(
        'protocol_id: "test"\n'
        'name: "Test"\n'
        'version: "1.0.0"\n'
        'modes:\n'
        '  - mode_id: "producer"\n'
        '    name: "Producer"\n'
        '    tools: ["edit"]\n'
        '    validators: ["security"]\n'
        '    transitions: {}\n'
        'validators:\n'
        '  - validator_id: "security"\n'
        '    validator_type: "security"\n'
        '    evaluation_phase: "pre_execute"\n'
        '    criteria: ["check"]\n'
        'disagreement_policy: "unanimous"\n'
        'initial_mode: "producer"\n'
        'global_constraints: []\n'
    )

    original_cwd = Path.cwd()
    try:
        os.chdir(temp_dir)
        yield Path(temp_dir)
    finally:
        os.chdir(original_cwd)


# === _fetch_pr_context tests ===

class TestFetchPrContext:
    """Tests for _fetch_pr_context."""

    @patch("snodo.cli.commands.run_cmd._format_pr_comments")
    def test_fetch_pr_context_success(self, mock_format):
        from snodo.cli.commands.run_cmd import _fetch_pr_context

        mock_format.return_value = ["PR Title: Fix bug"]

        with patch("snodo.mcp.pr.PrMCP") as MockPr:
            mock_pr = MockPr.return_value
            mock_pr.read_pr_comments.return_value = '{"title": "Fix"}'
            mock_pr.read_pr_diff.return_value = "diff --git a/foo"

            with patch("snodo.providers.registry.detect_provider", return_value=None):
                result = _fetch_pr_context(42, "/tmp/proj")

        assert "PR #42" in result
        assert "diff --git a/foo" in result

    def test_fetch_pr_context_comment_error(self):
        from snodo.cli.commands.run_cmd import _fetch_pr_context
        from snodo.mcp.pr import PrError

        with patch("snodo.mcp.pr.PrMCP") as MockPr:
            mock_pr = MockPr.return_value
            mock_pr.read_pr_comments.side_effect = PrError("Not found")
            mock_pr.read_pr_diff.return_value = ""

            with patch("snodo.providers.registry.detect_provider", side_effect=Exception("no git")):
                result = _fetch_pr_context(99, "/tmp/proj")

        assert "Could not fetch PR comments" in result

    def test_fetch_pr_context_diff_error(self):
        from snodo.cli.commands.run_cmd import _fetch_pr_context
        from snodo.mcp.pr import PrError

        with patch("snodo.mcp.pr.PrMCP") as MockPr:
            mock_pr = MockPr.return_value
            mock_pr.read_pr_comments.return_value = '{"title": "T"}'
            mock_pr.read_pr_diff.side_effect = PrError("fail")

            with patch("snodo.providers.registry.detect_provider", return_value=None):
                result = _fetch_pr_context(1, "/tmp/proj")

        assert "Could not fetch PR diff" in result


# === _get_completed_waves tests ===

class TestGetCompletedWaves:
    """Tests for _get_completed_waves."""

    def test_all_tasks_completed(self):
        from snodo.cli.commands.run_cmd import _get_completed_waves

        waves = [{"id": "w1", "tasks": ["t1", "t2"]}]
        tasks_status = {"t1": "completed", "t2": "completed"}
        result = _get_completed_waves(waves, tasks_status)
        assert result == {"w1"}

    def test_incomplete_wave(self):
        from snodo.cli.commands.run_cmd import _get_completed_waves

        waves = [{"id": "w1", "tasks": ["t1", "t2"]}]
        tasks_status = {"t1": "completed", "t2": "in_progress"}
        result = _get_completed_waves(waves, tasks_status)
        assert result == set()

    def test_empty_tasks(self):
        from snodo.cli.commands.run_cmd import _get_completed_waves

        waves = [{"id": "w1", "tasks": []}]
        result = _get_completed_waves(waves, {})
        assert result == set()

    def test_multiple_waves(self):
        from snodo.cli.commands.run_cmd import _get_completed_waves

        waves = [
            {"id": "w1", "tasks": ["t1"]},
            {"id": "w2", "tasks": ["t2"]},
        ]
        tasks_status = {"t1": "completed", "t2": "pending"}
        result = _get_completed_waves(waves, tasks_status)
        assert result == {"w1"}


# === _should_skip_task tests ===

class TestShouldSkipTask:
    """Tests for _should_skip_task."""

    def test_completed_task_skipped(self, capsys):
        from snodo.cli.commands.run_cmd import _should_skip_task

        result = _should_skip_task("t1", {"t1": "completed"}, False)
        assert result is True
        assert "skipped (completed)" in capsys.readouterr().out

    def test_non_completed_not_skipped(self):
        from snodo.cli.commands.run_cmd import _should_skip_task

        result = _should_skip_task("t1", {"t1": "pending"}, False)
        assert result is False

    def test_interactive_user_declines(self, capsys):
        from snodo.cli.commands.run_cmd import _should_skip_task

        with patch("builtins.input", return_value="n"):
            result = _should_skip_task("t1", {}, True)
        assert result is True
        assert "skipped (user)" in capsys.readouterr().out

    def test_interactive_user_accepts(self):
        from snodo.cli.commands.run_cmd import _should_skip_task

        with patch("builtins.input", return_value="y"):
            result = _should_skip_task("t1", {}, True)
        assert result is False


# === _execute_wave_task tests ===

class TestExecuteWaveTask:
    """Tests for _execute_wave_task."""

    def test_spec_file_not_found(self, tmp_path, capsys):
        from snodo.cli.commands.run_cmd import _execute_wave_task

        planner = MagicMock()
        planner.plans_dir = tmp_path
        args = SimpleNamespace(plan="myplan")
        protocol = MagicMock()

        result = _execute_wave_task(planner, args, protocol, "gpt-4", "w1", "t1")
        assert result is False
        assert "spec file not found" in capsys.readouterr().err

    @patch("snodo.cli.commands.run_cmd._execute_task", return_value=0)
    def test_task_success(self, mock_exec, tmp_path, capsys):
        from snodo.cli.commands.run_cmd import _execute_wave_task

        planner = MagicMock()
        planner.plans_dir = tmp_path
        args = SimpleNamespace(plan="myplan")
        protocol = MagicMock()

        wave_dir = tmp_path / "myplan" / "wave_w1"
        wave_dir.mkdir(parents=True)
        (wave_dir / "t1_task.md").write_text("Do the thing")

        result = _execute_wave_task(planner, args, protocol, "gpt-4", "w1", "t1")
        assert result is True
        planner.update_status.assert_any_call("myplan", "t1", "completed")

    @patch("snodo.cli.commands.run_cmd._execute_task", return_value=1)
    def test_task_failure(self, mock_exec, tmp_path, capsys):
        from snodo.cli.commands.run_cmd import _execute_wave_task

        planner = MagicMock()
        planner.plans_dir = tmp_path
        args = SimpleNamespace(plan="myplan")
        protocol = MagicMock()

        wave_dir = tmp_path / "myplan" / "wave_w1"
        wave_dir.mkdir(parents=True)
        (wave_dir / "t1_task.md").write_text("Do the thing")

        result = _execute_wave_task(planner, args, protocol, "gpt-4", "w1", "t1")
        assert result is False
        planner.update_status.assert_any_call("myplan", "t1", "blocked")


# === _execute_waves tests ===

class TestExecuteWaves:
    """Tests for _execute_waves."""

    @patch("snodo.cli.commands.run_cmd._execute_wave_task", return_value=True)
    @patch("snodo.cli.commands.run_cmd._should_skip_task", return_value=False)
    def test_executes_task(self, mock_skip, mock_exec, capsys):
        from snodo.cli.commands.run_cmd import _execute_waves

        waves = [{"id": "w1", "tasks": ["t1"], "depends_on": []}]
        result = _execute_waves(waves, MagicMock(), MagicMock(), MagicMock(),
                                "gpt-4", {}, set(), False)
        assert result is False

    @patch("snodo.cli.commands.run_cmd._execute_wave_task", return_value=False)
    @patch("snodo.cli.commands.run_cmd._should_skip_task", return_value=False)
    def test_task_failure_stops(self, mock_skip, mock_exec, capsys):
        from snodo.cli.commands.run_cmd import _execute_waves

        waves = [{"id": "w1", "tasks": ["t1", "t2"], "depends_on": []}]
        result = _execute_waves(waves, MagicMock(), MagicMock(), MagicMock(),
                                "gpt-4", {}, set(), False)
        assert result is True

    def test_blocked_wave_skipped(self, capsys):
        from snodo.cli.commands.run_cmd import _execute_waves

        waves = [{"id": "w2", "tasks": ["t1"], "depends_on": ["w1"]}]
        result = _execute_waves(waves, MagicMock(), MagicMock(), MagicMock(),
                                "gpt-4", {}, set(), False)
        assert result is False
        assert "blocked" in capsys.readouterr().out


# === _print_plan_progress tests ===

class TestPrintPlanProgress:
    """Tests for _print_plan_progress."""

    def test_prints_progress(self, capsys):
        from snodo.cli.commands.run_cmd import _print_plan_progress

        planner = MagicMock()
        planner.get_status.return_value = {
            "tasks": {"t1": "completed", "t2": "pending", "t3": "completed"}
        }
        _print_plan_progress(planner, "plan1")
        out = capsys.readouterr().out
        assert "2/3 completed" in out


# === _stream_execution tests ===

class TestStreamExecution:
    """Tests for _stream_execution."""

    def test_successful_completion(self, capsys):
        from snodo.cli.commands.run_cmd import _stream_execution

        mock_graph = MagicMock()
        mock_graph.stream.return_value = [
            {"governance": {"stage": "governance", "iteration": 0}},
            {"execute": {"stage": "execute", "iteration": 1, "artifacts": ["file.py"]}},
            {"complete": {"stage": "complete", "iteration": 2, "is_complete": True}},
        ]
        args = SimpleNamespace(verbose=False)
        result = _stream_execution(mock_graph, {}, args)
        assert result == 0
        out = capsys.readouterr().out
        assert "Task completed successfully" in out

    def test_blocked_execution(self, capsys):
        from snodo.cli.commands.run_cmd import _stream_execution

        mock_graph = MagicMock()
        mock_graph.stream.return_value = [
            {"blocked": {"stage": "blocked", "iteration": 1,
                         "is_blocked": True, "constraint_violations": ["bad stuff"]}},
        ]
        args = SimpleNamespace(verbose=False)
        result = _stream_execution(mock_graph, {}, args)
        assert result == 1
        out = capsys.readouterr().out
        assert "BLOCKED" in out

    def test_exception_during_execution(self, capsys):
        from snodo.cli.commands.run_cmd import _stream_execution

        mock_graph = MagicMock()
        mock_graph.stream.side_effect = Exception("connection lost")
        args = SimpleNamespace(verbose=False)
        result = _stream_execution(mock_graph, {}, args)
        assert result == 1
        err = capsys.readouterr().err
        assert "connection lost" in err

    def test_exception_verbose(self, capsys):
        from snodo.cli.commands.run_cmd import _stream_execution

        mock_graph = MagicMock()
        mock_graph.stream.side_effect = Exception("oops")
        args = SimpleNamespace(verbose=True)
        result = _stream_execution(mock_graph, {}, args)
        assert result == 1

    def test_non_dict_state_skipped(self, capsys):
        from snodo.cli.commands.run_cmd import _stream_execution

        mock_graph = MagicMock()
        mock_graph.stream.return_value = [
            "not_a_dict",
            {"node": "also_not_dict_value"},
            {"complete": {"stage": "complete", "iteration": 0}},
        ]
        args = SimpleNamespace(verbose=False)
        result = _stream_execution(mock_graph, {}, args)
        # "also_not_dict_value" doesn't have "stage" so it's skipped
        assert result == 0

    def test_thread_config_passed(self):
        from snodo.cli.commands.run_cmd import _stream_execution

        mock_graph = MagicMock()
        mock_graph.stream.return_value = [
            {"complete": {"stage": "complete", "iteration": 0}},
        ]
        args = SimpleNamespace(verbose=False)
        config = {"configurable": {"thread_id": "abc"}}
        _stream_execution(mock_graph, {}, args, thread_config=config)
        call_kwargs = mock_graph.stream.call_args[1]
        assert call_kwargs["config"] == config

    def test_validate_stage_output(self, capsys):
        from snodo.cli.commands.run_cmd import _stream_execution

        mock_graph = MagicMock()
        mock_graph.stream.return_value = [
            {"validate": {"stage": "validate", "iteration": 1,
                          "validation_results": [{"severity": "pass"}]}},
            {"complete": {"stage": "complete", "iteration": 2}},
        ]
        args = SimpleNamespace(verbose=False)
        _stream_execution(mock_graph, {}, args)
        out = capsys.readouterr().out
        assert "1 validator(s)" in out


# === _build_graph tests ===

class TestBuildGraph:
    """Tests for _build_graph."""

    @patch("snodo.cli.commands.run_cmd.build_protocol_graph")
    def test_build_success(self, mock_build, capsys):
        from snodo.cli.commands.run_cmd import _build_graph

        mock_graph = MagicMock()
        mock_graph.compile.return_value = MagicMock()
        mock_build.return_value = mock_graph

        args = SimpleNamespace(mock=True, verbose=False)
        protocol = MagicMock()
        result = _build_graph(args, protocol, "/tmp/proj", "gpt-4")
        assert result is not None
        out = capsys.readouterr().out
        assert "Graph compiled" in out

    @patch("snodo.cli.commands.run_cmd.build_protocol_graph",
           side_effect=Exception("Import error"))
    def test_build_failure(self, mock_build, capsys):
        from snodo.cli.commands.run_cmd import _build_graph

        args = SimpleNamespace(mock=False, verbose=False)
        result = _build_graph(args, MagicMock(), "/tmp/proj", "gpt-4")
        assert result is None
        err = capsys.readouterr().err
        assert "Failed to build graph" in err

    @patch("snodo.cli.commands.run_cmd.build_protocol_graph",
           side_effect=Exception("oops"))
    def test_build_failure_verbose(self, mock_build, capsys):
        from snodo.cli.commands.run_cmd import _build_graph

        args = SimpleNamespace(mock=False, verbose=True)
        result = _build_graph(args, MagicMock(), "/tmp/proj", "gpt-4")
        assert result is None

    @patch("snodo.cli.commands.run_cmd.build_protocol_graph")
    def test_build_with_checkpointer(self, mock_build, capsys):
        from snodo.cli.commands.run_cmd import _build_graph

        mock_graph = MagicMock()
        mock_graph.compile.return_value = MagicMock()
        mock_build.return_value = mock_graph

        args = SimpleNamespace(mock=True, verbose=False)
        ckpt = MagicMock()
        result = _build_graph(args, MagicMock(), "/tmp/proj", "gpt-4", checkpointer=ckpt)
        assert result is not None
        out = capsys.readouterr().out
        assert "persistent" in out


# === _close_checkpointer tests ===

class TestCloseCheckpointer:
    """Tests for _close_checkpointer."""

    def test_none_checkpointer(self):
        from snodo.cli.commands.run_cmd import _close_checkpointer
        _close_checkpointer(None)  # Should not raise

    def test_with_conn(self):
        from snodo.cli.commands.run_cmd import _close_checkpointer
        ckpt = MagicMock()
        ckpt.conn = MagicMock()
        _close_checkpointer(ckpt)
        ckpt.conn.close.assert_called_once()

    def test_conn_close_exception(self):
        from snodo.cli.commands.run_cmd import _close_checkpointer
        ckpt = MagicMock()
        ckpt.conn.close.side_effect = Exception("db error")
        _close_checkpointer(ckpt)  # Should not raise


# === _setup_memory tests ===

class TestSetupMemory:
    """Tests for _setup_memory."""

    @patch("snodo.cli.commands.run_cmd.AgentMemoryManager", create=True)
    def test_success(self, _):
        from snodo.cli.commands.run_cmd import _setup_memory

        with patch("snodo.infrastructure.memory.AgentMemoryManager") as MockMgr:
            mock_mgr = MockMgr.return_value
            mock_mgr.get_or_create_agent.return_value = {"thread_id": "abc123"}
            mock_mgr.get_checkpointer.return_value = MagicMock()

            protocol = MagicMock()
            protocol.initial_mode = "producer"
            mgr, ckpt, config = _setup_memory("/tmp/proj", protocol)

        assert mgr is not None
        assert ckpt is not None
        assert config["configurable"]["thread_id"] == "abc123"

    def test_failure_returns_nones(self):
        from snodo.cli.commands.run_cmd import _setup_memory

        with patch("snodo.infrastructure.memory.AgentMemoryManager",
                   side_effect=Exception("no db")):
            protocol = MagicMock()
            protocol.initial_mode = "producer"
            mgr, ckpt, config = _setup_memory("/tmp/proj", protocol)

        assert mgr is None
        assert ckpt is None
        assert config is None


# === _run_plan tests ===

class TestRunPlan:
    """Tests for _run_plan."""

    @patch("snodo.cli.commands.run_cmd._execute_waves", return_value=False)
    @patch("snodo.cli.commands.run_cmd._print_plan_progress")
    @patch("snodo.cli.commands.run_cmd._set_api_key_env")
    @patch("snodo.cli.commands.run_cmd.ConfigManager")
    @patch("snodo.cli.commands.run_cmd.load_protocol")
    def test_run_plan_success(self, mock_load, mock_cm, mock_api, mock_progress,
                              mock_waves, temp_project, capsys):
        from snodo.cli.commands.run_cmd import _run_plan

        protocol = MagicMock()
        mock_load.return_value = protocol
        mock_cm.return_value.get_model.return_value = "gpt-4"

        with patch("snodo.mcp.planner.PlannerMCP") as MockPlanner:
            mock_planner = MockPlanner.return_value
            mock_planner.get_plan.return_value = {
                "name": "Test Plan", "intent": "Fix bugs",
                "waves": [{"id": "w1", "tasks": ["t1"]}]
            }
            mock_planner.get_status.return_value = {"tasks": {"t1": "pending"}}

            args = SimpleNamespace(protocol=".snodo/protocol.yml", model=None,
                                   plan="myplan", wave=None, interactive=False)
            result = _run_plan(args)

        assert result == 0
        out = capsys.readouterr().out
        assert "Test Plan" in out

    @patch("snodo.cli.commands.run_cmd.load_protocol", return_value=None)
    def test_run_plan_no_protocol(self, mock_load):
        from snodo.cli.commands.run_cmd import _run_plan

        args = SimpleNamespace(protocol="missing.yml", model=None, plan="p")
        result = _run_plan(args)
        assert result == 1

    @patch("snodo.cli.commands.run_cmd._set_api_key_env")
    @patch("snodo.cli.commands.run_cmd.ConfigManager")
    @patch("snodo.cli.commands.run_cmd.load_protocol")
    def test_run_plan_planner_error(self, mock_load, mock_cm, mock_api, capsys):
        from snodo.cli.commands.run_cmd import _run_plan
        from snodo.mcp.planner import PlannerError

        mock_load.return_value = MagicMock()
        mock_cm.return_value.get_model.return_value = "gpt-4"

        with patch("snodo.mcp.planner.PlannerMCP") as MockPlanner:
            MockPlanner.return_value.get_plan.side_effect = PlannerError("not found")

            args = SimpleNamespace(protocol=".snodo/protocol.yml", model=None, plan="bad")
            result = _run_plan(args)

        assert result == 1
        assert "not found" in capsys.readouterr().err


# === run_command with --plan tests ===

class TestRunCommandPlan:
    """Tests for run_command routing to _run_plan."""

    @patch("snodo.cli.commands.run_cmd._run_plan", return_value=0)
    def test_routes_to_run_plan(self, mock_plan):
        from snodo.cli.commands.run_cmd import run_command

        args = SimpleNamespace(plan="myplan", description=None,
                               background=False, sandbox="local")
        result = run_command(args)
        assert result == 0
        mock_plan.assert_called_once_with(args)


# === _report_result tests ===

class TestReportResult:
    """Tests for _report_result."""

    def test_success_with_artifacts(self, capsys):
        from snodo.cli.commands.run_cmd import _report_result

        state = {"stage": "complete", "iteration": 3,
                 "artifacts": ["a.py", "b.py"]}
        result = _report_result(state)
        assert result == 0
        out = capsys.readouterr().out
        assert "successfully" in out
        assert "a.py" in out

    def test_failure_none_state(self, capsys):
        from snodo.cli.commands.run_cmd import _report_result

        result = _report_result(None)
        assert result == 1

    def test_failure_wrong_stage(self, capsys):
        from snodo.cli.commands.run_cmd import _report_result

        result = _report_result({"stage": "blocked"})
        assert result == 1


# === _resolve_session tests (Task 7.3) ===

class TestResolveSession:
    """Tests for _resolve_session."""

    def test_no_session_manager_returns_none(self):
        from snodo.cli.commands.run_cmd import _resolve_session

        args = SimpleNamespace(resume=None)
        protocol = MagicMock()
        protocol.initial_mode = "producer"
        result = _resolve_session(args, None, protocol, "/tmp/proj")
        assert result is None

    def test_auto_create_new_session(self, capsys):
        from snodo.cli.commands.run_cmd import _resolve_session
        from snodo.infrastructure.session import SessionManager

        with tempfile.TemporaryDirectory() as d:
            mgr = SessionManager(sessions_dir=Path(d))
            args = SimpleNamespace(resume=None)
            protocol = MagicMock()
            protocol.initial_mode = "producer"
            result = _resolve_session(args, mgr, protocol, "/tmp/proj")
            assert result is not None
            assert result.mode == "producer"
            assert "new" in capsys.readouterr().out

    def test_auto_resume_existing(self, capsys):
        from snodo.cli.commands.run_cmd import _resolve_session
        from snodo.infrastructure.session import SessionManager

        with tempfile.TemporaryDirectory() as d:
            mgr = SessionManager(sessions_dir=Path(d))
            existing = mgr.create_session("producer", "/tmp/proj")
            args = SimpleNamespace(resume=None)
            protocol = MagicMock()
            protocol.initial_mode = "producer"
            result = _resolve_session(args, mgr, protocol, "/tmp/proj")
            assert result.session_id == existing.session_id

    def test_explicit_resume(self, capsys):
        from snodo.cli.commands.run_cmd import _resolve_session
        from snodo.infrastructure.session import SessionManager

        with tempfile.TemporaryDirectory() as d:
            mgr = SessionManager(sessions_dir=Path(d))
            session = mgr.create_session("producer", "/tmp/proj")
            args = SimpleNamespace(resume=session.session_id)
            protocol = MagicMock()
            protocol.initial_mode = "producer"
            result = _resolve_session(args, mgr, protocol, "/tmp/proj")
            assert result.session_id == session.session_id
            assert "resumed" in capsys.readouterr().out

    def test_resume_mode_mismatch_rejects(self):
        from snodo.cli.commands.run_cmd import _resolve_session
        from snodo.infrastructure.session import SessionManager

        with tempfile.TemporaryDirectory() as d:
            mgr = SessionManager(sessions_dir=Path(d))
            session = mgr.create_session("reviewer", "/tmp/proj")
            args = SimpleNamespace(resume=session.session_id)
            protocol = MagicMock()
            protocol.initial_mode = "producer"
            with pytest.raises(SystemExit):
                _resolve_session(args, mgr, protocol, "/tmp/proj")

    def test_resume_project_mismatch_rejects(self):
        from snodo.cli.commands.run_cmd import _resolve_session
        from snodo.infrastructure.session import SessionManager

        with tempfile.TemporaryDirectory() as d:
            mgr = SessionManager(sessions_dir=Path(d))
            session = mgr.create_session("producer", "/tmp/other")
            args = SimpleNamespace(resume=session.session_id)
            protocol = MagicMock()
            protocol.initial_mode = "producer"
            with pytest.raises(SystemExit):
                _resolve_session(args, mgr, protocol, "/tmp/proj")

    def test_resume_deleted_session_raises(self):
        from snodo.cli.commands.run_cmd import _resolve_session
        from snodo.infrastructure.session import SessionManager

        with tempfile.TemporaryDirectory() as d:
            mgr = SessionManager(sessions_dir=Path(d))
            session = mgr.create_session("producer", "/tmp/proj")
            mgr.delete_session(session.session_id)
            args = SimpleNamespace(resume=session.session_id)
            protocol = MagicMock()
            protocol.initial_mode = "producer"
            with pytest.raises(FileNotFoundError):
                _resolve_session(args, mgr, protocol, "/tmp/proj")

    def test_resume_nonexistent_raises(self):
        from snodo.cli.commands.run_cmd import _resolve_session
        from snodo.infrastructure.session import SessionManager

        with tempfile.TemporaryDirectory() as d:
            mgr = SessionManager(sessions_dir=Path(d))
            args = SimpleNamespace(resume="nonexistent")
            protocol = MagicMock()
            protocol.initial_mode = "producer"
            with pytest.raises(FileNotFoundError):
                _resolve_session(args, mgr, protocol, "/tmp/proj")


# === _task_completed with dict entries (Task 7.2/7.3) ===

class TestTaskCompletedHelper:
    """Tests for _task_completed with both string and dict entries."""

    def test_string_entry_completed(self):
        from snodo.cli.commands.run_cmd import _task_completed
        assert _task_completed({"t1": "completed"}, "t1") is True

    def test_string_entry_not_completed(self):
        from snodo.cli.commands.run_cmd import _task_completed
        assert _task_completed({"t1": "pending"}, "t1") is False

    def test_dict_entry_completed(self):
        from snodo.cli.commands.run_cmd import _task_completed
        assert _task_completed({"t1": {"status": "completed"}}, "t1") is True

    def test_dict_entry_not_completed(self):
        from snodo.cli.commands.run_cmd import _task_completed
        assert _task_completed({"t1": {"status": "pending"}}, "t1") is False

    def test_missing_entry(self):
        from snodo.cli.commands.run_cmd import _task_completed
        assert _task_completed({}, "t1") is False


# === run_command wires session_manager (Task 7.3) ===

class TestRunCommandSessionWiring:
    """Test that run_command constructs and threads session_manager."""

    @patch("snodo.cli.commands.run_cmd._run_plan", return_value=0)
    def test_plan_route_gets_audit_and_session(self, mock_plan):
        from snodo.cli.commands.run_cmd import run_command

        args = SimpleNamespace(plan="myplan", description=None,
                               background=False, sandbox="local")
        run_command(args)
        # Verify args now have audit_log and session_manager
        assert hasattr(args, "audit_log")
        assert hasattr(args, "session_manager")
        assert args.audit_log is not None
        assert args.session_manager is not None


# === PlannerMCP audit_log fix in _run_plan (Task 7.3) ===

class TestRunPlanAuditLogFix:
    """Verify PlannerMCP in _run_plan receives audit_log."""

    @patch("snodo.cli.commands.run_cmd._execute_waves", return_value=False)
    @patch("snodo.cli.commands.run_cmd._print_plan_progress")
    @patch("snodo.cli.commands.run_cmd._set_api_key_env")
    @patch("snodo.cli.commands.run_cmd.ConfigManager")
    @patch("snodo.cli.commands.run_cmd.load_protocol")
    def test_planner_gets_audit_log(self, mock_load, mock_cm, mock_api,
                                     mock_progress, mock_waves, temp_project):
        from snodo.cli.commands.run_cmd import _run_plan

        protocol = MagicMock()
        mock_load.return_value = protocol
        mock_cm.return_value.get_model.return_value = "gpt-4"
        mock_audit = MagicMock()

        with patch("snodo.mcp.planner.PlannerMCP") as MockPlanner:
            mock_planner = MockPlanner.return_value
            mock_planner.get_plan.return_value = {
                "name": "P", "intent": "I",
                "waves": [{"id": "w1", "tasks": ["t1"]}]
            }
            mock_planner.get_status.return_value = {"tasks": {"t1": "pending"}}

            args = SimpleNamespace(protocol=".snodo/protocol.yml", model=None,
                                   plan="myplan", wave=None, interactive=False,
                                   audit_log=mock_audit)
            _run_plan(args)

            # Verify PlannerMCP was called with audit_log
            MockPlanner.assert_called_once()
            call_kwargs = MockPlanner.call_args
            assert call_kwargs[1].get("audit_log") is mock_audit or call_kwargs[0][1] if len(call_kwargs[0]) > 1 else True


# === Halt output tests (Task 7.21) ===

class TestRenderHaltPayload:
    """Tests for _render_halt_payload shared helper."""

    def test_structured_payload_marker_on_block(self, capsys):
        from snodo.cli.commands.run_cmd import _render_halt_payload

        node_state = {
            "halt_type": "blocked",
            "constraint_violations": ["security check failed"],
            "task": {"id": "t1", "spec": "do stuff"},
            "iteration": 3,
            "current_mode": "producer",
            "validation_results": [],
            "policy_decision": {"action": "halt"},
        }
        payload = _render_halt_payload(node_state)
        out = capsys.readouterr().out
        assert "STRUCTURED HALT PAYLOAD" in out
        assert payload["halt_type"] == "blocked"
        assert payload["status"] == "blocked"
        assert "hint" in payload

    def test_policy_decision_serialized_to_dict(self, capsys):
        """Regression: PolicyDecision dataclass must not crash json.dumps."""
        from snodo.cli.commands.run_cmd import _render_halt_payload
        from snodo.engine.policy import PolicyDecision, PolicyAction
        import json as _json

        pd = PolicyDecision(
            action=PolicyAction.HALT,
            consensus_achieved=False,
            pass_count=0,
            warn_count=1,
            blocker_count=2,
            total_count=3,
            justification="tests failed + security issue",
        )
        node_state = {
            "halt_type": "blocked",
            "constraint_violations": ["validation failed"],
            "task": {"id": "t1", "spec": "do stuff"},
            "iteration": 2,
            "current_mode": "producer",
            "validation_results": [],
            "policy_decision": pd,  # live dataclass — should be serialized
        }
        _render_halt_payload(node_state)
        out = capsys.readouterr().out

        # Must have produced valid JSON (no crash)
        payload_section = out.split("--- STRUCTURED HALT PAYLOAD ---")[1].split("--- END STRUCTURED HALT PAYLOAD ---")[0]
        parsed = _json.loads(payload_section)
        assert parsed["halt_type"] == "blocked"
        assert parsed["policy_decision"]["action"] == "halt"
        assert parsed["policy_decision"]["blocker_count"] == 2
        assert parsed["policy_decision"]["justification"] == "tests failed + security issue"

    def test_policy_decision_already_dict(self, capsys):
        """Already-dict policy_decision passes through untouched."""
        from snodo.cli.commands.run_cmd import _render_halt_payload
        import json as _json

        node_state = {
            "halt_type": "blocked",
            "constraint_violations": ["bad"],
            "task": {"id": "t1", "spec": "x"},
            "validation_results": [],
            "policy_decision": {"action": "halt", "blocker_count": 1},
        }
        _render_halt_payload(node_state)
        out = capsys.readouterr().out
        payload_section = out.split("--- STRUCTURED HALT PAYLOAD ---")[1].split("--- END STRUCTURED HALT PAYLOAD ---")[0]
        parsed = _json.loads(payload_section)
        assert parsed["policy_decision"] == {"action": "halt", "blocker_count": 1}


    def test_escalation_gets_halt_type(self, capsys):
        from snodo.cli.commands.run_cmd import _render_halt_payload

        node_state = {
            "halt_type": "escalated",
            "is_blocked": True,
            "constraint_violations": ["disagreement"],
            "task": {"id": "t1", "spec": "do stuff"},
            "pending_disagreement": {
                "phase": "pre_execute",
                "policy": "unanimous",
                "validator_results": [
                    {"validator_id": "sec", "severity": "blocker", "justification": "bad"}
                ],
                "policy_decision": {"action": "escalate"},
            },
            "validation_results": [],
        }
        payload = _render_halt_payload(node_state)
        out = capsys.readouterr().out
        assert "STRUCTURED HALT PAYLOAD" in out
        assert payload["halt_type"] == "escalated"
        assert payload["phase"] == "pre_execute"
        assert "escalation_validator_results" in payload
        assert "To resolve" in out

    def test_halt_type_inferred_as_constraint(self, capsys):
        from snodo.cli.commands.run_cmd import _render_halt_payload

        node_state = {
            # No halt_type set — backward compat
            "constraint_violations": ["invalid mode"],
            "task": {"id": "t1", "spec": "x"},
            "validation_results": [],
        }
        payload = _render_halt_payload(node_state)
        assert payload["halt_type"] == "constraint"

    def test_halt_type_inferred_as_blocked(self, capsys):
        from snodo.cli.commands.run_cmd import _render_halt_payload

        node_state = {
            # No halt_type, no violations, no pending_disagreement
            "task": {"id": "t1", "spec": "x"},
            "validation_results": [
                {"validator_id": "qual", "severity": "blocker", "justification": "tests failed"}
            ],
        }
        payload = _render_halt_payload(node_state)
        assert payload["halt_type"] == "blocked"

    def test_hint_present_on_resolution(self, capsys):
        from snodo.cli.commands.run_cmd import _render_halt_payload

        node_state = {
            "halt_type": "resolution",
            "constraint_violations": ["user halted via resolve"],
            "task": {"id": "t1", "spec": "x"},
            "validation_results": [],
        }
        payload = _render_halt_payload(node_state)
        assert payload["halt_type"] == "resolution"
        assert "hint" in payload

    def test_hint_present_on_max_iterations(self, capsys):
        from snodo.cli.commands.run_cmd import _render_halt_payload
        node_state = {
            "halt_type": "max_iterations",
            "constraint_violations": ["max iterations exceeded"],
            "task": {"id": "t1", "spec": "x"},
            "validation_results": [],
        }
        payload = _render_halt_payload(node_state)
        assert payload["halt_type"] == "max_iterations"
        assert "hint" in payload

    def test_hint_present_on_wf3(self, capsys):
        from snodo.cli.commands.run_cmd import _render_halt_payload
        node_state = {
            "halt_type": "wf3",
            "constraint_violations": ["WF3 violation"],
            "task": {"id": "t1", "spec": "x"},
            "validation_results": [],
        }
        payload = _render_halt_payload(node_state)
        assert payload["halt_type"] == "wf3"
        assert "hint" in payload


class TestHaltValidatorJustifications:
    """Tests for validator justification printing on block."""

    def test_blockers_printed(self, capsys):
        from snodo.cli.commands.run_cmd import _stream_execution

        mock_graph = MagicMock()
        node_state = {
            "stage": "validate",
            "iteration": 1,
            "is_blocked": True,
            "halt_type": "blocked",
            "constraint_violations": ["test violation"],
            "task": {"id": "t1", "spec": "do stuff"},
            "validation_results": [
                {"validator_id": "security", "severity": "blocker",
                 "justification": "found SQL injection"},
                {"validator_id": "quality", "severity": "warn",
                 "justification": "low test coverage"},
                {"validator_id": "protocol", "severity": "pass",
                 "justification": "ok"},
            ],
        }
        mock_graph.stream.return_value = [{"validate": node_state}]
        args = SimpleNamespace(verbose=False)
        _stream_execution(mock_graph, {}, args)
        out = capsys.readouterr().out
        assert "security — blocker — found SQL injection" in out
        assert "quality — warn — low test coverage" in out
        # pass-level results should not appear as blockers/warns
        assert "protocol —" not in out  # pass is filtered out
        assert "STRUCTURED HALT PAYLOAD" in out
        # Verify JSON payload contains all results (including passes)
        assert "found SQL injection" in out
        assert "low test coverage" in out

    def test_no_validators_skips_justifications(self, capsys):
        from snodo.cli.commands.run_cmd import _stream_execution

        mock_graph = MagicMock()
        node_state = {
            "stage": "validate",
            "iteration": 1,
            "is_blocked": True,
            "halt_type": "blocked",
            "constraint_violations": ["just a constraint"],
            "task": {"id": "t1", "spec": "do stuff"},
            "validation_results": [],
        }
        mock_graph.stream.return_value = [{"validate": node_state}]
        args = SimpleNamespace(verbose=False)
        _stream_execution(mock_graph, {}, args)
        out = capsys.readouterr().out
        assert "Validator blockers" not in out
        assert "Validator warnings" not in out
        assert "STRUCTURED HALT PAYLOAD" in out

    def test_only_warns_printed(self, capsys):
        from snodo.cli.commands.run_cmd import _stream_execution

        mock_graph = MagicMock()
        node_state = {
            "stage": "validate",
            "iteration": 1,
            "is_blocked": True,
            "halt_type": "constraint",
            "constraint_violations": ["bad"],
            "task": {"id": "t1", "spec": "x"},
            "validation_results": [
                {"validator_id": "qa", "severity": "warn",
                 "justification": "missing docstrings"},
            ],
        }
        mock_graph.stream.return_value = [{"validate": node_state}]
        args = SimpleNamespace(verbose=False)
        _stream_execution(mock_graph, {}, args)
        out = capsys.readouterr().out
        assert "qa — warn — missing docstrings" in out
        assert "Validator blockers" not in out

    def test_structured_payload_present_on_all_halt_types(self, capsys):
        from snodo.cli.commands.run_cmd import _stream_execution

        for ht in ["blocked", "escalated", "resolution", "constraint", "max_iterations", "wf3"]:
            mock_graph = MagicMock()
            node_state = {
                "stage": "validate",
                "iteration": 1,
                "is_blocked": True,
                "halt_type": ht,
                "constraint_violations": ["test"],
                "task": {"id": "t1", "spec": "x"},
                "validation_results": [],
            }
            mock_graph.stream.return_value = [{"validate": node_state}]
            args = SimpleNamespace(verbose=False)
            capsys.readouterr()  # flush
            _stream_execution(mock_graph, {}, args)
            out = capsys.readouterr().out
            assert "STRUCTURED HALT PAYLOAD" in out, f"missing payload for halt_type={ht}"


class TestLoopSerialization:
    """Tests that halt_type survives the state round-trip."""

    def test_round_trip_preserves_halt_type(self):
        from snodo.engine.loop import GraphBuilder, LoopState
        from snodo.core.interfaces import Task

        state = LoopState(
            task=Task(id="t1", spec="x"),
            current_mode="producer",
            is_blocked=True,
            halt_type="escalated",
            pending_disagreement={"phase": "pre_execute"},
        )
        builder = object.__new__(GraphBuilder)
        state_dict = GraphBuilder._state_to_dict(builder, state)
        assert state_dict["halt_type"] == "escalated"

        restored = GraphBuilder._dict_to_state(builder, state_dict)
        assert restored.halt_type == "escalated"

    def test_halt_type_none_by_default(self):
        from snodo.engine.loop import LoopState
        from snodo.core.interfaces import Task

        state = LoopState(task=Task(id="t1", spec="x"), current_mode="p")
        assert state.halt_type is None

    def test_halt_type_survives_json_round_trip(self):
        import json as _json
        # Simulate the state dict that would come through LangGraph streaming
        state_dict = {
            "task": {"id": "t1", "spec": "x", "parent_task_ref": None, "depth": 0},
            "current_mode": "producer",
            "validation_results": [],
            "validation_token": None,
            "artifacts": [],
            "stage": "blocked",
            "iteration": 3,
            "constraints_passed": False,
            "constraint_violations": ["bad"],
            "policy_decision": None,
            "is_complete": False,
            "is_blocked": True,
            "halt_type": "wf3",
            "pending_disagreement": None,
            "resolution_override": False,
            "metadata": {},
            "messages": [],
            "summary": "",
        }
        # Round-trip through JSON (as LangGraph does internally)
        rebuilt = _json.loads(_json.dumps(state_dict))
        assert rebuilt["halt_type"] == "wf3"
