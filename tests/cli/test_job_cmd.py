"""Tests for the job CLI command.

FILE: tests/cli/test_job_cmd.py

Unit tests for snodo/cli/commands/job_cmd.py covering all code paths:
- job_command routing for each action
- ValueError on manager creation
- JobError propagation
- _job_list with empty and non-empty results
- _job_status with all and minimal fields
- _job_logs with and without content
- _job_wait success, JobError, and non-int exit_code
- _job_cancel
- _format_time with valid, None, and invalid values
"""

from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest

from snodo.cli.commands.job_cmd import (
    job_command,
    _job_list,
    _job_status,
    _job_logs,
    _job_wait,
    _job_cancel,
    _format_time,
)
from snodo.jobs import JobError


# === Fixtures ===

@pytest.fixture
def mock_manager():
    """Create a mock JobManager instance."""
    return MagicMock()


# === _format_time Tests ===

class TestFormatTime:
    def test_valid_timestamp(self):
        """Valid numeric timestamp returns formatted string."""
        ts = 1700000000  # 2023-11-14 in UTC
        result = _format_time(ts)
        # Should match YYYY-MM-DD HH:MM:SS format
        assert len(result) == 19
        assert result[4] == "-"
        assert result[7] == "-"
        assert result[10] == " "
        assert result[13] == ":"
        assert result[16] == ":"

    def test_none_returns_na(self):
        """None timestamp returns N/A."""
        assert _format_time(None) == "N/A"

    def test_zero_returns_na(self):
        """Zero (falsy) timestamp returns N/A."""
        assert _format_time(0) == "N/A"

    def test_empty_string_returns_na(self):
        """Empty string (falsy) timestamp returns N/A."""
        assert _format_time("") == "N/A"

    def test_invalid_type_returns_na(self):
        """Non-numeric type returns N/A via exception handler."""
        assert _format_time("not-a-timestamp") == "N/A"

    def test_invalid_object_returns_na(self):
        """Object that cannot be converted returns N/A."""
        assert _format_time(object()) == "N/A"


# === _job_list Tests ===

class TestJobList:
    def test_empty_list(self, mock_manager, capsys):
        """Empty job list prints message and returns 0."""
        mock_manager.list_jobs.return_value = []

        result = _job_list(mock_manager)

        assert result == 0
        out = capsys.readouterr().out
        assert "No jobs found." in out

    def test_list_with_jobs(self, mock_manager, capsys):
        """Job list prints table with job details."""
        mock_manager.list_jobs.return_value = [
            {
                "id": "j_abc123",
                "status": "running",
                "created_at": 1700000000,
                "description": "Short description",
            },
            {
                "id": "j_def456",
                "status": "completed",
                "created_at": 1700001000,
                "description": "Another task",
            },
        ]

        result = _job_list(mock_manager)

        assert result == 0
        out = capsys.readouterr().out
        assert "j_abc123" in out
        assert "running" in out
        assert "Short description" in out
        assert "j_def456" in out
        assert "completed" in out
        assert "Another task" in out
        # Check header
        assert "ID" in out
        assert "Status" in out
        assert "Created" in out
        assert "Description" in out
        assert "-" * 72 in out

    def test_long_description_truncated(self, mock_manager, capsys):
        """Descriptions longer than 40 chars are truncated with ellipsis."""
        long_desc = "A" * 50  # 50 chars, exceeds 40
        mock_manager.list_jobs.return_value = [
            {
                "id": "j_aaa111",
                "status": "running",
                "created_at": 1700000000,
                "description": long_desc,
            },
        ]

        result = _job_list(mock_manager)

        assert result == 0
        out = capsys.readouterr().out
        # Should be truncated to 37 chars + "..."
        assert "A" * 37 + "..." in out
        assert long_desc not in out

    def test_exact_40_char_description_not_truncated(self, mock_manager, capsys):
        """Description with exactly 40 chars is not truncated."""
        desc_40 = "B" * 40
        mock_manager.list_jobs.return_value = [
            {
                "id": "j_bbb222",
                "status": "completed",
                "created_at": 1700000000,
                "description": desc_40,
            },
        ]

        result = _job_list(mock_manager)

        assert result == 0
        out = capsys.readouterr().out
        assert desc_40 in out
        assert "..." not in out


# === _job_status Tests ===

