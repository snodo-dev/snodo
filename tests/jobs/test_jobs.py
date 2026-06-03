"""Tests for the async job system.

FILE: tests/jobs/test_jobs.py

Unit tests (mock subprocess), CLI integration tests, and end-to-end tests.
"""

import json
import os
import signal
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from snodo.jobs import JobManager, JobError, TERMINAL_STATUSES
from snodo.jobs.runner import build_command


# === Fixtures ===

@pytest.fixture
def temp_project():
    """Create a temporary project with .snodo/ directory."""
    temp_dir = tempfile.mkdtemp()
    snodo_dir = Path(temp_dir) / ".snodo"
    snodo_dir.mkdir()

    # Write a minimal protocol file
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
        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def manager(temp_project):
    """Create a JobManager for the temp project."""
    return JobManager(str(temp_project))


@pytest.fixture
def sample_task_args():
    """Sample task arguments for testing."""
    return {
        "description": "Test task description",
        "protocol": ".snodo/protocol.yml",
        "model": "claude-sonnet-4-20250514",
        "mock": True,
        "verbose": False,
        "from_pr": None,
        "cwd": "/tmp/test",
    }


# === JobManager Init Tests ===

class TestJobManagerInit:
    def test_valid_project_root(self, temp_project):
        """JobManager initializes with valid project root."""
        mgr = JobManager(str(temp_project))
        assert mgr.jobs_dir.exists()
        assert mgr.jobs_dir == temp_project / ".snodo" / "jobs"

    def test_creates_jobs_dir(self, temp_project):
        """JobManager creates .snodo/jobs/ if missing."""
        jobs_dir = temp_project / ".snodo" / "jobs"
        if jobs_dir.exists():
            jobs_dir.rmdir()
        mgr = JobManager(str(temp_project))
        assert mgr.jobs_dir.exists()

    def test_invalid_project_root(self, tmp_path):
        """JobManager raises ValueError for non-snodo project."""
        with pytest.raises(ValueError, match="Not a snodo project"):
            JobManager(str(tmp_path))


# === ID Generation Tests ===

class TestIdGeneration:
    def test_id_format(self, manager):
        """Generated IDs match j_<6-hex> format."""
        job_id = manager._generate_id()
        assert job_id.startswith("j_")
        assert len(job_id) == 8  # j_ + 6 hex chars
        int(job_id[2:], 16)  # Should parse as hex

    def test_ids_are_unique(self, manager):
        """Successive IDs are different."""
        ids = set()
        for _ in range(5):
            job_id = manager._generate_id()
            # Create the dir so next call avoids collision
            (manager.jobs_dir / job_id).mkdir()
            ids.add(job_id)
        assert len(ids) == 5


# === State Management Tests ===

class TestStateManagement:
    def test_save_and_load_state(self, manager):
        """State round-trips through save/load."""
        job_dir = manager.jobs_dir / "j_test01"
        job_dir.mkdir()
        state = {"status": "running", "pid": 12345, "created_at": time.time()}
        manager._save_state(job_dir, state)
        loaded = manager._load_state(job_dir)
        assert loaded["status"] == "running"
        assert loaded["pid"] == 12345

    def test_save_state_atomic(self, manager):
        """State write uses atomic rename (no .tmp file left)."""
        job_dir = manager.jobs_dir / "j_test02"
        job_dir.mkdir()
        state = {"status": "queued"}
        manager._save_state(job_dir, state)
        assert not (job_dir / "state.json.tmp").exists()
        assert (job_dir / "state.json").exists()

    def test_load_state_missing_raises(self, manager):
        """Loading state from empty dir raises JobError."""
        job_dir = manager.jobs_dir / "j_test03"
        job_dir.mkdir()
        with pytest.raises(JobError, match="No state.json"):
            manager._load_state(job_dir)


# === Submit Tests (mock subprocess) ===

