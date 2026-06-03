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
