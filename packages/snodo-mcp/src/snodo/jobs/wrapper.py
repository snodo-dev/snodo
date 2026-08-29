"""Child process wrapper for background jobs.

FILE: snodo/jobs/wrapper.py

Invoked as: python -m snodo.jobs.wrapper <job_dir> run "task" [--flags...]

Runs the snodo CLI as a subprocess with the provided arguments, then writes
final status + exit_code to state.json. The CLI is invoked as a subprocess
rather than imported in-process so the mcp layer (snodo.jobs) does not depend
on the app layer (snodo.cli).
"""

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

_logger = logging.getLogger(__name__)


def _save_state(job_dir: str, state: dict) -> None:
    """Atomically write state.json (write tmp + os.replace)."""
    state_path = os.path.join(job_dir, "state.json")
    tmp_path = os.path.join(job_dir, "state.json.tmp")
    with open(tmp_path, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp_path, state_path)


def _load_state(job_dir: str) -> dict:
    """Load current state.json."""
    state_path = os.path.join(job_dir, "state.json")
    with open(state_path) as f:
        return json.load(f)


def main():
    """Entry point for the wrapper subprocess."""
    if len(sys.argv) < 3:
        print("Usage: python -m snodo.jobs.wrapper <job_dir> run <args...>", file=sys.stderr)
        sys.exit(2)

    job_dir = sys.argv[1]
    argv = sys.argv[2:]  # Everything after job_dir goes to snodo CLI

    # Export job_id + project_root so the engine can write to job state.json
    os.environ["SNODO_JOB_ID"] = Path(job_dir).name
    os.environ["SNODO_PROJECT_ROOT"] = str(Path(job_dir).parent.parent.parent)

    # Export worktree_path if the job was set up with one
    try:
        task_path = Path(job_dir) / "task.json"
        if task_path.exists():
            import json as _json
            task_data = _json.loads(task_path.read_text())
            wt = task_data.get("worktree_path")
            if wt:
                os.environ["SNODO_WORKTREE_PATH"] = wt
    except Exception as e:
        _logger.debug("Failed to set SNODO_WORKTREE_PATH from task.json: %s", e)

    # Load current state and mark as running
    state = _load_state(job_dir)
    state["status"] = "running"
    state["pid"] = os.getpid()
    state["started_at"] = time.time()
    _save_state(job_dir, state)

    exit_code = 1
    try:
        cmd = [sys.executable, "-m", "snodo", *argv]
        proc = subprocess.run(cmd, check=False)
        exit_code = proc.returncode
    except Exception as e:
        print(f"Job wrapper error: {e}", file=sys.stderr)
        exit_code = 1
    finally:
        # Belt-and-suspenders cleanup on success only. A failed task keeps its
        # worktree for inspection (the CLI already tears down a cleanly
        # completed task), so the wrapper must not delete the evidence.
        if exit_code == 0:
            wt = os.environ.get("SNODO_WORKTREE_PATH")
            if wt:
                try:
                    from snodo.infrastructure.worktree import remove_worktree
                    project_root = str(Path(job_dir).parent.parent.parent)
                    job_id = Path(job_dir).name
                    remove_worktree(project_root, job_id)
                except Exception as e:
                    _logger.debug("Failed to remove worktree for completed job %s: %s", job_id, e)

    # Write final state
    state = _load_state(job_dir)
    status = "completed" if exit_code == 0 else "failed"
    # Don't overwrite if already cancelled
    if state.get("status") != "cancelled":
        state["status"] = status
    state["exit_code"] = exit_code
    state["completed_at"] = time.time()

    _save_state(job_dir, state)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
