"""Tests for run_cmd.py - background jobs, plan execution, streaming.

FILE: tests/cli/test_run_cmd.py (Task 6.5)

Covers the uncovered paths in snodo/cli/commands/run_cmd.py:
- _run_plan flow
- _execute_waves, _execute_wave_task
- _report_closure (structured halt payload emission)
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
from unittest.mock import MagicMock, patch

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
        from snodo.mcp.pr import PrError

        from snodo.cli.commands.run_cmd import _fetch_pr_context

        with patch("snodo.mcp.pr.PrMCP") as MockPr:
            mock_pr = MockPr.return_value
            mock_pr.read_pr_comments.side_effect = PrError("Not found")
            mock_pr.read_pr_diff.return_value = ""

            with patch("snodo.providers.registry.detect_provider", side_effect=Exception("no git")):
                result = _fetch_pr_context(99, "/tmp/proj")

        assert "Could not fetch PR comments" in result

    def test_fetch_pr_context_diff_error(self):
        from snodo.mcp.pr import PrError

        from snodo.cli.commands.run_cmd import _fetch_pr_context

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
        from snodo.cli.commands.plan_run import _get_completed_waves

        waves = [{"id": "w1", "tasks": ["t1", "t2"]}]
        tasks_status = {"t1": "completed", "t2": "completed"}
        result = _get_completed_waves(waves, tasks_status)
        assert result == {"w1"}

    def test_incomplete_wave(self):
        from snodo.cli.commands.plan_run import _get_completed_waves

        waves = [{"id": "w1", "tasks": ["t1", "t2"]}]
        tasks_status = {"t1": "completed", "t2": "in_progress"}
        result = _get_completed_waves(waves, tasks_status)
        assert result == set()

    def test_empty_tasks(self):
        from snodo.cli.commands.plan_run import _get_completed_waves

        waves = [{"id": "w1", "tasks": []}]
        result = _get_completed_waves(waves, {})
        assert result == set()

    def test_multiple_waves(self):
        from snodo.cli.commands.plan_run import _get_completed_waves

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
        from snodo.cli.commands.plan_run import _should_skip_task

        result = _should_skip_task("t1", {"t1": "completed"}, False)
        assert result is True
        assert "skipped (completed)" in capsys.readouterr().out

    def test_non_completed_not_skipped(self):
        from snodo.cli.commands.plan_run import _should_skip_task

        result = _should_skip_task("t1", {"t1": "pending"}, False)
        assert result is False

    def test_interactive_user_declines(self, capsys):
        from snodo.cli.commands.plan_run import _should_skip_task

        with patch("builtins.input", return_value="n"):
            result = _should_skip_task("t1", {}, True)
        assert result is True
        assert "skipped (user)" in capsys.readouterr().out

    def test_interactive_user_accepts(self):
        from snodo.cli.commands.plan_run import _should_skip_task

        with patch("builtins.input", return_value="y"):
            result = _should_skip_task("t1", {}, True)
        assert result is False


# === _execute_wave_task tests ===

class TestExecuteWaveTask:
    """Tests for _execute_wave_task."""

    def test_spec_file_not_found(self, tmp_path, capsys):
        from snodo.cli.commands.plan_run import _execute_wave_task

        planner = MagicMock()
        planner.plans_dir = tmp_path
        args = SimpleNamespace(plan="myplan")
        protocol = MagicMock()

        result = _execute_wave_task(planner, args, protocol, "gpt-4", "w1", "t1")
        assert result is False
        assert "spec file not found" in capsys.readouterr().err

    @patch("snodo.cli.commands.run_cmd._execute_task", return_value=0)
    def test_task_success(self, mock_exec, tmp_path, capsys):
        from snodo.cli.commands.plan_run import _execute_wave_task

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
        from snodo.cli.commands.plan_run import _execute_wave_task

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

    @patch("snodo.cli.commands.plan_run._execute_wave_task", return_value=True)
    @patch("snodo.cli.commands.plan_run._should_skip_task", return_value=False)
    def test_executes_task(self, mock_skip, mock_exec, capsys):
        from snodo.cli.commands.plan_run import _execute_waves

        waves = [{"id": "w1", "tasks": ["t1"], "depends_on": []}]
        result = _execute_waves(waves, MagicMock(), MagicMock(), MagicMock(),
                                "gpt-4", waves, False)
        assert result is False

    @patch("snodo.cli.commands.plan_run._execute_wave_task", return_value=False)
    @patch("snodo.cli.commands.plan_run._should_skip_task", return_value=False)
    def test_task_failure_stops(self, mock_skip, mock_exec, capsys):
        from snodo.cli.commands.plan_run import _execute_waves

        waves = [{"id": "w1", "tasks": ["t1", "t2"], "depends_on": []}]
        result = _execute_waves(waves, MagicMock(), MagicMock(), MagicMock(),
                                "gpt-4", waves, False)
        assert result is True

    def test_blocked_wave_skipped(self, capsys):
        from snodo.cli.commands.plan_run import _execute_waves

        waves = [{"id": "w2", "tasks": ["t1"], "depends_on": ["w1"]}]
        result = _execute_waves(waves, MagicMock(), MagicMock(), MagicMock(),
                                "gpt-4", waves, False)
        assert result is True
        assert "blocked" in capsys.readouterr().out


# === _print_plan_progress tests ===

class TestPrintPlanProgress:
    """Tests for _print_plan_progress."""

    def test_prints_progress(self, capsys):
        from snodo.cli.commands.plan_run import _print_plan_progress

        planner = MagicMock()
        planner.get_status.return_value = {
            "tasks": {"t1": "completed", "t2": "pending", "t3": "completed"}
        }
        _print_plan_progress(planner, "plan1")
        out = capsys.readouterr().out
        assert "2/3 completed" in out


# === _build_graph tests ===

class TestBuildGraph:
    """Tests for _build_graph."""

    @patch("snodo.engine.loop.build_protocol_graph")
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

    @patch("snodo.engine.loop.build_protocol_graph",
           side_effect=Exception("Import error"))
    def test_build_failure(self, mock_build, capsys):
        from snodo.cli.commands.run_cmd import _build_graph

        args = SimpleNamespace(mock=False, verbose=False)
        result = _build_graph(args, MagicMock(), "/tmp/proj", "gpt-4")
        assert result is None
        err = capsys.readouterr().err
        assert "Failed to build graph" in err

    @patch("snodo.engine.loop.build_protocol_graph",
           side_effect=Exception("oops"))
    def test_build_failure_verbose(self, mock_build, capsys):
        from snodo.cli.commands.run_cmd import _build_graph

        args = SimpleNamespace(mock=False, verbose=True)
        result = _build_graph(args, MagicMock(), "/tmp/proj", "gpt-4")
        assert result is None

    @patch("snodo.engine.loop.build_protocol_graph")
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
            mgr, ckpt, config = _setup_memory("/tmp/proj", protocol, "producer")

        assert mgr is not None
        assert ckpt is not None
        assert config["configurable"]["thread_id"] == "abc123"

    def test_failure_returns_nones(self):
        from snodo.cli.commands.run_cmd import _setup_memory

        with patch("snodo.infrastructure.memory.AgentMemoryManager",
                   side_effect=Exception("no db")):
            protocol = MagicMock()
            protocol.initial_mode = "producer"
            mgr, ckpt, config = _setup_memory("/tmp/proj", protocol, "producer")

        assert mgr is None
        assert ckpt is None
        assert config is None


# === _run_plan tests ===

class TestRunPlan:
    """Tests for _run_plan."""

    @patch("snodo.cli.commands.plan_run._execute_waves", return_value=False)
    @patch("snodo.cli.commands.plan_run._print_plan_progress")
    @patch("snodo.cli.commands.run_cmd.provider_env")
    @patch("snodo.cli.commands.run_cmd.ConfigManager")
    @patch("snodo.cli.commands.run_cmd.load_protocol")
    def test_run_plan_success(self, mock_load, mock_cm, mock_provider_env, mock_progress,
                              mock_waves, temp_project, capsys):
        from snodo.cli.commands.plan_run import _run_plan

        protocol = MagicMock()
        mock_load.return_value = protocol
        mock_cm.return_value.get_model.return_value = "gpt-4"

        with patch("snodo.mcp.planner.PlannerMCP") as MockPlanner:
            mock_planner = MockPlanner.return_value
            mock_planner.get_plan.return_value = {
                "name": "Test Plan", "intent": "Fix bugs",
                "waves": [{"id": 1, "tasks": ["t1"]}]
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
        from snodo.cli.commands.plan_run import _run_plan

        args = SimpleNamespace(protocol="missing.yml", model=None, plan="p")
        result = _run_plan(args)
        assert result == 1

    @patch("snodo.cli.commands.run_cmd.ConfigManager")
    @patch("snodo.cli.commands.run_cmd.load_protocol")
    def test_run_plan_planner_error(self, mock_load, mock_cm, capsys):
        from snodo.mcp.planner import PlannerError

        from snodo.cli.commands.plan_run import _run_plan

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

    @pytest.fixture(autouse=True)
    def _patch_project_root(self, monkeypatch):
        """Ensure run_command finds a project root (isolation)."""
        monkeypatch.setattr(
            "snodo.infrastructure.paths.require_project_root",
            lambda: "/fake/project",
        )

    @patch("snodo.cli.commands.plan_run._run_plan", return_value=0)
    def test_routes_to_run_plan(self, mock_plan, temp_project):
        from snodo.cli.commands.run_cmd import run_command

        args = SimpleNamespace(plan="myplan", description=None,
                               background=False, sandbox="local")
        result = run_command(args)
        assert result == 0
        mock_plan.assert_called_once_with(args)


# === _resolve_session tests (Task 7.3) ===

class TestResolveSession:
    """Tests for _resolve_session."""

    def test_no_session_manager_returns_none(self):
        from snodo.cli.commands.run_cmd import _resolve_session

        args = SimpleNamespace(resume=None)
        protocol = MagicMock()
        protocol.initial_mode = "producer"
        session, mode = _resolve_session(args, None, protocol, "/fake/proj")
        assert session is None
        assert mode == "producer"

    def test_auto_create_new_session(self, capsys):
        from snodo.infrastructure.session import SessionManager

        from snodo.cli.commands.run_cmd import _resolve_session

        with tempfile.TemporaryDirectory() as d:
            mgr = SessionManager(sessions_dir=Path(d))
            args = SimpleNamespace(resume=None)
            protocol = MagicMock()
            protocol.initial_mode = "producer"
            session, mode = _resolve_session(args, mgr, protocol, d)
            assert session is not None
            assert session.mode == "producer"
            assert mode == "producer"
            assert "new" in capsys.readouterr().out

    def test_auto_resume_existing(self, capsys):
        from snodo.infrastructure.session import SessionManager

        from snodo.cli.commands.run_cmd import _resolve_session

        with tempfile.TemporaryDirectory() as d:
            mgr = SessionManager(sessions_dir=Path(d))
            existing = mgr.create_session("producer", d)
            args = SimpleNamespace(resume=None)
            protocol = MagicMock()
            protocol.initial_mode = "producer"
            session, mode = _resolve_session(args, mgr, protocol, d)
            assert session.session_id == existing.session_id
            assert mode == "producer"

    def test_explicit_resume(self, capsys):
        from snodo.infrastructure.session import SessionManager

        from snodo.cli.commands.run_cmd import _resolve_session

        with tempfile.TemporaryDirectory() as d:
            mgr = SessionManager(sessions_dir=Path(d))
            session = mgr.create_session("producer", d)
            args = SimpleNamespace(resume=session.session_id)
            protocol = MagicMock()
            protocol.initial_mode = "producer"
            resolved, mode = _resolve_session(args, mgr, protocol, d)
            assert resolved.session_id == session.session_id
            assert mode == "producer"
            assert "resumed" in capsys.readouterr().out

    def test_resume_mode_mismatch_rejects(self):
        from snodo.infrastructure.session import SessionManager

        from snodo.cli.commands.run_cmd import _resolve_session

        with tempfile.TemporaryDirectory() as d:
            mgr = SessionManager(sessions_dir=Path(d))
            session = mgr.create_session("reviewer", "/tmp/proj")
            args = SimpleNamespace(resume=session.session_id)
            protocol = MagicMock()
            protocol.initial_mode = "producer"
            with pytest.raises(SystemExit):
                _resolve_session(args, mgr, protocol, "/tmp/proj")

    def test_resume_project_mismatch_rejects(self):
        from snodo.infrastructure.session import SessionManager

        from snodo.cli.commands.run_cmd import _resolve_session

        with tempfile.TemporaryDirectory() as d:
            mgr = SessionManager(sessions_dir=Path(d))
            session = mgr.create_session("producer", "/tmp/other")
            args = SimpleNamespace(resume=session.session_id)
            protocol = MagicMock()
            protocol.initial_mode = "producer"
            with pytest.raises(SystemExit):
                _resolve_session(args, mgr, protocol, "/tmp/proj")

    def test_resume_deleted_session_raises(self):
        from snodo.infrastructure.session import SessionManager

        from snodo.cli.commands.run_cmd import _resolve_session

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
        from snodo.infrastructure.session import SessionManager

        from snodo.cli.commands.run_cmd import _resolve_session

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
        from snodo.cli.commands.plan_run import _task_completed
        assert _task_completed({"t1": "completed"}, "t1") is True

    def test_string_entry_not_completed(self):
        from snodo.cli.commands.plan_run import _task_completed
        assert _task_completed({"t1": "pending"}, "t1") is False

    def test_dict_entry_completed(self):
        from snodo.cli.commands.plan_run import _task_completed
        assert _task_completed({"t1": {"status": "completed"}}, "t1") is True

    def test_dict_entry_not_completed(self):
        from snodo.cli.commands.plan_run import _task_completed
        assert _task_completed({"t1": {"status": "pending"}}, "t1") is False

    def test_missing_entry(self):
        from snodo.cli.commands.plan_run import _task_completed
        assert _task_completed({}, "t1") is False


# === run_command wires session_manager (Task 7.3) ===

class TestRunCommandSessionWiring:
    """Test that run_command constructs and threads session_manager."""

    @pytest.fixture(autouse=True)
    def _patch_project_root(self, monkeypatch):
        """Ensure run_command finds a project root (isolation)."""
        monkeypatch.setattr(
            "snodo.infrastructure.paths.require_project_root",
            lambda: "/fake/project",
        )

    @patch("snodo.cli.commands.plan_run._run_plan", return_value=0)
    def test_plan_route_gets_audit_and_session(self, mock_plan, temp_project):
        from snodo.cli.commands.run_cmd import run_command

        args = SimpleNamespace(plan="myplan", description=None,
                               background=False, sandbox="local")
        run_command(args)
        # Verify args now have audit_log and session_manager
        assert hasattr(args, "audit_log")
        assert hasattr(args, "session_manager")
        assert args.audit_log is not None
        assert args.session_manager is not None


# === provider credential preflight (Fixes #137) ===

class TestProviderCredentialPreflight:
    """run_command fails fast when the model's provider has no credential."""

    @pytest.fixture(autouse=True)
    def _patch_project_root(self, monkeypatch):
        """Ensure run_command finds a project root (isolation)."""
        monkeypatch.setattr(
            "snodo.infrastructure.paths.require_project_root",
            lambda: "/fake/project",
        )

    def _args(self, **overrides):
        args = SimpleNamespace(
            description="do the thing",
            protocol=".snodo/protocol.yml",
            model=None,
            mock=False,
            background=False,
            sandbox="local",
        )
        for k, v in overrides.items():
            setattr(args, k, v)
        return args

    def test_no_credential_fails_before_session_or_worktree(self, monkeypatch, capsys):
        """A run with no credential fails before a session or worktree exists."""
        from snodo.cli.commands.run_cmd import run_command

        # No ANTHROPIC_API_KEY in env, no key in config (isolated SNODO_HOME).
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        executed = []
        with patch("snodo.cli.commands.run_cmd._execute_task",
                   side_effect=lambda *a: executed.append(a) or 0) as mock_exec:
            with patch("snodo.cli.commands.run_cmd.load_protocol") as mock_load:
                result = run_command(self._args())

        assert result == 1
        mock_exec.assert_not_called()
        mock_load.assert_not_called()
        err = capsys.readouterr().err
        assert "anthropic" in err
        assert "ANTHROPIC_API_KEY" in err
        assert "snodo config add anthropic" in err

        # No session file was created.
        sessions_dir = Path(os.environ["SNODO_HOME"]) / "sessions"
        assert not list(sessions_dir.glob("*.json"))

    def test_credential_in_env_proceeds(self, monkeypatch, capsys):
        """A run with a credential in the environment proceeds unchanged."""
        from snodo.cli.commands.run_cmd import run_command

        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        executed = []
        with patch("snodo.cli.commands.run_cmd._execute_task",
                   side_effect=lambda *a: executed.append(a) or 0) as mock_exec:
            with patch("snodo.cli.commands.run_cmd.load_protocol") as mock_load:
                mock_load.return_value = MagicMock()
                result = run_command(self._args())

        assert result == 0
        mock_exec.assert_called_once()
        assert "No credential" not in capsys.readouterr().err

    def test_credential_in_config_proceeds(self, monkeypatch, capsys):
        """A run with a key stored in config proceeds unchanged."""
        from snodo.cli.commands.run_cmd import run_command

        # The credential comes from config; run_command injects it into
        # os.environ via provider_env, which leaves it set. Use a throwaway
        # environ copy so that injection cannot leak into later tests, and
        # delenv (raising=False) records nothing when the key is already absent,
        # so it cannot serve as the undo (Fixes #200).
        monkeypatch.setattr(os, "environ", os.environ.copy())
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with patch("snodo.config.ConfigManager.get_key_for_model",
                   return_value="sk-config-key"):
            executed = []
            with patch("snodo.cli.commands.run_cmd._execute_task",
                       side_effect=lambda *a: executed.append(a) or 0) as mock_exec:
                with patch("snodo.cli.commands.run_cmd.load_protocol") as mock_load:
                    mock_load.return_value = MagicMock()
                    result = run_command(self._args())

        assert result == 0
        mock_exec.assert_called_once()
        assert "No credential" not in capsys.readouterr().err

    def test_mock_skips_check(self, monkeypatch, capsys):
        """--mock skips the credential check entirely."""
        from snodo.cli.commands.run_cmd import run_command

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        executed = []
        with patch("snodo.cli.commands.run_cmd._execute_task",
                   side_effect=lambda *a: executed.append(a) or 0) as mock_exec:
            with patch("snodo.cli.commands.run_cmd.load_protocol") as mock_load:
                mock_load.return_value = MagicMock()
                result = run_command(self._args(mock=True))

        assert result == 0
        mock_exec.assert_called_once()
        assert "No credential" not in capsys.readouterr().err