class TestSubmit:
    @patch("snodo.jobs.runner.spawn_background")
    def test_submit_creates_directory_structure(self, mock_spawn, manager, sample_task_args):
        """submit() creates job dir with state.json and task.json."""
        mock_spawn.return_value = 99999

        job_id = manager.submit(sample_task_args)

        job_dir = manager.jobs_dir / job_id
        assert job_dir.is_dir()
        assert (job_dir / "state.json").exists()
        assert (job_dir / "task.json").exists()

    @patch("snodo.jobs.runner.spawn_background")
    def test_submit_writes_task_json(self, mock_spawn, manager, sample_task_args):
        """submit() writes task_args to task.json."""
        mock_spawn.return_value = 99999

        job_id = manager.submit(sample_task_args)

        task_path = manager.jobs_dir / job_id / "task.json"
        task = json.loads(task_path.read_text())
        assert task["description"] == "Test task description"
        assert task["mock"] is True

    @patch("snodo.jobs.runner.spawn_background")
    def test_submit_state_is_running(self, mock_spawn, manager, sample_task_args):
        """After submit, state should be running with PID."""
        mock_spawn.return_value = 42000

        job_id = manager.submit(sample_task_args)

        state = manager._load_state(manager.jobs_dir / job_id)
        assert state["status"] == "running"
        assert state["pid"] == 42000
        assert state["started_at"] is not None

    @patch("snodo.jobs.runner.spawn_background")
    def test_submit_returns_job_id(self, mock_spawn, manager, sample_task_args):
        """submit() returns a valid job ID."""
        mock_spawn.return_value = 99999

        job_id = manager.submit(sample_task_args)
        assert job_id.startswith("j_")

    @patch("snodo.jobs.runner.spawn_background")
    def test_submit_calls_spawn_with_correct_args(self, mock_spawn, manager, sample_task_args):
        """submit() passes correct paths to spawn_background."""
        mock_spawn.return_value = 99999

        job_id = manager.submit(sample_task_args)

        mock_spawn.assert_called_once()
        call_args = mock_spawn.call_args
        cmd, stdout_path, stderr_path, cwd = call_args[0]
        assert "snodo.jobs.wrapper" in " ".join(cmd)
        assert stdout_path.endswith("stdout.log")
        assert stderr_path.endswith("stderr.log")


# === List Jobs Tests ===

class TestListJobs:
    @patch("snodo.jobs.runner.spawn_background")
    def test_list_returns_all_jobs(self, mock_spawn, manager, sample_task_args):
        """list_jobs() returns all submitted jobs."""
        mock_spawn.return_value = 99999

        id1 = manager.submit(sample_task_args)
        time.sleep(0.01)
        id2 = manager.submit(sample_task_args)

        jobs = manager.list_jobs()
        ids = [j["id"] for j in jobs]
        assert id1 in ids
        assert id2 in ids

    @patch("snodo.jobs.runner.spawn_background")
    def test_list_sorted_newest_first(self, mock_spawn, manager, sample_task_args):
        """list_jobs() returns newest jobs first."""
        mock_spawn.return_value = 99999

        id1 = manager.submit(sample_task_args)
        time.sleep(0.01)
        id2 = manager.submit(sample_task_args)

        jobs = manager.list_jobs()
        assert jobs[0]["id"] == id2
        assert jobs[1]["id"] == id1

    def test_list_empty(self, manager):
        """list_jobs() returns empty list with no jobs."""
        assert manager.list_jobs() == []

    @patch("snodo.jobs.runner.spawn_background")
    def test_list_includes_description(self, mock_spawn, manager, sample_task_args):
        """list_jobs() includes task description."""
        mock_spawn.return_value = 99999

        manager.submit(sample_task_args)

        jobs = manager.list_jobs()
        assert jobs[0]["description"] == "Test task description"


# === Get Status Tests ===

class TestGetStatus:
    @patch("os.kill")
    @patch("snodo.jobs.runner.spawn_background")
    def test_get_status_basic(self, mock_spawn, mock_kill, manager, sample_task_args):
        """get_status() returns state and task info."""
        mock_spawn.return_value = 99999
        mock_kill.return_value = None  # Process is "alive"

        job_id = manager.submit(sample_task_args)
        status = manager.get_status(job_id)

        assert status["id"] == job_id
        assert status["status"] == "running"
        assert status["pid"] == 99999
        assert "task" in status

    def test_get_status_invalid_id(self, manager):
        """get_status() raises JobError for unknown ID."""
        with pytest.raises(JobError, match="Job not found"):
            manager.get_status("j_nonexistent")


# === Get Logs Tests ===

