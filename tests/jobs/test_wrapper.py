"""Tests for the job wrapper subprocess module.

FILE: tests/jobs/test_wrapper.py

Unit tests for _save_state, _load_state, and main() in snodo/jobs/wrapper.py.
"""

import json
import os
import sys
from unittest.mock import patch

import pytest

from snodo.jobs.wrapper import _save_state, _load_state, main


# === _save_state / _load_state Tests ===

class TestSaveState:
    def test_save_creates_state_json(self, tmp_path):
        """_save_state writes state.json to job_dir."""
        state = {"status": "queued", "pid": None}
        _save_state(str(tmp_path), state)

        state_path = tmp_path / "state.json"
        assert state_path.exists()

    def test_save_state_content_is_valid_json(self, tmp_path):
        """_save_state writes valid JSON with expected keys."""
        state = {"status": "running", "pid": 12345, "started_at": 1700000000.0}
        _save_state(str(tmp_path), state)

        with open(tmp_path / "state.json") as f:
            loaded = json.load(f)

        assert loaded["status"] == "running"
        assert loaded["pid"] == 12345
        assert loaded["started_at"] == 1700000000.0

    def test_save_state_atomic_no_tmp_left(self, tmp_path):
        """_save_state uses atomic rename, leaving no .tmp file."""
        _save_state(str(tmp_path), {"status": "queued"})

        assert not (tmp_path / "state.json.tmp").exists()
        assert (tmp_path / "state.json").exists()

    def test_save_state_overwrites_existing(self, tmp_path):
        """_save_state overwrites a previously saved state."""
        _save_state(str(tmp_path), {"status": "queued"})
        _save_state(str(tmp_path), {"status": "running"})

        with open(tmp_path / "state.json") as f:
            loaded = json.load(f)

        assert loaded["status"] == "running"


class TestLoadState:
    def test_load_state_round_trip(self, tmp_path):
        """_load_state returns exactly what _save_state wrote."""
        original = {"status": "completed", "exit_code": 0, "pid": 999}
        _save_state(str(tmp_path), original)
        loaded = _load_state(str(tmp_path))

        assert loaded == original

    def test_load_state_missing_file_raises(self, tmp_path):
        """_load_state raises FileNotFoundError for missing state.json."""
        with pytest.raises(FileNotFoundError):
            _load_state(str(tmp_path))


# === main() Tests ===

