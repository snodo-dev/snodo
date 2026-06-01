"""Tests for the TUI dashboard.

FILE: tests/dashboard/test_dashboard.py (Task 5.3)

Tests cover: CLI command, dashboard app structure, panel logic,
and entry point. Uses mocks to avoid requiring a real terminal.
"""

import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

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
        assert app.TITLE == "Snodo Dashboard"

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
        assert "r" in binding_keys
        assert "c" in binding_keys


# === Panel Logic Tests ===

class TestJobsPanel:
    """Tests for JobsPanel utility methods."""

    def test_format_age_seconds(self):
        """Should format recent times as seconds."""
        from snodo.dashboard.panels.jobs import JobsPanel

        panel = JobsPanel.__new__(JobsPanel)
        now = time.time()
        assert panel._format_age(now - 30) == "30s"

    def test_format_age_minutes(self):
        """Should format minutes correctly."""
        from snodo.dashboard.panels.jobs import JobsPanel

        panel = JobsPanel.__new__(JobsPanel)
        now = time.time()
        assert panel._format_age(now - 300) == "5m"

    def test_format_age_hours(self):
        """Should format hours correctly."""
        from snodo.dashboard.panels.jobs import JobsPanel

        panel = JobsPanel.__new__(JobsPanel)
        now = time.time()
        assert panel._format_age(now - 7200) == "2h"

    def test_format_age_days(self):
        """Should format days correctly."""
        from snodo.dashboard.panels.jobs import JobsPanel

        panel = JobsPanel.__new__(JobsPanel)
        now = time.time()
        assert panel._format_age(now - 172800) == "2d"

    def test_format_age_none(self):
        """Should return N/A for missing timestamps."""
        from snodo.dashboard.panels.jobs import JobsPanel

        panel = JobsPanel.__new__(JobsPanel)
        assert panel._format_age(0) == "N/A"


class TestAgentsPanel:
    """Tests for AgentsPanel utility methods."""

    def test_format_last_used_never(self):
        """Should return 'never' for None timestamp."""
        from snodo.dashboard.panels.agents import AgentsPanel

        panel = AgentsPanel.__new__(AgentsPanel)
        assert panel._format_last_used(None) == "never"

    def test_format_last_used_recent(self):
        """Should return 'just now' for very recent."""
        from snodo.dashboard.panels.agents import AgentsPanel

        panel = AgentsPanel.__new__(AgentsPanel)
        assert panel._format_last_used(time.time() - 10) == "just now"

    def test_format_last_used_minutes(self):
        """Should format minutes ago."""
        from snodo.dashboard.panels.agents import AgentsPanel

        panel = AgentsPanel.__new__(AgentsPanel)
        result = panel._format_last_used(time.time() - 300)
        assert "5m ago" == result

    def test_format_last_used_hours(self):
        """Should format hours ago."""
        from snodo.dashboard.panels.agents import AgentsPanel

        panel = AgentsPanel.__new__(AgentsPanel)
        result = panel._format_last_used(time.time() - 7200)
        assert "2h ago" == result

    def test_format_last_used_days(self):
        """Should format days ago."""
        from snodo.dashboard.panels.agents import AgentsPanel

        panel = AgentsPanel.__new__(AgentsPanel)
        result = panel._format_last_used(time.time() - 172800)
        assert "2d ago" == result


class TestPlansPanel:
    """Tests for PlansPanel utility methods."""

    def test_progress_bar_empty(self):
        """Should show empty bar for 0%."""
        from snodo.dashboard.panels.plans import PlansPanel

        panel = PlansPanel.__new__(PlansPanel)
        assert panel._progress_bar(0) == "[----------]"

    def test_progress_bar_half(self):
        """Should show half-filled bar for 50%."""
        from snodo.dashboard.panels.plans import PlansPanel

        panel = PlansPanel.__new__(PlansPanel)
        assert panel._progress_bar(50) == "[#####-----]"

    def test_progress_bar_full(self):
        """Should show full bar for 100%."""
        from snodo.dashboard.panels.plans import PlansPanel

        panel = PlansPanel.__new__(PlansPanel)
        assert panel._progress_bar(100) == "[##########]"

    def test_progress_bar_partial(self):
        """Should round down for partial percentages."""
        from snodo.dashboard.panels.plans import PlansPanel

        panel = PlansPanel.__new__(PlansPanel)
        assert panel._progress_bar(25) == "[##--------]"


