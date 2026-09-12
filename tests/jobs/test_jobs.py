"""Tests for the async job system.

FILE: tests/jobs/test_jobs.py

Unit tests (mock subprocess), CLI integration tests, and end-to-end tests.
"""

import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from snodo.jobs import TERMINAL_STATUSES, JobError, JobManager
from snodo.jobs.runner import build_command


@pytest.fixture
def mock_worktree_creation(tmp_path):
    """Some tests have no git repo and exercise job state, not worktree
    creation. submit() now fails loud when a worktree cannot be created
    (Fixes #29), so the worktree must be mocked there.
    """
    with patch(
        "snodo.infrastructure.worktree.create_worktree",
        return_value=str(tmp_path / "wt"),
    ):
        yield


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
    @pytest.fixture(autouse=True)
    def _mock_wt(self, mock_worktree_creation):
        yield

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

        manager.submit(sample_task_args)

        mock_spawn.assert_called_once()
        call_args = mock_spawn.call_args
        cmd, stdout_path, stderr_path, cwd = call_args[0]
        assert "snodo.jobs.wrapper" in " ".join(cmd)
        assert stdout_path.endswith("stdout.log")
        assert stderr_path.endswith("stderr.log")

    @patch("snodo.jobs.runner.spawn_background")
    def test_submit_refused_when_worktree_cannot_be_created(self, mock_spawn, manager, sample_task_args):
        """A background job whose worktree cannot be created is refused up front.

        The job state records the failure and submit() raises — never a
        silently un-isolated background run in the project tree (Fixes #29).
        """
        from snodo.infrastructure.worktree import WorktreeIsolationError

        with (
            patch(
                "snodo.infrastructure.worktree.create_worktree",
                side_effect=WorktreeIsolationError("no commits"),
            ),
            pytest.raises(JobError, match="no commits"),
        ):
            manager.submit(sample_task_args)

        mock_spawn.assert_not_called()
        job_dirs = list((manager.jobs_dir).iterdir())
        assert len(job_dirs) == 1
        state = manager._load_state(job_dirs[0])
        assert state["status"] == "failed"
        assert state["exit_code"] == 1


# === List Jobs Tests ===

class TestListJobs:
    @pytest.fixture(autouse=True)
    def _mock_wt(self, mock_worktree_creation):
        yield

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
    def test_list_row_carries_title_not_spec(self, mock_spawn, manager, sample_task_args):
        """The listing identifies the work by its first line — never the spec."""
        mock_spawn.return_value = 99999
        sample_task_args["description"] = (
            "# Add payment retry with backoff\n\n"
            + "Detailed specification prose.\n" * 40
        )

        manager.submit(sample_task_args)

        jobs = manager.list_jobs()
        assert jobs[0]["title"] == "Add payment retry with backoff"
        assert "description" not in jobs[0]
        serialized = json.dumps(jobs)
        assert "Detailed specification prose" not in serialized


