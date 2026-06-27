"""Tests for the TUI dashboard.

FILE: tests/dashboard/test_dashboard.py (Task 5.3)

Tests cover: CLI command, dashboard app structure, panel logic,
and entry point. Uses mocks to avoid requiring a real terminal.
"""

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


# === Fixtures ===

@pytest.fixture
def temp_project():
    """Create a temporary project with .snodo/ directory."""
    temp_dir = tempfile.mkdtemp()
    snodo_dir = Path(temp_dir) / ".snodo"
    snodo_dir.mkdir()

    # Create jobs directory
    (snodo_dir / "jobs").mkdir()

    # Create a minimal protocol
    protocol_file = snodo_dir / "protocol.yml"
    protocol_file.write_text(
        'protocol_id: "test"\n'
        'name: "Test Protocol"\n'
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


@pytest.fixture
def temp_no_snodo():
    """Create a temporary directory without .snodo/."""
    temp_dir = tempfile.mkdtemp()
    original_cwd = Path.cwd()
    try:
        os.chdir(temp_dir)
        yield Path(temp_dir)
    finally:
        os.chdir(original_cwd)


# === CLI Command Tests ===

class TestDashboardCommand:
    """Tests for dashboard_command()."""

    def test_no_snodo_dir_returns_error(self, temp_no_snodo, capsys):
        """Should error if no .snodo/ directory exists."""
        from snodo.cli.commands.dashboard_cmd import dashboard_command

        args = SimpleNamespace()
        result = dashboard_command(args)

        assert result == 1
        captured = capsys.readouterr()
        assert "Not a snodo project" in captured.err

    @patch("snodo.dashboard.app.run_dashboard")
    def test_launches_dashboard(self, mock_run, temp_project):
        """Should launch dashboard when .snodo/ exists."""
        from snodo.cli.commands.dashboard_cmd import dashboard_command

        args = SimpleNamespace()
        result = dashboard_command(args)

        assert result == 0
        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args[1]
        # Resolve symlinks for macOS /var -> /private/var
        assert Path(call_kwargs["project_root"]).resolve() == temp_project.resolve()

    @patch("snodo.dashboard.app.run_dashboard", side_effect=Exception("TUI failed"))
    def test_handles_runtime_error(self, mock_run, temp_project, capsys):
        """Should catch and report runtime errors."""
        from snodo.cli.commands.dashboard_cmd import dashboard_command

        args = SimpleNamespace()
        result = dashboard_command(args)

        assert result == 1
        captured = capsys.readouterr()
        assert "Dashboard failed" in captured.err

    def test_handles_import_error(self, temp_project, capsys):
        """Should report missing textual dependency."""
        from snodo.cli.commands.dashboard_cmd import dashboard_command

        with patch.dict(sys.modules, {"snodo.dashboard.app": None}):
            with patch(
                "snodo.cli.commands.dashboard_cmd.dashboard_command",
                wraps=dashboard_command,
            ):
                # Simulate ImportError by patching the import inside the function
                original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

                def mock_import(name, *args, **kwargs):
                    if name == "snodo.dashboard.app":
                        raise ImportError("No module named 'textual'")
                    return original_import(name, *args, **kwargs)

                with patch("builtins.__import__", side_effect=mock_import):
                    args = SimpleNamespace()
                    result = dashboard_command(args)

                    assert result == 1
                    captured = capsys.readouterr()
                    assert "textual" in captured.err


class TestSnopEntry:
    """Tests for the snop entry point."""

    @patch("snodo.cli.commands.dashboard_cmd.dashboard_command", return_value=0)
    def test_snop_entry_calls_dashboard(self, mock_cmd):
        """snop_entry should call dashboard_command and exit."""
        from snodo.cli.commands.dashboard_cmd import snop_entry

        with pytest.raises(SystemExit) as exc_info:
            snop_entry()
        assert exc_info.value.code == 0
        mock_cmd.assert_called_once()

    @patch("snodo.cli.commands.dashboard_cmd.dashboard_command", return_value=1)
    def test_snop_entry_propagates_exit_code(self, mock_cmd):
        """snop_entry should propagate non-zero exit codes."""
        from snodo.cli.commands.dashboard_cmd import snop_entry

        with pytest.raises(SystemExit) as exc_info:
            snop_entry()
        assert exc_info.value.code == 1


# === CLI Integration Tests ===

class TestDashboardCLI:
    """Tests for the 'snodo dashboard' CLI command."""

    @patch("snodo.dashboard.app.run_dashboard")
    def test_cli_dashboard_command(self, mock_run, temp_project):
        """snodo dashboard should invoke dashboard_command."""
        from snodo.cli.main import main

        result = main(argv=["dashboard"])
        assert result == 0
        mock_run.assert_called_once()


# === App Structure Tests ===

class TestSnodoDashboard:
    """Tests for SnodoDashboard app structure (no terminal required)."""

    def test_app_instantiation(self):
        """Dashboard app should instantiate without errors."""
        from snodo.dashboard.app import SnodoDashboard

        app = SnodoDashboard(project_root="/tmp/test")
        assert app.project_root == "/tmp/test"
        assert app.TITLE.startswith("Snodo Dashboard")

    def test_app_default_project_root(self):
        """Should default to cwd if no project_root given."""
        from snodo.dashboard.app import SnodoDashboard

        app = SnodoDashboard()
        assert app.project_root == str(Path.cwd())

    def test_app_bindings(self):
        """Should have expected key bindings."""
        from snodo.dashboard.app import SnodoDashboard

        app = SnodoDashboard()
        binding_keys = [b.key for b in app.BINDINGS]
        assert "q" in binding_keys



class TestRunDashboard:
    """Tests for run_dashboard entry point."""

    def test_run_dashboard_creates_and_runs_app(self):
        """run_dashboard should create SnodoDashboard and call run()."""
        with patch("snodo.dashboard.app.SnodoDashboard") as MockApp:
            from snodo.dashboard.app import run_dashboard
            run_dashboard("/tmp/test")

        MockApp.assert_called_once_with(project_root="/tmp/test")
        MockApp.return_value.run.assert_called_once()

    def test_run_dashboard_default_project_root(self):
        """run_dashboard should pass None when no project_root given."""
        with patch("snodo.dashboard.app.SnodoDashboard") as MockApp:
            from snodo.dashboard.app import run_dashboard
            run_dashboard()

        MockApp.assert_called_once_with(project_root=None)
        MockApp.return_value.run.assert_called_once()


# === Characterization Tests ===

class TestDashboardDataProvider:
    """Characterization tests for DashboardDataProvider."""

    def test_get_protocol_and_sessions(self, temp_project):
        from snodo.dashboard.providers import DashboardDataProvider
        provider = DashboardDataProvider(str(temp_project))
        
        # Test protocol resolution
        protocol = provider.get_protocol()
        assert protocol is not None
        assert protocol.protocol_id == "test"
        
        # Test empty sessions list initially
        sessions = provider.get_sessions()
        assert isinstance(sessions, list)

    def test_get_session_detail_not_found(self, temp_project):
        from snodo.dashboard.providers import DashboardDataProvider
        provider = DashboardDataProvider(str(temp_project))
        
        detail = provider.get_session_detail("nonexistent_session")
        assert detail is None


class TestSessionsScreen:
    """Characterization tests for SessionsScreen."""

    def test_sessions_screen_instantiation(self, temp_project):
        from snodo.dashboard.providers import DashboardDataProvider
        from snodo.dashboard.screens import SessionsScreen

        provider = DashboardDataProvider(str(temp_project))
        screen = SessionsScreen(provider)
        assert screen.provider == provider


class TestDashboardDataProviderExtended:
    """Extended tests for new DashboardDataProvider data access methods."""

    def test_get_waves_and_tasks(self, temp_project):
        import json
        from snodo.dashboard.providers import DashboardDataProvider
        
        # Write mock wave.json
        wave_path = temp_project / ".snodo" / "wave.json"
        wave_data = [{"wave_id": "w_0001", "feature_description": "Test Wave", "task_ids": ["task_1"]}]
        wave_path.write_text(json.dumps(wave_data))
        
        # Write mock plan & status.json
        plans_dir = temp_project / ".snodo" / "plans" / "main"
        plans_dir.mkdir(parents=True)
        status_file = plans_dir / "status.json"
        status_data = {"tasks": {"task_1": {"status": "completed", "parent_task_ref": None, "depth": 0}}}
        status_file.write_text(json.dumps(status_data))
        
        provider = DashboardDataProvider(str(temp_project))
        
        # Verify get_waves
        waves = provider.get_waves("sess_1")
        assert len(waves) == 1
        assert waves[0]["wave_id"] == "w_0001"
        assert waves[0]["feature_description"] == "Test Wave"
        
        # Verify get_tasks
        tasks = provider.get_tasks("sess_1")
        assert len(tasks) == 1
        assert tasks[0]["task_id"] == "task_1"
        assert tasks[0]["status"] == "completed"

    def test_get_jobs_and_logs(self, temp_project):
        import json
        from snodo.dashboard.providers import DashboardDataProvider
        
        # Write mock job directories
        job_dir = temp_project / ".snodo" / "jobs" / "job_123"
        job_dir.mkdir(parents=True)
        (job_dir / "task.json").write_text(json.dumps({"task_id": "task_1"}))
        (job_dir / "state.json").write_text(json.dumps({
            "status": "completed",
            "created_at": 100.0,
            "started_at": 101.0,
            "completed_at": 105.0,
            "exit_code": 0
        }))
        (job_dir / "stdout.log").write_text("Hello stdout\n")
        (job_dir / "stderr.log").write_text("Hello stderr\n")
        
        provider = DashboardDataProvider(str(temp_project))
        
        # Verify get_jobs
        jobs = provider.get_jobs("sess_1", "main:task_1")
        assert len(jobs) == 1
        assert jobs[0]["job_id"] == "job_123"
        assert jobs[0]["status"] == "completed"
        assert jobs[0]["duration"] == 4.0
        
        # Verify get_job_log
        log = provider.get_job_log("sess_1", "main:task_1", "job_123")
        assert "Hello stdout" in log
        assert "Hello stderr" in log


class TestPanelRegistry:
    """Tests for the dashboard panel registry."""

    def test_registry_discovery_and_retrieval(self, temp_project):
        from snodo.dashboard.providers import DashboardDataProvider
        from snodo.dashboard.panels import get_panel, list_panels
        
        panels = list_panels()
        assert "sessions" in panels
        assert "cockpit" in panels
        
        provider = DashboardDataProvider(str(temp_project))
        
        # Verify retrieving sessions panel
        sess_panel = get_panel("sessions", provider)
        assert sess_panel.__class__.__name__ == "SessionsScreen"
        
        # Verify retrieving cockpit panel
        cockpit_panel = get_panel("cockpit", provider)
        assert cockpit_panel.__class__.__name__ == "CockpitScreen"