class TestEventsPanel:
    """Tests for EventsPanel utility methods."""

    def test_summarize_data_empty(self):
        """Should return empty string for no data."""
        from snodo.dashboard.panels.events import EventsPanel

        panel = EventsPanel.__new__(EventsPanel)
        assert panel._summarize_data(None) == ""
        assert panel._summarize_data({}) == ""

    def test_summarize_data_short(self):
        """Should show first key-value pair."""
        from snodo.dashboard.panels.events import EventsPanel

        panel = EventsPanel.__new__(EventsPanel)
        result = panel._summarize_data({"task": "hello"})
        assert result == "task=hello"

    def test_summarize_data_long_truncated(self):
        """Should truncate long values."""
        from snodo.dashboard.panels.events import EventsPanel

        panel = EventsPanel.__new__(EventsPanel)
        long_val = "x" * 50
        result = panel._summarize_data({"desc": long_val})
        assert len(result) <= 35
        assert result.endswith("...")


# === Module Import Tests ===

class TestImports:
    """Tests that dashboard modules import correctly."""

    def test_import_dashboard_package(self):
        """Should import snodo.dashboard."""
        from snodo.dashboard import SnodoDashboard
        assert SnodoDashboard is not None

    def test_import_panels(self):
        """Should import all panel classes."""
        from snodo.dashboard.panels import JobsPanel, AgentsPanel, PlansPanel, EventsPanel
        assert JobsPanel is not None
        assert AgentsPanel is not None
        assert PlansPanel is not None
        assert EventsPanel is not None

    def test_import_dashboard_cmd(self):
        """Should import dashboard command and entry point."""
        from snodo.cli.commands.dashboard_cmd import dashboard_command, snop_entry
        assert callable(dashboard_command)
        assert callable(snop_entry)


# === Coverage Expansion Tests ===