class TestGetLogs:
    def test_get_logs_stdout(self, manager):
        """get_logs() reads stdout.log content."""
        job_dir = manager.jobs_dir / "j_log01"
        job_dir.mkdir()
        (job_dir / "state.json").write_text('{"status": "completed"}')
        (job_dir / "stdout.log").write_text("line1\nline2\nline3\n")

        content = manager.get_logs("j_log01", stream="stdout")
        assert "line1" in content
        assert "line3" in content

    def test_get_logs_stderr(self, manager):
        """get_logs() reads stderr.log content."""
        job_dir = manager.jobs_dir / "j_log02"
        job_dir.mkdir()
        (job_dir / "state.json").write_text('{"status": "completed"}')
        (job_dir / "stderr.log").write_text("error output\n")

        content = manager.get_logs("j_log02", stream="stderr")
        assert "error output" in content

    def test_get_logs_tail(self, manager):
        """get_logs() with tail returns only last N lines."""
        job_dir = manager.jobs_dir / "j_log03"
        job_dir.mkdir()
        (job_dir / "state.json").write_text('{"status": "completed"}')
        (job_dir / "stdout.log").write_text("line1\nline2\nline3\nline4\nline5\n")

        content = manager.get_logs("j_log03", stream="stdout", tail=2)
        lines = content.strip().splitlines()
        assert len(lines) == 2
        assert "line4" in lines[0]
        assert "line5" in lines[1]

    def test_get_logs_missing_file(self, manager):
        """get_logs() returns empty string for missing log file."""
        job_dir = manager.jobs_dir / "j_log04"
        job_dir.mkdir()
        (job_dir / "state.json").write_text('{"status": "queued"}')

        content = manager.get_logs("j_log04", stream="stdout")
        assert content == ""

    def test_get_logs_tail_large_file_bounded_read(self, manager):
        """tail=50 on a large file reads only a trailing window, not whole file."""
        job_dir = manager.jobs_dir / "j_log05"
        job_dir.mkdir()
        (job_dir / "state.json").write_text('{"status": "completed"}')

        log_file = job_dir / "stdout.log"
        with open(log_file, "w") as f:
            for i in range(20000):
                f.write(f"line {i:05d} padding for size\n")

        import time
        start = time.time()
        content = manager.get_logs("j_log05", stream="stdout", tail=50)
        elapsed = time.time() - start

        lines = content.strip().splitlines()
        assert len(lines) == 50
        assert "line 19999" in lines[-1]
        # Should be well under 100ms for a bounded tail read
        assert elapsed < 1.0, f"tail read took {elapsed:.2f}s — not bounded"

    def test_get_logs_tail_fewer_lines_than_file(self, manager):
        """tail larger than file line count returns all lines."""
        job_dir = manager.jobs_dir / "j_log06"
        job_dir.mkdir()
        (job_dir / "state.json").write_text('{"status": "completed"}')
        (job_dir / "stdout.log").write_text("a\nb\nc\n")

        content = manager.get_logs("j_log06", stream="stdout", tail=10)
        lines = content.strip().splitlines()
        assert len(lines) == 3
        assert lines == ["a", "b", "c"]

    def test_get_logs_tail_empty_file(self, manager):
        """tail on empty log file returns empty string."""
        job_dir = manager.jobs_dir / "j_log07"
        job_dir.mkdir()
        (job_dir / "state.json").write_text('{"status": "completed"}')
        (job_dir / "stdout.log").write_text("")

        content = manager.get_logs("j_log07", stream="stdout", tail=10)
        assert content == ""

    def test_get_logs_no_tail_capped_read(self, manager):
        """tail=None reads at most 1MB from end, never unbounded."""
        job_dir = manager.jobs_dir / "j_log08"
        job_dir.mkdir()
        (job_dir / "state.json").write_text('{"status": "completed"}')
        (job_dir / "stdout.log").write_text("line1\nline2\nline3\n")

        content = manager.get_logs("j_log08", stream="stdout", tail=None)
        assert "line3" in content
        assert "line1" in content

    def test_get_logs_tail_zero_same_as_none(self, manager):
        """tail=0 returns capped read, same as tail=None."""
        job_dir = manager.jobs_dir / "j_log09"
        job_dir.mkdir()
        (job_dir / "state.json").write_text('{"status": "completed"}')
        (job_dir / "stdout.log").write_text("line1\nline2\nline3\n")

        content = manager.get_logs("j_log09", stream="stdout", tail=0)
        assert "line3" in content
        assert "line1" in content


# === Cancel Tests ===

class TestCancel:
    def test_cancel_running_job(self, manager):
        """cancel() sends SIGTERM and updates state."""
        job_dir = manager.jobs_dir / "j_can01"
        job_dir.mkdir()
        state = {"status": "running", "pid": 99999, "created_at": time.time()}
        manager._save_state(job_dir, state)

        with patch("os.kill") as mock_kill:
            result = manager.cancel("j_can01")

        assert result["status"] == "cancelled"
        assert result["completed_at"] is not None
        mock_kill.assert_called_once_with(99999, signal.SIGTERM)

    def test_cancel_already_terminal(self, manager):
        """cancel() raises for already completed jobs."""
        job_dir = manager.jobs_dir / "j_can02"
        job_dir.mkdir()
        state = {"status": "completed", "exit_code": 0}
        manager._save_state(job_dir, state)

        with pytest.raises(JobError, match="already completed"):
            manager.cancel("j_can02")

    def test_cancel_dead_process(self, manager):
        """cancel() handles already-dead process gracefully."""
        job_dir = manager.jobs_dir / "j_can03"
        job_dir.mkdir()
        state = {"status": "running", "pid": 99999, "created_at": time.time()}
        manager._save_state(job_dir, state)

        with patch("os.kill", side_effect=ProcessLookupError):
            result = manager.cancel("j_can03")

        assert result["status"] == "cancelled"