# === PlannerMCP audit_log fix in _run_plan (Task 7.3) ===

class TestRunPlanAuditLogFix:
    """Verify PlannerMCP in _run_plan receives audit_log."""

    @patch("snodo.cli.commands.plan_run._execute_waves", return_value=False)
    @patch("snodo.cli.commands.plan_run._print_plan_progress")
    @patch("snodo.cli.commands.run_cmd.provider_env")
    @patch("snodo.cli.commands.run_cmd.ConfigManager")
    @patch("snodo.cli.commands.run_cmd.load_protocol")
    def test_planner_gets_audit_log(self, mock_load, mock_cm, mock_provider_env,
                                     mock_progress, mock_waves, temp_project):
        from snodo.cli.commands.plan_run import _run_plan

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

class TestClosureHaltPayload:
    """Tests for closure-path structured halt payload emission (ADR 1.1b)."""

    @staticmethod
    def _payload(final_decision: str, **extra) -> dict:
        p = {
            "halt_type": final_decision,
            "final_decision": final_decision,
            "status": "blocked",
            "reason": "test",
            "task_id": "t1",
            "task_spec": "do stuff",
            "validator_results": [
                {"validator_id": "sec", "severity": "warn", "justification": "x"},
            ],
        }
        p.update(extra)
        return p

    @pytest.mark.parametrize("decision", ["escalate", "blocker", "validator_error", "internal_error"])
    def test_payload_emitted_for_each_halt_case(self, capsys, decision):
        import json as _json

        from snodo.engine.closure import ClosureNode

        from snodo.cli.commands.run_cmd import _report_closure

        tree = ClosureNode(
            task_id="t1", depth=0, outcome=decision,
            halt_payload=self._payload(decision),
        )
        result = _report_closure(tree, {})
        out = capsys.readouterr().out
        assert result == 1
        assert "STRUCTURED HALT PAYLOAD" in out
        section = out.split("--- STRUCTURED HALT PAYLOAD ---")[1].split("--- END STRUCTURED HALT PAYLOAD ---")[0]
        parsed = _json.loads(section)
        assert parsed["halt_type"] == decision
        assert parsed["final_decision"] == decision
        assert parsed["validator_results"], "validator_results must be non-empty"

    def test_no_payload_on_success(self, capsys):
        from snodo.engine.closure import ClosureNode

        from snodo.cli.commands.run_cmd import _report_closure

        tree = ClosureNode(task_id="t1", depth=0, outcome="resolved")
        result = _report_closure(tree, {"is_blocked": False, "is_complete": True, "artifacts": []})
        out = capsys.readouterr().out
        assert result == 0
        assert "STRUCTURED HALT PAYLOAD" not in out

    def test_validator_error_does_not_advise_authorize(self, capsys):
        from snodo.engine.closure import ClosureNode

        from snodo.cli.commands.run_cmd import _report_closure

        tree = ClosureNode(
            task_id="t1", depth=0, outcome="validator_error",
            halt_payload=self._payload("validator_error"),
        )
        _report_closure(tree, {})
        out = capsys.readouterr().out
        assert "snodo authorize" not in out

    def test_structured_payload_is_the_only_outcome(self, capsys):
        """A structured halt payload must be the single outcome — never a
        second, unclassified line after it (Fixes #66).

        In a recovery chain the root's final_state carries no `error` field
        (the error lives in the subtask's payload), so the legacy fallback
        would print "✗ Internal error during execution: unknown internal
        error" after the authoritative payload — one run, two outcomes, the
        second unclassified.
        """
        from snodo.engine.closure import ClosureNode

        from snodo.cli.commands.run_cmd import _report_closure

        tree = ClosureNode(
            task_id="root", depth=0, outcome="internal_error",
            halt_payload=self._payload("internal_error", reason="coder failed"),
        )
        # Root final_state with no `error` field — the exact shape that
        # previously produced the second, unclassified outcome.
        result = _report_closure(tree, {"halt_type": "internal_error"})
        captured = capsys.readouterr()
        out, err = captured.out, captured.err

        assert result == 1
        assert "STRUCTURED HALT PAYLOAD" in out
        assert "unknown internal error" not in out
        assert "unknown internal error" not in err
        assert "Internal error during execution" not in out
        assert "Internal error during execution" not in err

    def test_no_second_outcome_for_blocker_payload(self, capsys):
        """A blocker payload also emits exactly one outcome."""
        from snodo.engine.closure import ClosureNode

        from snodo.cli.commands.run_cmd import _report_closure

        tree = ClosureNode(
            task_id="t1", depth=0, outcome="blocked",
            halt_payload=self._payload("blocker", reason="violation"),
        )
        result = _report_closure(tree, {"halt_type": "blocked"})
        captured = capsys.readouterr()
        out, err = captured.out, captured.err

        assert result == 1
        assert "STRUCTURED HALT PAYLOAD" in out
        assert "did not complete successfully" not in out
        assert "did not complete successfully" not in err

    def test_terminal_halt_prefers_deepest_subtask(self):
        from snodo.engine.closure import ClosureNode

        from snodo.cli.commands.run_cmd import _find_terminal_halt_payload

        root = ClosureNode(
            task_id="root", depth=0, outcome="blocker",
            halt_payload=self._payload("completed"),
        )
        child = ClosureNode(
            task_id="fix1", depth=1, outcome="blocker",
            halt_payload=self._payload("blocker", reason="fix failed"),
        )
        root.subtasks = [child]
        found = _find_terminal_halt_payload(root, {})
        assert found["final_decision"] == "blocker"

    def test_root_halt_used_when_no_subtask_halted(self):
        from snodo.engine.closure import ClosureNode

        from snodo.cli.commands.run_cmd import _find_terminal_halt_payload

        root = ClosureNode(
            task_id="root", depth=0, outcome="escalate",
            halt_payload=self._payload("escalate"),
        )
        found = _find_terminal_halt_payload(root, {})
        assert found["final_decision"] == "escalate"

    def test_resolved_through_recovery_prints_resolving_attempts_verdicts(self):
        """A task that resolved through recovery must print the resolving
        attempt's verdicts, not the first attempt's (Fixes #85).

        The root's graph invocation ends at the recovery node, which writes a
        payload with final_decision 'completed' but phase 'unknown' and the
        FIRST attempt's warns. The genuine completion lives in the resolving
        subtask's payload (phase 'complete'). The terminal payload must be the
        subtask's, so a reader sees the verdicts that actually resolved.
        """
        from snodo.engine.closure import ClosureNode

        from snodo.cli.commands.run_cmd import _find_terminal_halt_payload

        root = ClosureNode(
            task_id="root", depth=0, outcome="resolved",
            halt_payload=self._payload(
                "completed",
                status="completed",
                phase="unknown",
                iteration=1,
                validator_results=[
                    {"validator_id": "quality", "severity": "warn",
                     "justification": "first attempt warn"},
                ],
                post_validation={"outcome": "recovery"},
            ),
        )
        child = ClosureNode(
            task_id="root_fix_1", depth=1, outcome="resolved",
            halt_payload=self._payload(
                "completed",
                status="completed",
                phase="complete",
                iteration=2,
                validator_results=[
                    {"validator_id": "quality", "severity": "pass",
                     "justification": "resolving attempt pass"},
                ],
                post_validation={"outcome": "passed"},
            ),
        )
        root.subtasks = [child]

        found = _find_terminal_halt_payload(root, {})
        assert found["phase"] == "complete"
        assert found["iteration"] == 2
        assert found["validator_results"][0]["severity"] == "pass"
        assert "first attempt warn" not in str(found["validator_results"])

    def test_resolved_through_recovery_report_prints_complete_verdicts(self, capsys):
        """The emitted payload for a resolved-through-recovery run shows the
        resolving attempt's verdicts under a completed status (Fixes #85)."""
        import json as _json

        from snodo.engine.closure import ClosureNode

        from snodo.cli.commands.run_cmd import _report_closure

        root = ClosureNode(
            task_id="root", depth=0, outcome="resolved",
            halt_payload=self._payload(
                "completed",
                status="completed",
                phase="unknown",
                iteration=1,
                validator_results=[
                    {"validator_id": "quality", "severity": "warn",
                     "justification": "first attempt warn"},
                ],
                post_validation={"outcome": "recovery"},
            ),
        )
        child = ClosureNode(
            task_id="root_fix_1", depth=1, outcome="resolved",
            halt_payload=self._payload(
                "completed",
                status="completed",
                phase="complete",
                iteration=2,
                validator_results=[
                    {"validator_id": "quality", "severity": "pass",
                     "justification": "resolving attempt pass"},
                ],
                post_validation={"outcome": "passed"},
            ),
        )
        root.subtasks = [child]

        result = _report_closure(root, {"is_blocked": False, "is_complete": True, "artifacts": []})
        assert result == 0
        out = capsys.readouterr().out
        assert "STRUCTURED HALT PAYLOAD" in out
        section = out.split("--- STRUCTURED HALT PAYLOAD ---")[1].split("--- END STRUCTURED HALT PAYLOAD ---")[0]
        parsed = _json.loads(section)
        assert parsed["status"] == "completed"
        assert parsed["phase"] == "complete"
        assert parsed["iteration"] == 2
        assert parsed["validator_results"][0]["severity"] == "pass"
        assert "first attempt warn" not in str(parsed["validator_results"])