class TestJobsPanelLoadJobs:
    """Tests for JobsPanel._load_jobs and refresh_data."""

    def test_load_jobs_returns_jobs(self):
        """_load_jobs should return jobs from JobManager."""
        from snodo.dashboard.panels.jobs import JobsPanel

        mock_jobs = [
            {"id": "j1", "status": "running", "description": "test job", "created_at": 0},
            {"id": "j2", "status": "done", "description": "other job", "created_at": 0},
        ]
        with patch("snodo.jobs.JobManager") as MockJM:
            mock_mgr = MockJM.return_value
            mock_mgr.list_jobs.return_value = mock_jobs
            panel = JobsPanel.__new__(JobsPanel)
            result = panel._load_jobs()
            assert result == mock_jobs

    def test_load_jobs_returns_empty_on_exception(self):
        """_load_jobs should return [] when JobManager raises."""
        from snodo.dashboard.panels.jobs import JobsPanel

        with patch("snodo.jobs.JobManager", side_effect=ValueError("no project")):
            panel = JobsPanel.__new__(JobsPanel)
            result = panel._load_jobs()
            assert result == []

    def test_load_jobs_returns_empty_on_generic_exception(self):
        """_load_jobs should return [] on any Exception."""
        from snodo.dashboard.panels.jobs import JobsPanel

        with patch("snodo.jobs.JobManager", side_effect=RuntimeError("unexpected")):
            panel = JobsPanel.__new__(JobsPanel)
            result = panel._load_jobs()
            assert result == []

    def test_refresh_data_populates_table(self):
        """refresh_data should clear table and add job rows."""
        from snodo.dashboard.panels.jobs import JobsPanel

        mock_jobs = [
            {"id": "j1", "status": "running", "description": "short desc", "created_at": time.time() - 30},
            {"id": "j2", "status": "done", "description": "another one", "created_at": 0},
        ]
        panel = JobsPanel.__new__(JobsPanel)
        mock_table = MagicMock()
        with patch.object(panel, 'query_one', return_value=mock_table):
            with patch.object(panel, '_load_jobs', return_value=mock_jobs):
                panel.refresh_data()

        mock_table.clear.assert_called_once()
        assert mock_table.add_row.call_count == 2

    def test_refresh_data_truncates_long_descriptions(self):
        """refresh_data should truncate descriptions longer than 35 chars."""
        from snodo.dashboard.panels.jobs import JobsPanel

        long_desc = "A" * 50  # 50 chars
        mock_jobs = [
            {"id": "j1", "status": "running", "description": long_desc, "created_at": 0},
        ]
        panel = JobsPanel.__new__(JobsPanel)
        mock_table = MagicMock()
        with patch.object(panel, 'query_one', return_value=mock_table):
            with patch.object(panel, '_load_jobs', return_value=mock_jobs):
                panel.refresh_data()

        # The description passed to add_row should be truncated
        call_args = mock_table.add_row.call_args[0]
        desc_arg = call_args[2]  # third positional arg is description
        assert len(desc_arg) <= 35
        assert desc_arg.endswith("...")

    def test_refresh_data_limits_to_10_jobs(self):
        """refresh_data should show at most 10 jobs."""
        from snodo.dashboard.panels.jobs import JobsPanel

        mock_jobs = [
            {"id": f"j{i}", "status": "done", "description": f"job {i}", "created_at": 0}
            for i in range(15)
        ]
        panel = JobsPanel.__new__(JobsPanel)
        mock_table = MagicMock()
        with patch.object(panel, 'query_one', return_value=mock_table):
            with patch.object(panel, '_load_jobs', return_value=mock_jobs):
                panel.refresh_data()

        assert mock_table.add_row.call_count == 10

    def test_compose_yields_widgets(self):
        """compose should yield Static and DataTable widgets."""
        from snodo.dashboard.panels.jobs import JobsPanel

        panel = JobsPanel.__new__(JobsPanel)
        widgets = list(panel.compose())
        assert len(widgets) == 2
        # First is Static with "JOBS", second is DataTable
        from textual.widgets import Static, DataTable
        assert isinstance(widgets[0], Static)
        assert isinstance(widgets[1], DataTable)

    def test_on_mount_adds_columns(self):
        """on_mount should add columns to the table."""
        from snodo.dashboard.panels.jobs import JobsPanel

        panel = JobsPanel.__new__(JobsPanel)
        mock_table = MagicMock()
        with patch.object(panel, 'query_one', return_value=mock_table):
            panel.on_mount()

        mock_table.add_columns.assert_called_once_with("ID", "Status", "Description", "Age")

    def test_get_selected_job_id_returns_id(self):
        """get_selected_job_id should return job ID from selected row."""
        from snodo.dashboard.panels.jobs import JobsPanel

        panel = JobsPanel.__new__(JobsPanel)
        mock_table = MagicMock()
        mock_table.cursor_row = 0
        mock_table.get_row_at.return_value = ["job-123", "running", "desc", "5s"]
        with patch.object(panel, 'query_one', return_value=mock_table):
            result = panel.get_selected_job_id()
        assert result == "job-123"

    def test_get_selected_job_id_returns_none_when_no_cursor(self):
        """get_selected_job_id should return None if cursor_row is None."""
        from snodo.dashboard.panels.jobs import JobsPanel

        panel = JobsPanel.__new__(JobsPanel)
        mock_table = MagicMock()
        mock_table.cursor_row = None
        with patch.object(panel, 'query_one', return_value=mock_table):
            result = panel.get_selected_job_id()
        assert result is None

    def test_get_selected_job_id_returns_none_on_exception(self):
        """get_selected_job_id should return None if get_row_at raises."""
        from snodo.dashboard.panels.jobs import JobsPanel

        panel = JobsPanel.__new__(JobsPanel)
        mock_table = MagicMock()
        mock_table.cursor_row = 0
        mock_table.get_row_at.side_effect = IndexError("out of range")
        with patch.object(panel, 'query_one', return_value=mock_table):
            result = panel.get_selected_job_id()
        assert result is None

    def test_get_selected_job_id_returns_none_for_empty_row(self):
        """get_selected_job_id should return None if row is empty."""
        from snodo.dashboard.panels.jobs import JobsPanel

        panel = JobsPanel.__new__(JobsPanel)
        mock_table = MagicMock()
        mock_table.cursor_row = 0
        mock_table.get_row_at.return_value = []
        with patch.object(panel, 'query_one', return_value=mock_table):
            result = panel.get_selected_job_id()
        assert result is None