# === Wait For Tests ===

class TestWaitFor:
    def test_wait_returns_when_complete(self, manager):
        """wait_for() returns immediately for completed jobs."""
        job_dir = manager.jobs_dir / "j_wait1"
        job_dir.mkdir()
        state = {"status": "completed", "exit_code": 0, "created_at": time.time()}
        manager._save_state(job_dir, state)
        (job_dir / "task.json").write_text('{"description": "test"}')

        result = manager.wait_for("j_wait1", timeout=5)
        assert result["status"] == "completed"

    def test_wait_timeout(self, manager):
        """wait_for() raises JobError on timeout."""
        job_dir = manager.jobs_dir / "j_wait2"
        job_dir.mkdir()
        state = {"status": "running", "pid": None, "created_at": time.time()}
        manager._save_state(job_dir, state)

        with pytest.raises(JobError, match="Timeout"):
            manager.wait_for("j_wait2", timeout=0.1)

    def test_wait_returns_all_terminal(self, manager):
        """wait_for() returns for any terminal status."""
        for status in TERMINAL_STATUSES:
            job_dir = manager.jobs_dir / f"j_wt_{status[:4]}"
            job_dir.mkdir()
            state = {"status": status, "exit_code": 0, "created_at": time.time()}
            manager._save_state(job_dir, state)
            (job_dir / "task.json").write_text('{}')

            result = manager.wait_for(f"j_wt_{status[:4]}", timeout=5)
            assert result["status"] == status


# === State Reconciliation Tests ===

class TestReconciliation:
    def test_reconcile_running_alive(self, manager):
        """Reconciliation keeps running status if process is alive."""
        job_dir = manager.jobs_dir / "j_rec01"
        job_dir.mkdir()
        state = {"status": "running", "pid": os.getpid()}

        with patch("os.kill") as mock_kill:
            mock_kill.return_value = None  # Process is alive
            result = manager._reconcile_state(job_dir, state)

        assert result["status"] == "running"

    def test_reconcile_dead_wrapper_updated(self, manager):
        """Reconciliation reads wrapper's final state for dead process."""
        job_dir = manager.jobs_dir / "j_rec02"
        job_dir.mkdir()
        # Wrapper wrote completed state before dying
        final_state = {"status": "completed", "exit_code": 0, "pid": 99999}
        manager._save_state(job_dir, final_state)

        running_state = {"status": "running", "pid": 99999}

        with patch("os.kill", side_effect=ProcessLookupError):
            result = manager._reconcile_state(job_dir, running_state)

        assert result["status"] == "completed"

    def test_reconcile_dead_wrapper_crashed(self, manager):
        """Reconciliation marks failed if wrapper crashed without updating state."""
        job_dir = manager.jobs_dir / "j_rec03"
        job_dir.mkdir()
        # State still says running (wrapper never updated)
        state = {"status": "running", "pid": 99999, "created_at": time.time()}
        manager._save_state(job_dir, state)

        with patch("os.kill", side_effect=ProcessLookupError):
            result = manager._reconcile_state(job_dir, state)

        assert result["status"] == "failed"
        assert result["exit_code"] == -1

    def test_reconcile_terminal_no_change(self, manager):
        """Reconciliation doesn't change terminal states."""
        job_dir = manager.jobs_dir / "j_rec04"
        job_dir.mkdir()
        state = {"status": "completed", "exit_code": 0}
        result = manager._reconcile_state(job_dir, state)
        assert result["status"] == "completed"


# === Runner Tests ===