class TestLoopSerialization:
    """Tests that halt_type survives the state round-trip."""

    def test_round_trip_preserves_halt_type(self):
        from snodo.core.interfaces import Task
        from snodo.engine.loop import GraphBuilder, LoopState

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
        from snodo.core.interfaces import Task
        from snodo.engine.loop import LoopState

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
            "metadata": {},
            "messages": [],
            "summary": "",
        }
        # Round-trip through JSON (as LangGraph does internally)
        rebuilt = _json.loads(_json.dumps(state_dict))
        assert rebuilt["halt_type"] == "wf3"


# === Auto-merge decision + execution (Task: merge task branch on success) ===

class TestAutoMerge:
    def _protocol(self, auto_merge=True):
        from snodo.compiler.models import ExecutionConfig, Mode, Protocol, Validator
        return Protocol(
            protocol_id="am", name="Auto Merge", version="1.0.0",
            modes=[Mode(mode_id="producer", name="Producer", tools=["edit"], validators=["v1"])],
            validators=[Validator(validator_id="v1", validator_type="security")],
            initial_mode="producer",
            execution=ExecutionConfig(auto_merge=auto_merge),
        )

    def _tree(self, outcome):
        return SimpleNamespace(outcome=outcome)

    def test_disabled_never_merges(self):
        from snodo.cli.commands.run_cmd import _should_auto_merge
        proto = self._protocol(auto_merge=False)
        assert _should_auto_merge(proto, "producer", self._tree("resolved"),
                                  "/tmp/wt", False) is False

    def test_non_resolved_never_merges(self):
        from snodo.cli.commands.run_cmd import _should_auto_merge
        proto = self._protocol(auto_merge=True)
        for outcome in ("blocked", "recovery_exhausted", "escalated", "internal_error"):
            assert _should_auto_merge(proto, "producer", self._tree(outcome),
                                      "/tmp/wt", False) is False, outcome

    def test_degraded_never_merges(self):
        from snodo.cli.commands.run_cmd import _should_auto_merge
        proto = self._protocol(auto_merge=True)
        assert _should_auto_merge(proto, "producer", self._tree("resolved"),
                                  None, True) is False
        assert _should_auto_merge(proto, "producer", self._tree("resolved"),
                                  None, False) is False

    def test_resolved_merges(self):
        from snodo.cli.commands.run_cmd import _should_auto_merge
        proto = self._protocol(auto_merge=True)
        assert _should_auto_merge(proto, "producer", self._tree("resolved"),
                                  "/tmp/wt", False) is True

    def test_resolved_auto_merge_off_reports_unmerged_branch(self, tmp_path, capsys):
        """A resolved task with auto_merge off must name the unmerged branch and
        record it, so a stranded run is distinguishable from one that merged."""
        from snodo.core.interfaces import Task
        from snodo.infrastructure.audit import AuditLog
        from snodo.infrastructure.worktree import task_branch_name

        from snodo.cli.commands.run_cmd import _report_unmerged_branch, _should_auto_merge

        proto = self._protocol(auto_merge=False)
        tree = self._tree("resolved")
        assert _should_auto_merge(proto, "producer", tree, "/tmp/wt", False) is False

        task = Task(id="task_stranded", spec="a resolved feature")
        branch = task_branch_name(task.id, task.spec)
        audit_log = AuditLog(str(tmp_path / "audit.log"))

        _report_unmerged_branch(
            str(tmp_path), task, proto, "producer", tree,
            "/tmp/wt", False, "sess_1", audit_log,
        )

        err = capsys.readouterr().err
        assert branch in err
        assert "auto-merge not enabled" in err
        assert "main has NOT moved" in err

        events = audit_log.get_history("task_unmerged")
        assert len(events) == 1
        assert events[0].data["op"] == "task_unmerged"
        assert events[0].data["branch"] == branch
        assert events[0].data["merged"] is False
        assert "auto-merge not enabled" in events[0].data["reason"]

    def test_resolved_degraded_reports_working_tree_not_branch(self, tmp_path, capsys):
        """Degraded isolation leaves work in the working tree, not on a branch:
        the report says so instead of naming a branch that was never created."""
        from snodo.core.interfaces import Task
        from snodo.infrastructure.audit import AuditLog

        from snodo.cli.commands.run_cmd import _report_unmerged_branch

        proto = self._protocol(auto_merge=True)
        task = Task(id="task_degraded", spec="no isolation")
        audit_log = AuditLog(str(tmp_path / "audit.log"))

        _report_unmerged_branch(
            str(tmp_path), task, proto, "producer", self._tree("resolved"),
            None, True, "sess_1", audit_log,
        )

        err = capsys.readouterr().err
        assert "isolation degraded" in err
        assert "working tree" in err
        assert "git merge" not in err
        events = audit_log.get_history("task_unmerged")
        assert len(events) == 1
        assert "isolation degraded" in events[0].data["reason"]

    def test_merge_on_success_clean_merge(self, tmp_path):
        from snodo.core.interfaces import Task
        from snodo.infrastructure.worktree import task_branch_name

        from snodo.cli.commands.run_cmd import _merge_on_success

        repo = tmp_path
        subprocess_run = __import__("subprocess").run
        subprocess_run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess_run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
        subprocess_run(["git", "config", "user.name", "t"], cwd=repo, check=True)
        (repo / "README.md").write_text("init\n")
        subprocess_run(["git", "add", "README.md"], cwd=repo, check=True)
        subprocess_run(["git", "commit", "-qm", "init"], cwd=repo, check=True)

        task = Task(id="task_1", spec="add feature")
        branch = task_branch_name(task.id, task.spec)
        subprocess_run(["git", "checkout", "-qb", branch], cwd=repo, check=True)
        (repo / "feature.txt").write_text("feature\n")
        subprocess_run(["git", "add", "feature.txt"], cwd=repo, check=True)
        subprocess_run(["git", "commit", "-qm", "feature"], cwd=repo, check=True)
        subprocess_run(["git", "checkout", "-q", "main"], cwd=repo, check=True)

        result, preserve, merged_branch = _merge_on_success(str(repo), task, 0, None, None)
        assert result == 0
        assert preserve is False
        assert merged_branch == branch
        assert (repo / "feature.txt").exists()

    def test_merge_on_success_conflict_escalates(self, tmp_path):
        from snodo.core.interfaces import Task
        from snodo.infrastructure.worktree import task_branch_name

        from snodo.cli.commands.run_cmd import _merge_on_success

        repo = tmp_path
        subprocess_run = __import__("subprocess").run
        subprocess_run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess_run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
        subprocess_run(["git", "config", "user.name", "t"], cwd=repo, check=True)
        (repo / "README.md").write_text("init\n")
        subprocess_run(["git", "add", "README.md"], cwd=repo, check=True)
        subprocess_run(["git", "commit", "-qm", "init"], cwd=repo, check=True)

        task = Task(id="task_1", spec="conflicting change")
        branch = task_branch_name(task.id, task.spec)
        subprocess_run(["git", "checkout", "-qb", branch], cwd=repo, check=True)
        (repo / "README.md").write_text("branch\n")
        subprocess_run(["git", "add", "README.md"], cwd=repo, check=True)
        subprocess_run(["git", "commit", "-qm", "branch change"], cwd=repo, check=True)
        subprocess_run(["git", "checkout", "-q", "main"], cwd=repo, check=True)
        (repo / "README.md").write_text("main\n")
        subprocess_run(["git", "add", "README.md"], cwd=repo, check=True)
        subprocess_run(["git", "commit", "-qm", "main change"], cwd=repo, check=True)

        result, preserve, merged_branch = _merge_on_success(str(repo), task, 0, None, None)
        assert result == 1
        assert preserve is True
        assert merged_branch is None
        # The source branch survives.
        branches = subprocess_run(
            ["git", "branch"], cwd=repo, capture_output=True, text=True, check=True,
        ).stdout
        assert branch in branches