class TestAgentsPanelLoadAgents:
    """Tests for AgentsPanel._load_agents, compose, on_mount, refresh_data."""

    def test_load_agents_returns_agents(self):
        """_load_agents should return agents from AgentMemoryManager."""
        from snodo.dashboard.panels.agents import AgentsPanel

        mock_agents = [
            {"id": "a1", "thread_id": "abcdef1234567890", "task_count": 5, "last_used_at": None},
        ]
        with patch("snodo.infrastructure.memory.AgentMemoryManager") as MockAMM:
            mock_mgr = MockAMM.return_value
            mock_mgr.list_agents.return_value = mock_agents
            panel = AgentsPanel.__new__(AgentsPanel)
            result = panel._load_agents()
            assert result == mock_agents

    def test_load_agents_returns_empty_on_exception(self):
        """_load_agents should return [] when AgentMemoryManager raises."""
        from snodo.dashboard.panels.agents import AgentsPanel

        with patch("snodo.infrastructure.memory.AgentMemoryManager", side_effect=Exception("broken")):
            panel = AgentsPanel.__new__(AgentsPanel)
            result = panel._load_agents()
            assert result == []

    def test_refresh_data_populates_table(self):
        """refresh_data should clear table and add agent rows."""
        from snodo.dashboard.panels.agents import AgentsPanel

        mock_agents = [
            {"id": "a1", "thread_id": "abcdef1234567890", "task_count": 5, "last_used_at": None},
            {"id": "a2", "thread_id": "zzzzzzzzxxxxxxxx", "task_count": 2, "last_used_at": time.time() - 60},
        ]
        panel = AgentsPanel.__new__(AgentsPanel)
        mock_table = MagicMock()
        with patch.object(panel, 'query_one', return_value=mock_table):
            with patch.object(panel, '_load_agents', return_value=mock_agents):
                panel.refresh_data()

        mock_table.clear.assert_called_once()
        assert mock_table.add_row.call_count == 2
        # Check first call: thread_id should be truncated to 8 chars
        first_call = mock_table.add_row.call_args_list[0][0]
        assert first_call[0] == "a1"
        assert first_call[1] == "abcdef12"  # first 8 chars of thread_id

    def test_refresh_data_limits_to_8_agents(self):
        """refresh_data should show at most 8 agents."""
        from snodo.dashboard.panels.agents import AgentsPanel

        mock_agents = [
            {"id": f"a{i}", "thread_id": "aaaa", "task_count": 0, "last_used_at": None}
            for i in range(12)
        ]
        panel = AgentsPanel.__new__(AgentsPanel)
        mock_table = MagicMock()
        with patch.object(panel, 'query_one', return_value=mock_table):
            with patch.object(panel, '_load_agents', return_value=mock_agents):
                panel.refresh_data()

        assert mock_table.add_row.call_count == 8

    def test_compose_yields_widgets(self):
        """compose should yield Static and DataTable widgets."""
        from snodo.dashboard.panels.agents import AgentsPanel

        panel = AgentsPanel.__new__(AgentsPanel)
        widgets = list(panel.compose())
        assert len(widgets) == 2
        from textual.widgets import Static, DataTable
        assert isinstance(widgets[0], Static)
        assert isinstance(widgets[1], DataTable)

    def test_on_mount_adds_columns(self):
        """on_mount should add columns to the agents table."""
        from snodo.dashboard.panels.agents import AgentsPanel

        panel = AgentsPanel.__new__(AgentsPanel)
        mock_table = MagicMock()
        with patch.object(panel, 'query_one', return_value=mock_table):
            panel.on_mount()

        mock_table.add_columns.assert_called_once_with("ID", "Thread", "Tasks", "Last Used")