class TestBoundedListing:
    """A listing answerable at a glance, cheap to ask repeatedly.

    The rows must carry enough to identify the work (task_ref, title) and
    how it ended (status, exit_code, duration), at a per-row size that does
    not scale with how much spec prose the project has accumulated.
    """

    @staticmethod
    def _seed_jobs(manager, count, spec_chars):
        base = 1_700_000_000.0
        spec = "# Job title " + "x" * spec_chars
        for i in range(count):
            job_dir = manager.jobs_dir / f"j_{i:06d}"
            job_dir.mkdir()
            (job_dir / "task.json").write_text(json.dumps({
                "description": spec,
                "task_id": f"T-{i}",
            }))
            (job_dir / "state.json").write_text(json.dumps({
                "status": "failed" if i % 2 else "completed",
                "pid": None,
                "created_at": base + i,
                "started_at": base + i + 1,
                "completed_at": base + i + 31,
                "exit_code": 1 if i % 2 else 0,
            }))

    def test_listing_bounded_across_many_jobs(self, manager):
        """91 jobs with fat specs produce a listing of rows, not prose."""
        self._seed_jobs(manager, 91, spec_chars=2400)

        jobs = manager.list_jobs()

        assert len(jobs) == 91
        payload = json.dumps(jobs)
        # the pre-fix row *was* the spec: 91 x ~2.5KB ≈ 229KB of listing
        old_style_row = json.dumps({
            "id": "j_xxxxxx", "status": "completed",
            "description": "# Job title " + "x" * 2400, "created_at": 0,
        })
        assert len(payload) < 91 * len(old_style_row) / 5
        # and per row, bounded regardless of spec length
        for j in jobs:
            assert len(json.dumps(j)) < 500
        assert "x" * 300 not in payload

    def test_row_identifies_work_and_ending(self, manager):
        self._seed_jobs(manager, 2, spec_chars=100)

        jobs = manager.list_jobs()
        failed = next(j for j in jobs if j["status"] == "failed")
        assert failed["task_ref"].startswith("T-")
        assert failed["exit_code"] == 1
        assert failed["duration_seconds"] == 30.0
        assert failed["title"].startswith("Job title")
        assert set(jobs[0]) == {
            "id", "status", "task_ref", "title", "exit_code",
            "created_at", "started_at", "completed_at", "duration_seconds",
        }

    def test_running_job_duration_advances_queued_job_none(self, manager):
        job_dir = manager.jobs_dir / "j_running"
        job_dir.mkdir()
        (job_dir / "task.json").write_text(json.dumps({"description": "go"}))
        (job_dir / "state.json").write_text(json.dumps({
            "status": "running", "pid": os.getpid(),
            "created_at": time.time() - 60, "started_at": time.time() - 30,
            "completed_at": None, "exit_code": None,
        }))
        (manager.jobs_dir / "j_queued").mkdir()

        jobs = manager.list_jobs()
        running = next(j for j in jobs if j["id"] == "j_running")
        assert 29 <= running["duration_seconds"] <= 31

    def test_retry_task_id_used_as_task_ref(self, manager):
        job_dir = manager.jobs_dir / "j_retry"
        job_dir.mkdir()
        (job_dir / "task.json").write_text(json.dumps({
            "description": "retry attempt",
            "retry_task_id": "T-9",
        }))
        (job_dir / "state.json").write_text(json.dumps({
            "status": "completed", "pid": None, "created_at": 1.0,
            "started_at": 2.0, "completed_at": 3.0, "exit_code": 0,
        }))

        jobs = manager.list_jobs()
        assert jobs[0]["task_ref"] == "T-9"

    def test_template_label_yields_to_the_prose_line(self, manager):
        """Real specs open with a section label; that is not their title."""
        job_dir = manager.jobs_dir / "j_intent"
        job_dir.mkdir()
        (job_dir / "task.json").write_text(json.dumps({
            "description": (
                "INTENT\n\n"
                "The print export works and produces a good file.\n\n"
                "PLAN\n- step one\n"
            ),
        }))
        (job_dir / "state.json").write_text(json.dumps({
            "status": "completed", "pid": None, "created_at": 1.0,
            "started_at": None, "completed_at": None, "exit_code": None,
        }))

        jobs = manager.list_jobs()
        assert jobs[0]["title"].startswith("The print export works")

    def test_short_specs_still_get_a_title(self, manager):
        job_dir = manager.jobs_dir / "j_short"
        job_dir.mkdir()
        (job_dir / "task.json").write_text(json.dumps({"description": "Fix login"}))
        (job_dir / "state.json").write_text(json.dumps({
            "status": "completed", "pid": None, "created_at": 1.0,
            "started_at": None, "completed_at": None, "exit_code": None,
        }))

        jobs = manager.list_jobs()
        assert jobs[0]["title"] == "Fix login"

    def test_title_clip_is_bounded(self, manager):
        long_line = "word " * 500
        job_dir = manager.jobs_dir / "j_long"
        job_dir.mkdir()
        (job_dir / "task.json").write_text(json.dumps({"description": long_line}))
        (job_dir / "state.json").write_text(json.dumps({
            "status": "completed", "pid": None, "created_at": 1.0,
            "started_at": None, "completed_at": None, "exit_code": None,
        }))

        jobs = manager.list_jobs()
        assert len(jobs[0]["title"]) <= 120