class TestJobStatus:
    def test_full_status(self, mock_manager, capsys):
        """Status with all fields present prints everything."""
        mock_manager.get_status.return_value = {
            "id": "j_full01",
            "status": "completed",
            "pid": 12345,
            "created_at": 1700000000,
            "started_at": 1700000001,
            "completed_at": 1700000010,
            "exit_code": 0,
            "task": {
                "description": "Full task description",
                "protocol": ".snodo/protocol.yml",
                "model": "gpt-4",
                "mock": True,
            },
        }

        result = _job_status(mock_manager, "j_full01")

        assert result == 0
        out = capsys.readouterr().out
        assert "Job: j_full01" in out
        assert "Status: completed" in out
        assert "PID: 12345" in out
        assert "Created:" in out
        assert "Started:" in out
        assert "Completed:" in out
        assert "Exit code: 0" in out
        assert "Description: Full task description" in out
        assert "Protocol: .snodo/protocol.yml" in out
        assert "Model: gpt-4" in out
        assert "Mock: yes" in out

    def test_minimal_status(self, mock_manager, capsys):
        """Status with minimal fields omits optional lines."""
        mock_manager.get_status.return_value = {
            "id": "j_min01",
            "status": "queued",
            "task": {},
        }

        result = _job_status(mock_manager, "j_min01")

        assert result == 0
        out = capsys.readouterr().out
        assert "Job: j_min01" in out
        assert "Status: queued" in out
        assert "PID: N/A" in out
        assert "Created: N/A" in out
        # These should NOT appear
        assert "Started:" not in out
        assert "Completed:" not in out
        assert "Exit code:" not in out
        assert "Description:" not in out
        assert "Protocol:" not in out
        assert "Model:" not in out
        assert "Mock:" not in out

    def test_status_with_started_no_completed(self, mock_manager, capsys):
        """Status with started_at but no completed_at."""
        mock_manager.get_status.return_value = {
            "id": "j_run01",
            "status": "running",
            "pid": 54321,
            "created_at": 1700000000,
            "started_at": 1700000002,
            "task": {},
        }

        result = _job_status(mock_manager, "j_run01")

        assert result == 0
        out = capsys.readouterr().out
        assert "Started:" in out
        assert "Completed:" not in out
        assert "Exit code:" not in out

    def test_status_no_task_key(self, mock_manager, capsys):
        """Status without 'task' key defaults to empty dict."""
        mock_manager.get_status.return_value = {
            "id": "j_notask",
            "status": "unknown",
        }

        result = _job_status(mock_manager, "j_notask")

        assert result == 0
        out = capsys.readouterr().out
        assert "Job: j_notask" in out


# === _job_logs Tests ===

class TestJobLogs:
    def test_logs_with_content(self, mock_manager, capsys):
        """Logs with content prints the content."""
        mock_manager.get_logs.return_value = "line 1\nline 2\n"

        result = _job_logs(mock_manager, "j_log01", "stdout", None)

        assert result == 0
        out = capsys.readouterr().out
        assert "line 1" in out
        assert "line 2" in out

    def test_logs_empty_content(self, mock_manager, capsys):
        """Empty logs prints a no-output message."""
        mock_manager.get_logs.return_value = ""

        result = _job_logs(mock_manager, "j_log02", "stdout", None)

        assert result == 0
        out = capsys.readouterr().out
        assert "(no stdout output)" in out

    def test_logs_none_content(self, mock_manager, capsys):
        """None logs prints a no-output message."""
        mock_manager.get_logs.return_value = None

        result = _job_logs(mock_manager, "j_log03", "stderr", None)

        assert result == 0
        out = capsys.readouterr().out
        assert "(no stderr output)" in out

    def test_logs_passes_stream_and_tail(self, mock_manager, capsys):
        """Stream and tail parameters are passed through to manager."""
        mock_manager.get_logs.return_value = "output"

        _job_logs(mock_manager, "j_log04", "stderr", 50)

        mock_manager.get_logs.assert_called_once_with(
            "j_log04", stream="stderr", tail=50
        )


# === _job_wait Tests ===

