"""Tests for the logs command and logs watch functionality.

FILE: tests/cli/test_logs_cmd.py
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

from snodo.cli.commands.logs_cmd import logs_command


def test_logs_watch_drain_loop(capsys):
    """Test that logs --watch runs the drain loop and outputs all remaining lines when status becomes terminal."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        job_dir = Path(tmp_dir) / ".snodo" / "jobs" / "j_test"
        job_dir.mkdir(parents=True)
        log_file = job_dir / "stdout.log"
        # Write some initial lines
        log_file.write_text("line1\nline2\n")

        # Mock the require_project_root to return our tmp_dir
        with patch("snodo.infrastructure.paths.require_project_root", return_value=tmp_dir):
            mock_manager = MagicMock()
            # mock_manager._job_dir returns the correct dir
            mock_manager._job_dir.return_value = job_dir
            
            # To verify the drain loop runs, we append "line3\n" to the file
            # during the mock get_status call and return completed.
            def mock_get_status(job_id):
                log_file.write_text("line1\nline2\nline3\n")
                return {"status": "completed"}
            
            mock_manager.get_status.side_effect = mock_get_status

            with patch("snodo.jobs.JobManager", return_value=mock_manager):
                args = SimpleNamespace(composite_id="j_test", watch=True)
                result = logs_command(args)
                
                assert result == 0
                out = capsys.readouterr().out
                # First pass reads "line1" and "line2".
                # When get_status is called, it writes "line3".
                # The drain loop should read "line3" before exiting.
                assert "line1" in out
                assert "line2" in out
                assert "line3" in out