# === Get Status Tests ===

class TestGetStatus:
    @pytest.fixture(autouse=True)
    def _mock_wt(self, mock_worktree_creation):
        yield

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

        # Injected clock: each 1s poll advances the fake clock instead of
        # sleeping, so the timeout fires without waiting real time.
        class FakeClock:
            def __init__(self):
                self.t = 0.0

            def monotonic(self):
                return self.t

            def sleep(self, seconds):
                self.t += seconds

        with pytest.raises(JobError, match="Timeout"):
            manager.wait_for("j_wait2", timeout=0.1, clock=FakeClock())

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
        assert "-u" in cmd
        assert cmd[1:4] == ["-u", "-m", "snodo.jobs.wrapper"]
        assert "/path/to/job" in cmd
        assert "run" in cmd
        assert "do something" in cmd

    def test_build_command_with_flags(self):
        """build_command() includes --mock, --model, --verbose, --coder, --mode flags."""
        task_args = {
            "description": "task",
            "protocol": "proto.yml",
            "model": "gpt-4",
            "coder": "opencode-cli",
            "mode": "reviewer",
            "mock": True,
            "verbose": True,
            "from_pr": 42,
        }
        cmd = build_command("/job", task_args)
        assert "--mock" in cmd
        assert "--verbose" in cmd
        assert "--model" in cmd
        assert "gpt-4" in cmd
        assert "--coder" in cmd
        assert "opencode-cli" in cmd
        assert "--mode" in cmd
        assert "reviewer" in cmd
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

    def test_build_command_unbuffered(self):
        """build_command() includes -u flag for unbuffered output."""
        task_args = {
            "description": "task",
            "protocol": "proto.yml",
            "model": None,
            "mock": False,
            "verbose": False,
            "from_pr": None,
        }
        cmd = build_command("/job", task_args)
        assert "-u" in cmd

    def test_build_command_retry_names_the_spec_action(self):
        """A resumed task's spec is passed as a replacement, never as a bare positional.

        On the CLI a description beside --retry is guidance added on top of the
        recorded spec. The plan layer means the opposite — its spec file is the
        authority for the attempt — so it has to say so with the flag that means
        it, or a resumed task would be told its own spec twice.
        """
        task_args = {
            "description": "the task's spec from the plan file",
            "protocol": "proto.yml",
            "retry": "task_abc",
        }
        cmd = build_command("/job", task_args)
        assert "--retry" in cmd and "task_abc" in cmd
        assert "--replace-spec" in cmd
        assert "the task's spec from the plan file" in cmd
        # Not a positional: the argv element right after "run" is the flag.
        assert cmd[cmd.index("run") + 1] == "--replace-spec"

    def test_build_command_without_retry_keeps_positional_description(self):
        task_args = {"description": "do the thing", "protocol": "proto.yml"}
        cmd = build_command("/job", task_args)
        assert "--replace-spec" not in cmd
        assert cmd[cmd.index("run") + 1] == "do the thing"


# === CLI Integration Tests ===