class TestMain:
    def _prepare_job_dir(self, tmp_path, initial_state=None):
        """Helper: create a job_dir with state.json and return its path."""
        if initial_state is None:
            initial_state = {"status": "queued", "pid": None}
        _save_state(str(tmp_path), initial_state)
        return str(tmp_path)

    def test_too_few_args_exits_with_code_2(self, capsys):
        """main() prints usage and exits 2 when fewer than 3 args."""
        with patch.object(sys, "argv", ["wrapper"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 2

        captured = capsys.readouterr()
        assert "Usage" in captured.err

    def test_too_few_args_just_job_dir(self, capsys):
        """main() prints usage and exits 2 when only job_dir given."""
        with patch.object(sys, "argv", ["wrapper", "/some/dir"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 2

    @patch("snodo.jobs.wrapper.time")
    def test_successful_cli_run(self, mock_time, tmp_path):
        """main() marks status=completed and exit_code=0 on success."""
        mock_time.time.return_value = 1700000000.0
        job_dir = self._prepare_job_dir(tmp_path)

        with patch.object(sys, "argv", ["wrapper", job_dir, "run", "task"]):
            with patch("snodo.cli.main.main", return_value=0) as mock_cli:
                with pytest.raises(SystemExit) as exc_info:
                    main()

        assert exc_info.value.code == 0
        mock_cli.assert_called_once_with(argv=["run", "task"])

        state = _load_state(job_dir)
        assert state["status"] == "completed"
        assert state["exit_code"] == 0
        assert state["completed_at"] == 1700000000.0

    @patch("snodo.jobs.wrapper.time")
    def test_cli_returns_nonzero(self, mock_time, tmp_path):
        """main() marks status=failed when CLI returns non-zero int."""
        mock_time.time.return_value = 1700000000.0
        job_dir = self._prepare_job_dir(tmp_path)

        with patch.object(sys, "argv", ["wrapper", job_dir, "run", "task"]):
            with patch("snodo.cli.main.main", return_value=1):
                with pytest.raises(SystemExit) as exc_info:
                    main()

        assert exc_info.value.code == 1

        state = _load_state(job_dir)
        assert state["status"] == "failed"
        assert state["exit_code"] == 1

    @patch("snodo.jobs.wrapper.time")
    def test_cli_returns_non_int(self, mock_time, tmp_path):
        """main() treats non-int return as exit_code=0 (success)."""
        mock_time.time.return_value = 1700000000.0
        job_dir = self._prepare_job_dir(tmp_path)

        with patch.object(sys, "argv", ["wrapper", job_dir, "run", "task"]):
            with patch("snodo.cli.main.main", return_value=None):
                with pytest.raises(SystemExit) as exc_info:
                    main()

        assert exc_info.value.code == 0

        state = _load_state(job_dir)
        assert state["status"] == "completed"
        assert state["exit_code"] == 0

    @patch("snodo.jobs.wrapper.time")
    def test_system_exit_with_int_code(self, mock_time, tmp_path):
        """main() captures SystemExit and uses its int code."""
        mock_time.time.return_value = 1700000000.0
        job_dir = self._prepare_job_dir(tmp_path)

        with patch.object(sys, "argv", ["wrapper", job_dir, "run", "task"]):
            with patch("snodo.cli.main.main", side_effect=SystemExit(3)):
                with pytest.raises(SystemExit) as exc_info:
                    main()

        assert exc_info.value.code == 3

        state = _load_state(job_dir)
        assert state["status"] == "failed"
        assert state["exit_code"] == 3

    @patch("snodo.jobs.wrapper.time")
    def test_system_exit_with_zero_code(self, mock_time, tmp_path):
        """main() treats SystemExit(0) as success."""
        mock_time.time.return_value = 1700000000.0
        job_dir = self._prepare_job_dir(tmp_path)

        with patch.object(sys, "argv", ["wrapper", job_dir, "run", "task"]):
            with patch("snodo.cli.main.main", side_effect=SystemExit(0)):
                with pytest.raises(SystemExit) as exc_info:
                    main()

        assert exc_info.value.code == 0

        state = _load_state(job_dir)
        assert state["status"] == "completed"
        assert state["exit_code"] == 0

    @patch("snodo.jobs.wrapper.time")
    def test_system_exit_with_non_int_code(self, mock_time, tmp_path):
        """main() treats SystemExit with non-int code as exit_code=1."""
        mock_time.time.return_value = 1700000000.0
        job_dir = self._prepare_job_dir(tmp_path)

        with patch.object(sys, "argv", ["wrapper", job_dir, "run", "task"]):
            with patch("snodo.cli.main.main", side_effect=SystemExit("error message")):
                with pytest.raises(SystemExit) as exc_info:
                    main()

        assert exc_info.value.code == 1

        state = _load_state(job_dir)
        assert state["status"] == "failed"
        assert state["exit_code"] == 1

    @patch("snodo.jobs.wrapper.time")
    def test_generic_exception(self, mock_time, tmp_path, capsys):
        """main() catches generic exceptions, marks failed, prints error."""
        mock_time.time.return_value = 1700000000.0
        job_dir = self._prepare_job_dir(tmp_path)

        with patch.object(sys, "argv", ["wrapper", job_dir, "run", "task"]):
            with patch("snodo.cli.main.main", side_effect=RuntimeError("boom")):
                with pytest.raises(SystemExit) as exc_info:
                    main()

        assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert "Job wrapper error: boom" in captured.err

        state = _load_state(job_dir)
        assert state["status"] == "failed"
        assert state["exit_code"] == 1

    @patch("snodo.jobs.wrapper.time")
    def test_cancelled_status_preserved(self, mock_time, tmp_path):
        """main() does not overwrite status=cancelled with failed/completed."""
        mock_time.time.return_value = 1700000000.0
        job_dir = self._prepare_job_dir(tmp_path, {"status": "queued", "pid": None})

        call_count = [0]
        original_load = _load_state

        def patched_load(d):
            """On second _load_state call, simulate external cancellation."""
            call_count[0] += 1
            state = original_load(d)
            if call_count[0] == 2:
                # Simulate: between first and second load, job was cancelled externally
                state["status"] = "cancelled"
                _save_state(d, state)
                return _load_state.__wrapped__(d) if hasattr(_load_state, "__wrapped__") else original_load(d)
            return state

        with patch.object(sys, "argv", ["wrapper", job_dir, "run", "task"]):
            with patch("snodo.cli.main.main", return_value=0):
                with patch("snodo.jobs.wrapper._load_state", side_effect=patched_load):
                    with pytest.raises(SystemExit) as exc_info:
                        main()

        assert exc_info.value.code == 0

        state = _load_state(job_dir)
        assert state["status"] == "cancelled"
        assert state["exit_code"] == 0
        assert "completed_at" in state

    @patch("snodo.jobs.wrapper.time")
    def test_running_state_set_with_pid(self, mock_time, tmp_path):
        """main() sets status=running, pid, and started_at before calling CLI."""
        mock_time.time.return_value = 1700000000.0
        job_dir = self._prepare_job_dir(tmp_path)

        states_during_run = []

        def capture_state(argv):
            """Capture state as seen during CLI execution."""
            states_during_run.append(_load_state(job_dir))
            return 0

        with patch.object(sys, "argv", ["wrapper", job_dir, "run", "task"]):
            with patch("snodo.cli.main.main", side_effect=capture_state):
                with pytest.raises(SystemExit):
                    main()

        assert len(states_during_run) == 1
        running_state = states_during_run[0]
        assert running_state["status"] == "running"
        assert running_state["pid"] == os.getpid()
        assert running_state["started_at"] == 1700000000.0

    @patch("snodo.jobs.wrapper.time")
    def test_argv_passed_to_cli(self, mock_time, tmp_path):
        """main() passes everything after job_dir as argv to cli_main."""
        mock_time.time.return_value = 1700000000.0
        job_dir = self._prepare_job_dir(tmp_path)

        with patch.object(sys, "argv", ["wrapper", job_dir, "run", "my task", "--mock", "--verbose"]):
            with patch("snodo.cli.main.main", return_value=0) as mock_cli:
                with pytest.raises(SystemExit):
                    main()

        mock_cli.assert_called_once_with(argv=["run", "my task", "--mock", "--verbose"])