class TestJobWait:
    def test_wait_success_exit_0(self, mock_manager, capsys):
        """Successful wait with exit_code 0 returns 0."""
        mock_manager.wait_for.return_value = {
            "status": "completed",
            "exit_code": 0,
        }

        result = _job_wait(mock_manager, "j_wait1", timeout=30)

        assert result == 0
        out = capsys.readouterr().out
        assert "Waiting for job j_wait1..." in out
        assert "Job j_wait1: completed" in out
        assert "Exit code: 0" in out

    def test_wait_success_nonzero_exit(self, mock_manager, capsys):
        """Wait with non-zero exit code returns that code."""
        mock_manager.wait_for.return_value = {
            "status": "failed",
            "exit_code": 2,
        }

        result = _job_wait(mock_manager, "j_wait2", timeout=30)

        assert result == 2
        out = capsys.readouterr().out
        assert "Exit code: 2" in out

    def test_wait_job_error(self, mock_manager, capsys):
        """Wait that raises JobError returns 1."""
        mock_manager.wait_for.side_effect = JobError("Timeout waiting for job")

        result = _job_wait(mock_manager, "j_wait3", timeout=1)

        assert result == 1
        captured = capsys.readouterr()
        assert "Waiting for job j_wait3..." in captured.out
        assert "Error: Timeout waiting for job" in captured.err

    def test_wait_passes_timeout(self, mock_manager, capsys):
        """Timeout parameter is forwarded to manager.wait_for."""
        mock_manager.wait_for.return_value = {"status": "completed", "exit_code": 0}

        _job_wait(mock_manager, "j_wait4", timeout=120)

        mock_manager.wait_for.assert_called_once_with("j_wait4", timeout=120)

    def test_wait_non_int_exit_code(self, mock_manager, capsys):
        """Non-integer exit_code falls back to returning 1."""
        mock_manager.wait_for.return_value = {
            "status": "completed",
            "exit_code": "unknown",
        }

        result = _job_wait(mock_manager, "j_wait5", timeout=30)

        assert result == 1

    def test_wait_none_exit_code(self, mock_manager, capsys):
        """None exit_code (missing key) defaults to 1 via get default."""
        mock_manager.wait_for.return_value = {
            "status": "completed",
            # No exit_code key - get("exit_code", 1) returns 1
        }

        result = _job_wait(mock_manager, "j_wait6", timeout=30)

        assert result == 1
        out = capsys.readouterr().out
        assert "Exit code: 1" in out

    def test_wait_timeout_none(self, mock_manager, capsys):
        """Passing timeout=None is forwarded correctly."""
        mock_manager.wait_for.return_value = {"status": "completed", "exit_code": 0}

        _job_wait(mock_manager, "j_wait7", None)

        mock_manager.wait_for.assert_called_once_with("j_wait7", timeout=None)


# === _job_cancel Tests ===

class TestJobCancel:
    def test_cancel_success(self, mock_manager, capsys):
        """Cancel prints confirmation and returns 0."""
        result = _job_cancel(mock_manager, "j_can01")

        assert result == 0
        mock_manager.cancel.assert_called_once_with("j_can01")
        out = capsys.readouterr().out
        assert "Job j_can01 cancelled." in out


# === job_command Routing Tests ===