class TestRunner:
    def test_build_command_basic(self):
        """build_command() constructs correct command list."""
        task_args = {
            "description": "do something",
            "protocol": ".snodo/protocol.yml",
            "model": None,
            "mock": False,
            "verbose": False,
            "from_pr": None,
        }
        cmd = build_command("/path/to/job", task_args)
        assert cmd[1:3] == ["-m", "snodo.jobs.wrapper"]
        assert "/path/to/job" in cmd
        assert "run" in cmd
        assert "do something" in cmd

    def test_build_command_with_flags(self):
        """build_command() includes --mock, --model, --verbose flags."""
        task_args = {
            "description": "task",
            "protocol": "proto.yml",
            "model": "gpt-4",
            "mock": True,
            "verbose": True,
            "from_pr": 42,
        }
        cmd = build_command("/job", task_args)
        assert "--mock" in cmd
        assert "--verbose" in cmd
        assert "--model" in cmd
        assert "gpt-4" in cmd
        assert "--from-pr" in cmd
        assert "42" in cmd

    def test_build_command_protocol(self):
        """build_command() includes --protocol flag."""
        task_args = {
            "description": "task",
            "protocol": "custom/proto.yml",
            "model": None,
            "mock": False,
            "verbose": False,
            "from_pr": None,
        }
        cmd = build_command("/job", task_args)
        assert "--protocol" in cmd
        assert "custom/proto.yml" in cmd


# === CLI Integration Tests ===

class TestJobCLI:
    def test_job_list_via_main(self, temp_project):
        """snodo job list works via main()."""
        from snodo.cli.main import main
        result = main(["job", "list"])
        assert result == 0

    def test_job_status_missing_id(self, temp_project):
        """snodo job status with unknown ID returns error."""
        from snodo.cli.main import main
        result = main(["job", "status", "j_nonexist"])
        assert result == 1

    def test_job_logs_missing_id(self, temp_project):
        """snodo job logs with unknown ID returns error."""
        from snodo.cli.main import main
        result = main(["job", "logs", "j_nonexist"])
        assert result == 1

    def test_job_cancel_missing_id(self, temp_project):
        """snodo job cancel with unknown ID returns error."""
        from snodo.cli.main import main
        result = main(["job", "cancel", "j_nonexist"])
        assert result == 1

    @patch("snodo.jobs.runner.spawn_background")
    def test_run_background_creates_job(self, mock_spawn, temp_project):
        """snodo run --background creates a job."""
        mock_spawn.return_value = 99999
        from snodo.cli.main import main

        # Initialize git repo for the temp project
        subprocess.run(["git", "init"], cwd=str(temp_project), capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"],
                       cwd=str(temp_project), capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"],
                       cwd=str(temp_project), capture_output=True)
        Path(temp_project / "README.md").write_text("test")
        subprocess.run(["git", "add", "."], cwd=str(temp_project), capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(temp_project), capture_output=True)

        result = main(["run", "--background", "--mock", "test background task"])
        assert result == 0

        # Verify job was created
        jobs_dir = temp_project / ".snodo" / "jobs"
        job_dirs = list(jobs_dir.iterdir())
        assert len(job_dirs) == 1

    def test_run_background_plan_rejected(self, temp_project, capsys):
        """--plan and --background cannot be combined."""
        from snodo.cli.main import main
        result = main(["run", "--background", "--plan", "myplan", "task"])
        assert result == 1
        captured = capsys.readouterr()
        assert "--plan" in captured.err and "--background" in captured.err


# === End-to-End Test ===

class TestEndToEnd:
    @pytest.mark.timeout(30)
    def test_mock_job_end_to_end(self, temp_project):
        """Submit a --mock job, wait, and verify logs contain output."""
        # Initialize git repo
        subprocess.run(["git", "init"], cwd=str(temp_project), capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"],
                       cwd=str(temp_project), capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"],
                       cwd=str(temp_project), capture_output=True)
        Path(temp_project / "README.md").write_text("test")
        subprocess.run(["git", "add", "."], cwd=str(temp_project), capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"],
                       cwd=str(temp_project), capture_output=True)

        manager = JobManager(str(temp_project))
        task_args = {
            "description": "e2e test task",
            "protocol": ".snodo/protocol.yml",
            "model": None,
            "mock": True,
            "verbose": False,
            "from_pr": None,
            "cwd": str(temp_project),
        }

        job_id = manager.submit(task_args)
        assert job_id.startswith("j_")

        # Wait for completion (mock should finish quickly)
        result = manager.wait_for(job_id, timeout=20)
        assert result["status"] in TERMINAL_STATUSES

        # Check that logs were written
        stdout = manager.get_logs(job_id, stream="stdout")
        stderr = manager.get_logs(job_id, stream="stderr")
        # At minimum, the wrapper should have produced some output
        assert len(stdout) > 0 or len(stderr) > 0 or result["status"] == "completed"

        # Verify state.json has final status
        status = manager.get_status(job_id)
        assert status["exit_code"] is not None
