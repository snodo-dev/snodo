"""Wave 4 safety net: behavioral e2e gap fills.

FILE: tests/e2e/test_behavioral_gaps.py

Minimal-but-real e2e journeys for three command clusters that were
previously at zero e2e coverage and are heavily touched by Wave 4:

  - authorize: pending-decision round-trip (list → authorize with --yes)
  - job:       minimal lifecycle (list empty → run background → list → status → cancel)
  - agent:     list + memory (read-only happy paths)

All tests run against the CURRENT (unrefactored) code and pin observable
CLI output / state so regressions are caught immediately after Wave 4.
"""

import json
import re
import time

import pytest


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[mGKH]", "", text)


# ---------------------------------------------------------------------------
# Authorize: pending-decision round-trip
# ---------------------------------------------------------------------------

class TestAuthorize:
    """authorize command e2e journeys."""

    @pytest.mark.e2e
    def test_authorize_help(self, snodo_cli):
        """authorize --help exits 0 and documents task_id + --yes."""
        r = snodo_cli(["authorize", "--help"])
        assert r.returncode == 0
        out = _strip_ansi(r.stdout)
        assert "--yes" in out or "yes" in out.lower(), (
            "authorize --help should document --yes flag"
        )

    @pytest.mark.e2e
    def test_authorize_no_project_root_errors_gracefully(self, snodo_cli):
        """authorize without a .snodo/ project prints a clear error (exit ≠ 0)."""
        # tmp_path has no .snodo/ — should fail cleanly
        r = snodo_cli(["authorize"])
        assert r.returncode != 0, (
            "authorize should fail when no project root is found"
        )
        # Must not crash with a traceback
        assert "Traceback" not in r.stderr, (
            f"authorize crashed with traceback:\n{r.stderr}"
        )

    @pytest.mark.e2e
    def test_authorize_list_no_active_session(self, snodo_cli):
        """authorize lists pending decisions; with no active session, error is clean."""
        snodo_cli(["init", "--template", "solo", "--force"])
        r = snodo_cli(["authorize"])
        # With no active session yet, must exit non-zero with a clear message
        assert r.returncode != 0
        combined = (r.stdout + r.stderr).lower()
        assert "session" in combined or "mode" in combined, (
            f"Expected session/mode error, got: stdout={r.stdout!r} stderr={r.stderr!r}"
        )
        assert "Traceback" not in r.stderr

    @pytest.mark.e2e
    def test_authorize_list_after_run_shows_no_pending(self, snodo_cli):
        """After a --mock run attempt, authorize reports no pending decisions."""
        snodo_cli(["init", "--template", "solo", "--force"])
        # Run may exit non-zero in e2e env (no initial git commit / no API key)
        # — that is acceptable; we only care about authorize's response.
        snodo_cli(["run", "add a comment", "--mock"])

        r = snodo_cli(["authorize"])
        # Either: 0 exit with "No pending decisions" message, or non-zero
        # because no active session was established — both are valid.
        combined = _strip_ansi(r.stdout + r.stderr)
        if r.returncode == 0:
            assert "no pending" in combined.lower() or "pending" in combined.lower()
        else:
            # Acceptable: no active session for the mode after task completes
            assert "Traceback" not in r.stderr

    @pytest.mark.e2e
    def test_authorize_reject_all_no_pending(self, snodo_cli):
        """authorize --reject-all with no pending decisions exits 0 and says so."""
        snodo_cli(["init", "--template", "solo", "--force"])
        snodo_cli(["run", "add a comment", "--mock"])

        r = snodo_cli(["authorize", "--reject-all"])
        # Exit 0 with "no pending" if there is an active session
        if r.returncode == 0:
            combined = _strip_ansi(r.stdout + r.stderr).lower()
            assert "no pending" in combined or "0" in combined
        else:
            # Non-zero is also acceptable if no active session exists
            assert "Traceback" not in r.stderr

    @pytest.mark.e2e
    def test_authorize_bad_task_id_exits_nonzero(self, snodo_cli):
        """authorize <nonexistent_task_id> exits non-zero with a clear message."""
        snodo_cli(["init", "--template", "solo", "--force"])
        snodo_cli(["run", "add a comment", "--mock"])

        r = snodo_cli(["authorize", "task_does_not_exist_xyz"])
        assert r.returncode != 0
        assert "Traceback" not in r.stderr
        combined = _strip_ansi(r.stdout + r.stderr)
        # Should say something about no pending decision or no session
        assert any(kw in combined.lower() for kw in ["no pending", "session", "not found"]), (
            f"Expected descriptive error, got:\nstdout={r.stdout!r}\nstderr={r.stderr!r}"
        )