class TestJobCLI:
    def test_resumed_spec_rule_matches_the_cli(self):
        """Re-dispatching a job follows `snodo run --retry`: text adds, only an
        explicit replacement discards the recorded spec."""
        from snodo.cli.commands.job_cmd import _resumed_spec

        assert _resumed_spec("the spec", "", "") == "the spec"
        assert _resumed_spec("the spec", "and note this", "") == "the spec\n\nand note this"
        assert _resumed_spec("the spec", "ignored?", "a new spec") == "a new spec"
        assert _resumed_spec("", "only text", "") == "only text"
        assert _resumed_spec("the spec", "   ", "   ") == "the spec"

    def test_job_retry_without_task_id_redispatches_the_recorded_spec(
        self, temp_project, capsys, monkeypatch
    ):
        """A job that predates task tracking is re-dispatched with its own spec.

        Text the operator adds is guidance on top of it, not a substitute for it
        — the rule that holds everywhere else a retry is offered.
        """
        import json as _json

        job_dir = temp_project / ".snodo" / "jobs" / "j_old1"
        job_dir.mkdir(parents=True)
        (job_dir / "task.json").write_text(_json.dumps({"description": "the old spec"}))

        from snodo.cli.main import main

        monkeypatch.setattr("builtins.input", lambda prompt="": "y")
        executed = []
        with patch("snodo.cli.commands.run_cmd._execute_task",
                   side_effect=lambda a, p, t, m: executed.append(t) or 0):
            assert main(["job", "retry", "j_old1", "and note this"]) == 0

        assert executed[0].spec == "the old spec\n\nand note this"

    def test_dispatch_as_new_task_refuses_an_empty_spec(self, capsys):
        from types import SimpleNamespace as NS

        from snodo.cli.commands.job_cmd import _dispatch_as_new_task

        assert _dispatch_as_new_task(NS(description="", replace_spec=""), {}, "j_none") == 1
        assert "No specification recorded for job j_none" in capsys.readouterr().err

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

    def test_startup_failure_surfaces_diagnosable_reason(self, temp_project, capsys):
        """A job that fails at startup surfaces diagnosable reason in status and logs."""
        from snodo.cli.main import main
        manager = JobManager(str(temp_project))
        job_dir = manager.jobs_dir / "j_fail01"
        job_dir.mkdir()
        (job_dir / "state.json").write_text(json.dumps({
            "status": "failed",
            "exit_code": 1,
            "created_at": time.time(),
            "completed_at": time.time(),
        }))
        (job_dir / "task.json").write_text(json.dumps({"description": "failing task"}))
        (job_dir / "stdout.log").write_text("")
        (job_dir / "stderr.log").write_text("Error: Not inside a Snodo project\n")

        # Test job status surfaces stderr error
        status_res = main(["job", "status", "j_fail01"])
        assert status_res == 0
        status_out = capsys.readouterr().out
        assert "Status: failed" in status_out
        assert "Error:" in status_out
        assert "Not inside a Snodo project" in status_out

        # Test job logs surfaces stderr when stdout is empty
        logs_res = main(["job", "logs", "j_fail01"])
        assert logs_res == 0
        logs_out = capsys.readouterr().out
        assert "(no stdout output — showing stderr)" in logs_out
        assert "Not inside a Snodo project" in logs_out


# === End-to-End Test ===

class TestEndToEnd:
    @pytest.mark.timeout(60)
    def test_mock_job_end_to_end(self, temp_project):
        """Submit a --mock job in a repo where .snodo is gitignored, wait, and verify success."""
        # Initialize git repo with .snodo in .gitignore (matching real projects)
        (temp_project / ".gitignore").write_text(".snodo/\n.snodo-worktrees/\n")
        subprocess.run(["git", "init"], cwd=str(temp_project), capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"],
                       cwd=str(temp_project), capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"],
                       cwd=str(temp_project), capture_output=True)
        Path(temp_project / "README.md").write_text("test")
        subprocess.run(["git", "add", ".gitignore", "README.md"], cwd=str(temp_project), capture_output=True)
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
        result = manager.wait_for(job_id, timeout=45)
        assert result["status"] in TERMINAL_STATUSES
        assert result["status"] == "completed"

        # Check that logs were written
        stdout = manager.get_logs(job_id, stream="stdout")
        assert len(stdout) > 0

        # Verify state.json has exit_code 0
        status = manager.get_status(job_id)
        assert status["exit_code"] == 0