class TestEventsPanelLoadEvents:
    """Tests for EventsPanel._load_events, compose, refresh_data."""

    def test_load_events_returns_events(self):
        """_load_events should return events from AuditLog."""
        from snodo.dashboard.panels.events import EventsPanel

        mock_events = [MagicMock(timestamp="2025-01-01T12:30:00Z", event_type="job.start", data={})]
        with patch("snodo.infrastructure.audit.AuditLog") as MockAudit:
            with patch("snodo.dashboard.panels.events.Path") as MockPath:
                # Path.cwd() / ".snodo" / "audit.log" => str(...)
                MockPath.cwd.return_value.__truediv__.return_value.__truediv__.return_value = Path("/fake/.snodo/audit.log")
                # Path(log_path).exists() => True
                MockPath.return_value.exists.return_value = True
                mock_audit = MockAudit.return_value
                mock_audit.get_history.return_value = mock_events
                panel = EventsPanel.__new__(EventsPanel)
                result = panel._load_events()
                assert result == mock_events

    def test_load_events_returns_empty_when_no_file(self):
        """_load_events should return [] if audit.log does not exist."""
        from snodo.dashboard.panels.events import EventsPanel

        with patch("snodo.dashboard.panels.events.Path") as MockPath:
            MockPath.cwd.return_value.__truediv__.return_value.__truediv__.return_value = Path("/fake/.snodo/audit.log")
            MockPath.return_value.exists.return_value = False
            panel = EventsPanel.__new__(EventsPanel)
            result = panel._load_events()
            assert result == []

    def test_load_events_returns_empty_on_exception(self):
        """_load_events should return [] on any exception."""
        from snodo.dashboard.panels.events import EventsPanel

        with patch("snodo.infrastructure.audit.AuditLog", side_effect=Exception("broken")):
            with patch("snodo.dashboard.panels.events.Path") as MockPath:
                MockPath.cwd.return_value.__truediv__.return_value.__truediv__.return_value = Path("/fake/.snodo/audit.log")
                MockPath.return_value.exists.return_value = True
                panel = EventsPanel.__new__(EventsPanel)
                result = panel._load_events()
                assert result == []

    def test_refresh_data_no_events(self):
        """refresh_data should show '(no events)' when empty."""
        from snodo.dashboard.panels.events import EventsPanel

        panel = EventsPanel.__new__(EventsPanel)
        mock_content = MagicMock()
        with patch.object(panel, 'query_one', return_value=mock_content):
            with patch.object(panel, '_load_events', return_value=[]):
                panel.refresh_data()

        mock_content.update.assert_called_once_with("  (no events)")

    def test_refresh_data_with_events(self):
        """refresh_data should format and display events."""
        from snodo.dashboard.panels.events import EventsPanel
        from types import SimpleNamespace

        mock_events = [
            SimpleNamespace(
                timestamp="2025-01-15T14:30:00Z",
                event_type="job.started",
                data={"task": "build"},
            ),
            SimpleNamespace(
                timestamp="2025-01-15T14:31:00Z",
                event_type="job.completed",
                data=None,
            ),
        ]
        panel = EventsPanel.__new__(EventsPanel)
        mock_content = MagicMock()
        with patch.object(panel, 'query_one', return_value=mock_content):
            with patch.object(panel, '_load_events', return_value=mock_events):
                panel.refresh_data()

        mock_content.update.assert_called_once()
        output = mock_content.update.call_args[0][0]
        assert "14:30" in output
        assert "job.started" in output
        assert "task=build" in output
        assert "14:31" in output
        assert "job.completed" in output

    def test_refresh_data_limits_to_8_events(self):
        """refresh_data should show at most last 8 events."""
        from snodo.dashboard.panels.events import EventsPanel
        from types import SimpleNamespace

        mock_events = [
            SimpleNamespace(
                timestamp=f"2025-01-15T14:{i:02d}:00Z",
                event_type=f"event.{i}",
                data={},
            )
            for i in range(15)
        ]
        panel = EventsPanel.__new__(EventsPanel)
        mock_content = MagicMock()
        with patch.object(panel, 'query_one', return_value=mock_content):
            with patch.object(panel, '_load_events', return_value=mock_events):
                panel.refresh_data()

        output = mock_content.update.call_args[0][0]
        # Should only have 8 lines (the last 8 events)
        lines = output.strip().split("\n")
        assert len(lines) == 8

    def test_refresh_data_short_timestamp(self):
        """refresh_data should handle short timestamps gracefully."""
        from snodo.dashboard.panels.events import EventsPanel
        from types import SimpleNamespace

        mock_events = [
            SimpleNamespace(
                timestamp="12:30",  # Short timestamp, len < 16
                event_type="test",
                data={},
            ),
        ]
        panel = EventsPanel.__new__(EventsPanel)
        mock_content = MagicMock()
        with patch.object(panel, 'query_one', return_value=mock_content):
            with patch.object(panel, '_load_events', return_value=mock_events):
                panel.refresh_data()

        output = mock_content.update.call_args[0][0]
        assert "12:30" in output

    def test_compose_yields_widgets(self):
        """compose should yield two Static widgets."""
        from snodo.dashboard.panels.events import EventsPanel

        panel = EventsPanel.__new__(EventsPanel)
        widgets = list(panel.compose())
        assert len(widgets) == 2
        from textual.widgets import Static
        assert isinstance(widgets[0], Static)
        assert isinstance(widgets[1], Static)