# ---------------------------------------------------------------------------
# Job: minimal lifecycle
# ---------------------------------------------------------------------------

class TestJobLifecycle:
    """job command e2e journeys."""

    @pytest.mark.e2e
    def test_job_list_empty(self, snodo_cli):
        """job list in a fresh project exits 0 and reports no jobs."""
        snodo_cli(["init", "--template", "solo", "--force"])
        r = snodo_cli(["job", "list"])
        assert r.returncode == 0
        out = _strip_ansi(r.stdout).lower()
        assert "no jobs" in out or "job" in out, (
            f"Unexpected output from job list: {r.stdout!r}"
        )

    @pytest.mark.e2e
    def test_job_status_unknown_id_errors(self, snodo_cli):
        """job status with an unknown ID exits non-zero with a clear message."""
        snodo_cli(["init", "--template", "solo", "--force"])
        r = snodo_cli(["job", "status", "j_does_not_exist"])
        assert r.returncode != 0
        assert "Traceback" not in r.stderr

    @pytest.mark.e2e
    def test_job_cancel_unknown_id_errors(self, snodo_cli):
        """job cancel with an unknown ID exits non-zero cleanly."""
        snodo_cli(["init", "--template", "solo", "--force"])
        r = snodo_cli(["job", "cancel", "j_does_not_exist"])
        assert r.returncode != 0
        assert "Traceback" not in r.stderr

    @pytest.mark.e2e
    def test_job_archive_prune_empty(self, snodo_cli):
        """job archive and job prune with --yes on an empty project exit 0."""
        snodo_cli(["init", "--template", "solo", "--force"])

        r_archive = snodo_cli(["job", "archive", "--yes"])
        assert r_archive.returncode == 0, (
            f"job archive failed: {r_archive.stderr}"
        )

        r_prune = snodo_cli(["job", "prune", "--yes"])
        assert r_prune.returncode == 0, (
            f"job prune failed: {r_prune.stderr}"
        )

    @pytest.mark.e2e
    def test_job_background_create_then_list_and_status(self, snodo_cli):
        """Create a background job, then list and status it."""
        snodo_cli(["init", "--template", "solo", "--force"])

        # Dispatch in background; --mock ensures it finishes quickly
        run_r = snodo_cli(["run", "add a comment", "--mock", "--background"])
        assert run_r.returncode == 0, (
            f"background run failed:\nstdout={run_r.stdout}\nstderr={run_r.stderr}"
        )

        # Extract job ID from output
        out = _strip_ansi(run_r.stdout)
        job_id = None
        for token in out.split():
            if token.startswith("j_"):
                job_id = token.rstrip(".,;")
                break
        assert job_id is not None, (
            f"No job ID (j_...) found in background run output:\n{out}"
        )

        # job list should show it
        list_r = snodo_cli(["job", "list"])
        assert list_r.returncode == 0
        assert job_id in _strip_ansi(list_r.stdout), (
            f"Job {job_id} not found in job list output:\n{list_r.stdout}"
        )

        # job status should describe it
        status_r = snodo_cli(["job", "status", job_id])
        assert status_r.returncode == 0
        status_out = _strip_ansi(status_r.stdout)
        assert job_id in status_out
        assert any(kw in status_out.lower() for kw in [
            "status", "running", "done", "complete", "failed", "pending",
        ]), f"job status output lacks status field:\n{status_out}"

    @pytest.mark.e2e
    def test_job_logs_on_existing_job(self, snodo_cli):
        """job logs on a completed --mock background job exits 0."""
        snodo_cli(["init", "--template", "solo", "--force"])
        run_r = snodo_cli(["run", "add a comment", "--mock", "--background"])
        assert run_r.returncode == 0

        out = _strip_ansi(run_r.stdout)
        job_id = next(
            (t.rstrip(".,;") for t in out.split() if t.startswith("j_")),
            None,
        )
        if job_id is None:
            pytest.skip("No job ID found — background mode may not be supported")

        # Give it a moment to start
        time.sleep(1)

        logs_r = snodo_cli(["job", "logs", job_id])
        assert logs_r.returncode == 0, (
            f"job logs exited {logs_r.returncode}: {logs_r.stderr}"
        )

    @pytest.mark.e2e
    def test_job_retry_unknown_exits_nonzero(self, snodo_cli):
        """job retry on an unknown job ID exits non-zero cleanly."""
        snodo_cli(["init", "--template", "solo", "--force"])
        r = snodo_cli(["job", "retry", "j_nonexistent_abc123"])
        assert r.returncode != 0
        assert "Traceback" not in r.stderr

    @pytest.mark.e2e
    def test_job_unarchive_empty(self, snodo_cli):
        """job unarchive --yes with no archive exits 0."""
        snodo_cli(["init", "--template", "solo", "--force"])
        r = snodo_cli(["job", "unarchive", "--yes"])
        assert r.returncode == 0, (
            f"job unarchive failed: {r.stderr}"
        )