class TestJobCommandRouting:
    @patch("snodo.jobs.JobManager")
    def test_route_list(self, MockJobManager, capsys):
        """job_command routes 'list' action correctly."""
        mock_mgr = MagicMock()
        mock_mgr.list_jobs.return_value = []
        MockJobManager.return_value = mock_mgr

        args = SimpleNamespace(job_action="list")
        result = job_command(args)

        assert result == 0
        mock_mgr.list_jobs.assert_called_once()

    @patch("snodo.jobs.JobManager")
    def test_route_status(self, MockJobManager, capsys):
        """job_command routes 'status' action correctly."""
        mock_mgr = MagicMock()
        mock_mgr.get_status.return_value = {
            "id": "j_test01",
            "status": "running",
            "task": {},
        }
        MockJobManager.return_value = mock_mgr

        args = SimpleNamespace(job_action="status", job_id="j_test01")
        result = job_command(args)

        assert result == 0
        mock_mgr.get_status.assert_called_once_with("j_test01")

    @patch("snodo.jobs.JobManager")
    def test_route_logs(self, MockJobManager, capsys):
        """job_command routes 'logs' action correctly."""
        mock_mgr = MagicMock()
        mock_mgr.get_logs.return_value = "some output"
        MockJobManager.return_value = mock_mgr

        args = SimpleNamespace(
            job_action="logs", job_id="j_test02", stream="stderr", tail=10
        )
        result = job_command(args)

        assert result == 0
        mock_mgr.get_logs.assert_called_once_with("j_test02", stream="stderr", tail=10)

    @patch("snodo.jobs.JobManager")
    def test_route_logs_defaults(self, MockJobManager, capsys):
        """job_command uses default stream/tail when not present on args."""
        mock_mgr = MagicMock()
        mock_mgr.get_logs.return_value = ""
        MockJobManager.return_value = mock_mgr

        # SimpleNamespace without stream/tail attributes
        args = SimpleNamespace(job_action="logs", job_id="j_test03")
        result = job_command(args)

        assert result == 0
        mock_mgr.get_logs.assert_called_once_with(
            "j_test03", stream="stdout", tail=None
        )

    @patch("snodo.jobs.JobManager")
    def test_route_wait(self, MockJobManager, capsys):
        """job_command routes 'wait' action correctly."""
        mock_mgr = MagicMock()
        mock_mgr.wait_for.return_value = {"status": "completed", "exit_code": 0}
        MockJobManager.return_value = mock_mgr

        args = SimpleNamespace(job_action="wait", job_id="j_test04", timeout=60)
        result = job_command(args)

        assert result == 0

    @patch("snodo.jobs.JobManager")
    def test_route_wait_default_timeout(self, MockJobManager, capsys):
        """job_command uses default timeout when not present on args."""
        mock_mgr = MagicMock()
        mock_mgr.wait_for.return_value = {"status": "completed", "exit_code": 0}
        MockJobManager.return_value = mock_mgr

        args = SimpleNamespace(job_action="wait", job_id="j_test05")
        result = job_command(args)

        assert result == 0

    @patch("snodo.jobs.JobManager")
    def test_route_cancel(self, MockJobManager, capsys):
        """job_command routes 'cancel' action correctly."""
        mock_mgr = MagicMock()
        MockJobManager.return_value = mock_mgr

        args = SimpleNamespace(job_action="cancel", job_id="j_test06")
        result = job_command(args)

        assert result == 0
        mock_mgr.cancel.assert_called_once_with("j_test06")

    @patch("snodo.jobs.JobManager")
    def test_route_unknown_action(self, MockJobManager, capsys):
        """job_command returns 1 for unknown action."""
        MockJobManager.return_value = MagicMock()

        args = SimpleNamespace(job_action="bogus")
        result = job_command(args)

        assert result == 1
        err = capsys.readouterr().err
        assert "Unknown job action" in err

    @patch("snodo.jobs.JobManager")
    def test_value_error_on_manager_creation(self, MockJobManager, capsys):
        """job_command catches ValueError from JobManager constructor."""
        MockJobManager.side_effect = ValueError("Not a snodo project")

        args = SimpleNamespace(job_action="list")
        result = job_command(args)

        assert result == 1
        err = capsys.readouterr().err
        assert "Error: Not a snodo project" in err

    @patch("snodo.jobs.JobManager")
    def test_job_error_propagation(self, MockJobManager, capsys):
        """job_command catches JobError from action handlers."""
        mock_mgr = MagicMock()
        mock_mgr.get_status.side_effect = JobError("Job not found: j_bad")
        MockJobManager.return_value = mock_mgr

        args = SimpleNamespace(job_action="status", job_id="j_bad")
        result = job_command(args)

        assert result == 1
        err = capsys.readouterr().err
        assert "Error: Job not found: j_bad" in err

    @patch("snodo.jobs.JobManager")
    def test_job_error_from_list(self, MockJobManager, capsys):
        """job_command catches JobError from list action."""
        mock_mgr = MagicMock()
        mock_mgr.list_jobs.side_effect = JobError("Database corrupted")
        MockJobManager.return_value = mock_mgr

        args = SimpleNamespace(job_action="list")
        result = job_command(args)

        assert result == 1
        err = capsys.readouterr().err
        assert "Error: Database corrupted" in err

    @patch("snodo.jobs.JobManager")
    def test_job_error_from_cancel(self, MockJobManager, capsys):
        """job_command catches JobError from cancel action."""
        mock_mgr = MagicMock()
        mock_mgr.cancel.side_effect = JobError("Job already completed")
        MockJobManager.return_value = mock_mgr

        args = SimpleNamespace(job_action="cancel", job_id="j_done")
        result = job_command(args)

        assert result == 1
        err = capsys.readouterr().err
        assert "Error: Job already completed" in err

    @patch("snodo.jobs.JobManager")
    def test_job_error_from_logs(self, MockJobManager, capsys):
        """job_command catches JobError from logs action."""
        mock_mgr = MagicMock()
        mock_mgr.get_logs.side_effect = JobError("Job not found: j_missing")
        MockJobManager.return_value = mock_mgr

        args = SimpleNamespace(job_action="logs", job_id="j_missing")
        result = job_command(args)

        assert result == 1
        err = capsys.readouterr().err
        assert "Error: Job not found: j_missing" in err