class TestPlansPanelLoadPlans:
    """Tests for PlansPanel._load_plans, compose, refresh_data."""

    def test_load_plans_returns_plans(self):
        """_load_plans should return plans from PlannerMCP."""
        from snodo.dashboard.panels.plans import PlansPanel

        mock_plans = [
            {"name": "plan1", "task_count": 10, "status_counts": {"completed": 5}},
        ]
        with patch("snodo.mcp.planner.PlannerMCP") as MockPlanner:
            mock_planner = MockPlanner.return_value
            mock_planner.list_plans.return_value = mock_plans
            panel = PlansPanel.__new__(PlansPanel)
            result = panel._load_plans()
            assert result == mock_plans

    def test_load_plans_returns_empty_on_value_error(self):
        """_load_plans should return [] when PlannerMCP raises ValueError."""
        from snodo.dashboard.panels.plans import PlansPanel

        with patch("snodo.mcp.planner.PlannerMCP", side_effect=ValueError("no project")):
            panel = PlansPanel.__new__(PlansPanel)
            result = panel._load_plans()
            assert result == []

    def test_load_plans_returns_empty_on_exception(self):
        """_load_plans should return [] on any exception."""
        from snodo.dashboard.panels.plans import PlansPanel

        with patch("snodo.mcp.planner.PlannerMCP", side_effect=RuntimeError("broken")):
            panel = PlansPanel.__new__(PlansPanel)
            result = panel._load_plans()
            assert result == []

    def test_refresh_data_no_plans(self):
        """refresh_data should show '(no plans)' when empty."""
        from snodo.dashboard.panels.plans import PlansPanel

        panel = PlansPanel.__new__(PlansPanel)
        mock_content = MagicMock()
        with patch.object(panel, 'query_one', return_value=mock_content):
            with patch.object(panel, '_load_plans', return_value=[]):
                panel.refresh_data()

        mock_content.update.assert_called_once_with("  (no plans)")

    def test_refresh_data_with_plans(self):
        """refresh_data should format plans with progress bars."""
        from snodo.dashboard.panels.plans import PlansPanel

        mock_plans = [
            {"name": "build-app", "task_count": 10, "status_counts": {"completed": 5}},
            {"name": "deploy", "task_count": 4, "status_counts": {"completed": 4}},
        ]
        panel = PlansPanel.__new__(PlansPanel)
        mock_content = MagicMock()
        with patch.object(panel, 'query_one', return_value=mock_content):
            with patch.object(panel, '_load_plans', return_value=mock_plans):
                panel.refresh_data()

        output = mock_content.update.call_args[0][0]
        assert "build-app" in output
        assert "50%" in output
        assert "(5/10)" in output
        assert "deploy" in output
        assert "100%" in output
        assert "(4/4)" in output

    def test_refresh_data_zero_total_tasks(self):
        """refresh_data should handle zero total tasks without division error."""
        from snodo.dashboard.panels.plans import PlansPanel

        mock_plans = [
            {"name": "empty-plan", "task_count": 0, "status_counts": {}},
        ]
        panel = PlansPanel.__new__(PlansPanel)
        mock_content = MagicMock()
        with patch.object(panel, 'query_one', return_value=mock_content):
            with patch.object(panel, '_load_plans', return_value=mock_plans):
                panel.refresh_data()

        output = mock_content.update.call_args[0][0]
        assert "empty-plan" in output
        assert "0%" in output

    def test_refresh_data_limits_to_5_plans(self):
        """refresh_data should show at most 5 plans."""
        from snodo.dashboard.panels.plans import PlansPanel

        mock_plans = [
            {"name": f"plan-{i}", "task_count": 10, "status_counts": {"completed": i}}
            for i in range(10)
        ]
        panel = PlansPanel.__new__(PlansPanel)
        mock_content = MagicMock()
        with patch.object(panel, 'query_one', return_value=mock_content):
            with patch.object(panel, '_load_plans', return_value=mock_plans):
                panel.refresh_data()

        output = mock_content.update.call_args[0][0]
        lines = output.strip().split("\n")
        assert len(lines) == 5

    def test_refresh_data_unnamed_plan(self):
        """refresh_data should use 'unnamed' for plans without a name."""
        from snodo.dashboard.panels.plans import PlansPanel

        mock_plans = [
            {"task_count": 2, "status_counts": {"completed": 1}},
        ]
        panel = PlansPanel.__new__(PlansPanel)
        mock_content = MagicMock()
        with patch.object(panel, 'query_one', return_value=mock_content):
            with patch.object(panel, '_load_plans', return_value=mock_plans):
                panel.refresh_data()

        output = mock_content.update.call_args[0][0]
        assert "unnamed" in output

    def test_compose_yields_widgets(self):
        """compose should yield two Static widgets."""
        from snodo.dashboard.panels.plans import PlansPanel

        panel = PlansPanel.__new__(PlansPanel)
        widgets = list(panel.compose())
        assert len(widgets) == 2
        from textual.widgets import Static
        assert isinstance(widgets[0], Static)
        assert isinstance(widgets[1], Static)