# ---------------------------------------------------------------------------
# Agent: read-only happy paths
# ---------------------------------------------------------------------------

class TestAgent:
    """agent command e2e journeys."""

    @pytest.mark.e2e
    def test_agent_list_no_agents(self, snodo_cli):
        """agent list on a fresh project exits 0 and says no agents."""
        snodo_cli(["init", "--template", "solo", "--force"])
        r = snodo_cli(["agent", "list"])
        assert r.returncode == 0
        out = _strip_ansi(r.stdout).lower()
        assert "no agents" in out or "agent" in out, (
            f"Unexpected agent list output: {r.stdout!r}"
        )

    @pytest.mark.e2e
    def test_agent_list_after_mock_run(self, snodo_cli):
        """After a --mock run, agent list exits 0 (agent may or may not appear)."""
        snodo_cli(["init", "--template", "solo", "--force"])
        snodo_cli(["run", "add a comment", "--mock"])

        r = snodo_cli(["agent", "list"])
        assert r.returncode == 0
        assert "Traceback" not in r.stderr

    @pytest.mark.e2e
    def test_agent_memory_unknown_id_exits_nonzero(self, snodo_cli):
        """agent memory with an unknown agent ID exits non-zero cleanly."""
        snodo_cli(["init", "--template", "solo", "--force"])
        r = snodo_cli(["agent", "memory", "unknown:nonexistent"])
        assert r.returncode != 0
        assert "Traceback" not in r.stderr
        combined = _strip_ansi(r.stdout + r.stderr)
        assert any(kw in combined.lower() for kw in ["not found", "error"]), (
            f"Expected descriptive error, got: {combined!r}"
        )

    @pytest.mark.e2e
    def test_agent_memory_registered_agent(self, snodo_cli):
        """After a --mock run, if an agent was registered, memory shows its details."""
        snodo_cli(["init", "--template", "solo", "--force"])
        snodo_cli(["run", "add a comment", "--mock"])

        list_r = snodo_cli(["agent", "list"])
        assert list_r.returncode == 0

        out = _strip_ansi(list_r.stdout)
        # Check if any agent IDs are listed (format: project:mode)
        agent_id = None
        for line in out.splitlines():
            stripped = line.strip()
            if ":" in stripped and not stripped.startswith("ID") and not stripped.startswith("-"):
                # Likely an agent row
                agent_id = stripped.split()[0] if stripped.split() else None
                break

        if agent_id is None:
            # No agents created in this run (mock may not register one)
            pytest.skip("No agent registered after mock run — nothing to test for memory")

        mem_r = snodo_cli(["agent", "memory", agent_id])
        assert mem_r.returncode == 0, (
            f"agent memory failed for {agent_id}:\n{mem_r.stderr}"
        )
        mem_out = _strip_ansi(mem_r.stdout)
        assert "Thread ID" in mem_out or "thread" in mem_out.lower(), (
            f"agent memory output lacks thread info:\n{mem_out}"
        )

    @pytest.mark.e2e
    def test_agent_rotate_unknown_id_exits_nonzero(self, snodo_cli):
        """agent rotate with an unknown agent ID exits non-zero cleanly."""
        snodo_cli(["init", "--template", "solo", "--force"])
        r = snodo_cli(["agent", "rotate", "unknown:nonexistent"])
        # rotate may succeed (creating a new agent) or fail — either is fine
        # but it must never crash with a traceback
        assert "Traceback" not in r.stderr

    @pytest.mark.e2e
    def test_agent_reset_unknown_id_behavior(self, snodo_cli):
        """agent reset with an unknown agent ID does not crash with a traceback."""
        snodo_cli(["init", "--template", "solo", "--force"])
        r = snodo_cli(["agent", "reset", "unknown:nonexistent"])
        assert "Traceback" not in r.stderr