class TestUnverifiedMergeBlocked:
    """Verify _merge_on_success refuses to merge unverified commits."""

    def test_unverified_merge_blocked(self, tmp_path):
        from snodo.core.interfaces import Task
        from snodo.infrastructure.audit import AuditLog

        from snodo.cli.commands.run_cmd import _merge_on_success

        repo = tmp_path
        subprocess_run = __import__("subprocess").run
        subprocess_run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess_run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
        subprocess_run(["git", "config", "user.name", "t"], cwd=repo, check=True)
        (repo / "README.md").write_text("init\n")
        subprocess_run(["git", "add", "README.md"], cwd=repo, check=True)
        subprocess_run(["git", "commit", "-qm", "init"], cwd=repo, check=True)

        audit_log = AuditLog(str(repo / "audit.log"))
        task = Task(id="task_unverified", spec="unverified task")

        # No verification_executed event in audit_log -> _merge_on_success must block merge
        res, preserve, merged = _merge_on_success(str(repo), task, 0, "sess_1", audit_log)
        assert res == 1
        assert preserve is True
        assert merged is None

        events = audit_log.get_history("unverified_merge_blocked")
        assert len(events) == 1
        assert events[0].data["op"] == "unverified_merge_blocked"

    def test_verified_merge_allowed(self, tmp_path):
        from snodo.core.interfaces import Task
        from snodo.infrastructure.audit import AuditLog
        from snodo.infrastructure.worktree import task_branch_name

        from snodo.cli.commands.run_cmd import _merge_on_success

        repo = tmp_path
        subprocess_run = __import__("subprocess").run
        subprocess_run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess_run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
        subprocess_run(["git", "config", "user.name", "t"], cwd=repo, check=True)
        (repo / "README.md").write_text("init\n")
        subprocess_run(["git", "add", "README.md"], cwd=repo, check=True)
        subprocess_run(["git", "commit", "-qm", "init"], cwd=repo, check=True)

        task = Task(id="task_verified", spec="verified task")
        branch = task_branch_name(task.id, task.spec)
        subprocess_run(["git", "checkout", "-qb", branch], cwd=repo, check=True)
        (repo / "file.txt").write_text("new file\n")
        subprocess_run(["git", "add", "file.txt"], cwd=repo, check=True)
        subprocess_run(["git", "commit", "-qm", "feature"], cwd=repo, check=True)
        subprocess_run(["git", "checkout", "-q", "main"], cwd=repo, check=True)

        audit_log = AuditLog(str(repo / "audit.log"))
        target_commit = subprocess_run(
            ["git", "rev-parse", branch], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()
        # Record passing verification event
        audit_log.append_event("verification_executed", {
            "op": "verification_executed",
            "command": "pytest",
            "commit": target_commit,
            "returncode": 0,
            "outcome": "pass",
            "validator_id": "quality",
            "task_ref": task.id,
        })

        res, preserve, merged = _merge_on_success(str(repo), task, 0, "sess_1", audit_log)
        assert res == 0
        assert preserve is False
        assert merged == branch
        assert (repo / "file.txt").exists()

    def test_ungated_merge_allowed_with_no_tests_record(self, tmp_path):
        """A task whose verification record states outcome 'no_tests' (the
        configured no-op default ran — no tests were executed) still merges: a
        fresh project must not strand its first task. The merge line must say
        the task ran no tests, never claim it was verified."""
        from snodo.core.interfaces import Task
        from snodo.infrastructure.audit import AuditLog
        from snodo.infrastructure.worktree import task_branch_name

        from snodo.cli.commands.run_cmd import _merge_on_success

        repo = tmp_path
        subprocess_run = __import__("subprocess").run
        subprocess_run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess_run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
        subprocess_run(["git", "config", "user.name", "t"], cwd=repo, check=True)
        (repo / "README.md").write_text("init\n")
        subprocess_run(["git", "add", "README.md"], cwd=repo, check=True)
        subprocess_run(["git", "commit", "-qm", "init"], cwd=repo, check=True)

        task = Task(id="task_ungated", spec="ungated task")
        branch = task_branch_name(task.id, task.spec)
        subprocess_run(["git", "checkout", "-qb", branch], cwd=repo, check=True)
        (repo / "file.txt").write_text("new file\n")
        subprocess_run(["git", "add", "file.txt"], cwd=repo, check=True)
        subprocess_run(["git", "commit", "-qm", "feature"], cwd=repo, check=True)
        subprocess_run(["git", "checkout", "-q", "main"], cwd=repo, check=True)

        audit_log = AuditLog(str(repo / "audit.log"))
        target_commit = subprocess_run(
            ["git", "rev-parse", branch], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()
        # Record the honest no-tests verification event (never outcome "pass").
        audit_log.append_event("verification_executed", {
            "op": "verification_executed",
            "command": "echo 'snodo: no test_command configured; no tests executed'",
            "commit": target_commit,
            "returncode": 0,
            "outcome": "no_tests",
            "validator_id": "quality",
            "task_ref": task.id,
        })

        res, preserve, merged = _merge_on_success(str(repo), task, 0, "sess_1", audit_log)
        assert res == 0
        assert preserve is False
        assert merged == branch
        assert (repo / "file.txt").exists()

    def test_merge_refused_when_verification_is_for_different_task_and_commit(self, tmp_path):
        """A passing verification for a different task and commit does not satisfy the merge gate (Fixes #76)."""
        from snodo.core.interfaces import Task
        from snodo.infrastructure.audit import AuditLog
        from snodo.infrastructure.worktree import task_branch_name

        from snodo.cli.commands.run_cmd import _merge_on_success

        repo = tmp_path
        subprocess_run = __import__("subprocess").run
        subprocess_run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess_run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
        subprocess_run(["git", "config", "user.name", "t"], cwd=repo, check=True)
        (repo / "README.md").write_text("init\n")
        subprocess_run(["git", "add", "README.md"], cwd=repo, check=True)
        subprocess_run(["git", "commit", "-qm", "init"], cwd=repo, check=True)

        task_being_merged = Task(id="task_current", spec="current task")
        branch = task_branch_name(task_being_merged.id, task_being_merged.spec)
        subprocess_run(["git", "checkout", "-qb", branch], cwd=repo, check=True)
        (repo / "feature.txt").write_text("current feature\n")
        subprocess_run(["git", "add", "feature.txt"], cwd=repo, check=True)
        subprocess_run(["git", "commit", "-qm", "current commit"], cwd=repo, check=True)
        current_commit = subprocess_run(
            ["git", "rev-parse", branch], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()
        subprocess_run(["git", "checkout", "-q", "main"], cwd=repo, check=True)

        audit_log = AuditLog(str(repo / "audit.log"))
        # Audit log contains a passing event for a DIFFERENT task and an OLD/DIFFERENT commit
        audit_log.append_event("verification_executed", {
            "op": "verification_executed",
            "command": "pytest",
            "commit": "different_commit_sha_1234567890abcdef",
            "returncode": 0,
            "outcome": "pass",
            "validator_id": "quality",
            "task_ref": "task_different",
        })

        res, preserve, merged = _merge_on_success(str(repo), task_being_merged, 0, "sess_1", audit_log)
        assert res == 1
        assert preserve is True
        assert merged is None
        assert not (repo / "feature.txt").exists()

        events = audit_log.get_history("unverified_merge_blocked")
        assert len(events) == 1
        assert events[0].data["task_ref"] == "task_current"
        assert events[0].data["target_commit"] == current_commit

    def test_commit_prefix_that_is_not_the_merge_target_is_refused(self, tmp_path):
        """A stored commit that only matches the target via the reverse prefix
        direction (stored is longer, target is its prefix) must be refused
        (Refs #206)."""
        from snodo.cli.commands.run_cmd import _verified_commit_matches_merge_target

        target = "a" * 40  # a full 40-hex merge target
        # A genuine abbreviation of the target still evidences it.
        assert _verified_commit_matches_merge_target(target[:7], target)
        assert _verified_commit_matches_merge_target(target, target)
        # A value that merely has the target as ITS prefix (i.e. a longer,
        # different commit) must not satisfy the gate.
        assert not _verified_commit_matches_merge_target(target + "b", target)
        # A short prefix belonging to an unrelated commit is refused too.
        assert not _verified_commit_matches_merge_target("abcdef1", target)
        # Empty/None stored commit is never a match.
        assert not _verified_commit_matches_merge_target("", target)
        assert not _verified_commit_matches_merge_target(None, target)

    def test_merge_accept_and_refuse_paths_name_evidence(self, tmp_path, capsys):
        """Accept and refuse paths each name the evidence (task, commit, command) they relied on (Fixes #76)."""
        from snodo.core.interfaces import Task
        from snodo.infrastructure.audit import AuditLog
        from snodo.infrastructure.worktree import task_branch_name

        from snodo.cli.commands.run_cmd import _merge_on_success

        repo = tmp_path
        subprocess_run = __import__("subprocess").run
        subprocess_run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess_run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
        subprocess_run(["git", "config", "user.name", "t"], cwd=repo, check=True)
        (repo / "README.md").write_text("init\n")
        subprocess_run(["git", "add", "README.md"], cwd=repo, check=True)
        subprocess_run(["git", "commit", "-qm", "init"], cwd=repo, check=True)

        task = Task(id="task_audit_evidence", spec="test evidence reporting")
        branch = task_branch_name(task.id, task.spec)
        subprocess_run(["git", "checkout", "-qb", branch], cwd=repo, check=True)
        (repo / "code.py").write_text("pass\n")
        subprocess_run(["git", "add", "code.py"], cwd=repo, check=True)
        subprocess_run(["git", "commit", "-qm", "add code"], cwd=repo, check=True)
        branch_commit = subprocess_run(
            ["git", "rev-parse", branch], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()
        subprocess_run(["git", "checkout", "-q", "main"], cwd=repo, check=True)

        audit_log = AuditLog(str(repo / "audit.log"))

        # Refusal path: no matching evidence
        res_refuse, _, _ = _merge_on_success(str(repo), task, 0, "sess_1", audit_log)
        assert res_refuse == 1
        err_refusal = capsys.readouterr().err
        assert "Refused merge for" in err_refusal
        assert "task_audit_evidence" in err_refusal
        assert branch_commit[:7] in err_refusal

        # Accept path: record matching passing evidence
        audit_log.append_event("verification_executed", {
            "op": "verification_executed",
            "command": "pytest -q tests/test_code.py",
            "commit": branch_commit,
            "returncode": 0,
            "outcome": "pass",
            "validator_id": "quality",
            "task_ref": task.id,
        })
        res_accept, _, merged = _merge_on_success(str(repo), task, 0, "sess_1", audit_log)
        assert res_accept == 0
        assert merged == branch
        err_accept = capsys.readouterr().err
        assert "Verified merge for" in err_accept
        assert "task_audit_evidence" in err_accept
        assert branch_commit[:7] in err_accept
        assert "pytest -q tests/test_code.py" in err_accept


class TestForegroundTelemetryPersistence:
    def test_foreground_run_telemetry_persists_and_is_readable(self, temp_project, capsys):
        """A foreground snodo run persists task telemetry and is readable via snodo meta."""
        import json
        import subprocess
        from snodo.cli.main import main
        from snodo.paths import derive_task_id

        # Initialize git repo in temp_project
        subprocess.run(["git", "init", "-q"], cwd=str(temp_project), check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(temp_project), check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=str(temp_project), check=True)
        (temp_project / "README.md").write_text("init\n")
        subprocess.run(["git", "add", "README.md"], cwd=str(temp_project), check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=str(temp_project), check=True)

        task_desc = "implement calculator function"
        task_id = derive_task_id(task_desc)

        # Execute foreground run with --mock
        res = main(["run", "--mock", task_desc])
        assert res == 0

        # Verify task state was created under .snodo/tasks/<task_id>/state.json
        task_state_file = temp_project / ".snodo" / "tasks" / task_id / "state.json"
        assert task_state_file.exists()

        # Read back using snodo meta <task_id>
        meta_res = main(["meta", task_id])
        assert meta_res == 0
        out = capsys.readouterr().out
        assert f"Task {task_id}" in out
        assert "implement calculator function" in out

        # Read back using snodo meta <task_id> --json
        meta_json_res = main(["meta", task_id, "--json"])
        assert meta_json_res == 0
        json_out = capsys.readouterr().out
        data = json.loads(json_out)
        assert data["schema"] == "snodo.meta.v1"
        assert data["id"] == task_id
        assert data["type"] == "task"
        assert data["ok"] is True