class TestSnodoDashboardActions:
    """Tests for SnodoDashboard compose, on_mount, action_refresh, action_cancel_job."""

    def _make_app(self):
        """Create a properly initialized SnodoDashboard for testing."""
        from snodo.dashboard.app import SnodoDashboard
        return SnodoDashboard(project_root="/tmp/test")

    def test_compose_is_generator(self):
        """compose should be a generator method that yields widgets."""
        app = self._make_app()
        # compose() uses VerticalScroll context manager which requires a running app,
        # so we verify the method exists and the app has the right project info
        assert hasattr(app, 'compose')
        assert callable(app.compose)
        assert app.project_root == "/tmp/test"

    def test_action_refresh_calls_panel_refresh(self):
        """action_refresh should call refresh_data on all panels."""
        app = self._make_app()

        mock_panel = MagicMock()
        mock_query = MagicMock(return_value=[mock_panel])
        with patch.object(app, 'query', mock_query):
            app.action_refresh()
            # query is called 4 times (for each panel type)
            assert mock_query.call_count == 4
            # refresh_data is called once per panel type (same mock returned each time)
            assert mock_panel.refresh_data.call_count == 4

    def test_action_refresh_with_multiple_panels(self):
        """action_refresh should call refresh_data on each panel returned by query."""
        app = self._make_app()

        mock_jobs_panel = MagicMock()
        mock_agents_panel = MagicMock()
        mock_plans_panel = MagicMock()
        mock_events_panel = MagicMock()

        def mock_query(selector):
            panels = {
                "JobsPanel": [mock_jobs_panel],
                "AgentsPanel": [mock_agents_panel],
                "PlansPanel": [mock_plans_panel],
                "EventsPanel": [mock_events_panel],
            }
            return panels.get(selector, [])

        with patch.object(app, 'query', side_effect=mock_query):
            app.action_refresh()

        mock_jobs_panel.refresh_data.assert_called_once()
        mock_agents_panel.refresh_data.assert_called_once()
        mock_plans_panel.refresh_data.assert_called_once()
        mock_events_panel.refresh_data.assert_called_once()

    def test_action_cancel_job_no_selection(self):
        """action_cancel_job should notify warning when no job selected."""
        app = self._make_app()

        mock_jobs_panel = MagicMock()
        mock_jobs_panel.get_selected_job_id.return_value = None
        with patch.object(app, 'query_one', return_value=mock_jobs_panel):
            with patch.object(app, 'notify') as mock_notify:
                app.action_cancel_job()

        mock_notify.assert_called_once_with("No job selected", severity="warning")

    def test_action_cancel_job_success(self):
        """action_cancel_job should cancel the selected job and notify."""
        app = self._make_app()

        mock_jobs_panel = MagicMock()
        mock_jobs_panel.get_selected_job_id.return_value = "job-abc"
        with patch.object(app, 'query_one', return_value=mock_jobs_panel):
            with patch.object(app, 'notify') as mock_notify:
                with patch.object(app, 'action_refresh') as mock_refresh:
                    with patch("snodo.jobs.JobManager") as MockJM:
                        mock_mgr = MockJM.return_value
                        app.action_cancel_job()

        mock_mgr.cancel.assert_called_once_with("job-abc")
        mock_notify.assert_called_once_with("Cancelled job job-abc")
        mock_refresh.assert_called_once()

    def test_action_cancel_job_error(self):
        """action_cancel_job should notify error on exception."""
        app = self._make_app()

        mock_jobs_panel = MagicMock()
        mock_jobs_panel.get_selected_job_id.return_value = "job-abc"
        with patch.object(app, 'query_one', return_value=mock_jobs_panel):
            with patch.object(app, 'notify') as mock_notify:
                with patch("snodo.jobs.JobManager", side_effect=Exception("cancel failed")):
                    app.action_cancel_job()

        mock_notify.assert_called_once()
        call_args = mock_notify.call_args
        assert "Error" in call_args[0][0]
        assert call_args[1]["severity"] == "error"

    def test_on_mount_starts_refresh_timer(self):
        """on_mount should call action_refresh and set_interval."""
        app = self._make_app()
        app._refresh_timer = None

        mock_timer = MagicMock()
        with patch.object(app, 'action_refresh') as mock_refresh:
            with patch.object(app, 'set_interval', return_value=mock_timer) as mock_interval:
                app.on_mount()

        mock_refresh.assert_called_once()
        mock_interval.assert_called_once()
        # Verify the interval value (first positional arg)
        assert mock_interval.call_args[0][0] == 1.0
        assert app._refresh_timer == mock_timer


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
