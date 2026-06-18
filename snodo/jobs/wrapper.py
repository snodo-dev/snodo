"""Child process wrapper for background jobs.

FILE: snodo/jobs/wrapper.py

Invoked as: python -m snodo.jobs.wrapper <job_dir> run "task" [--flags...]

Calls snodo.cli.main.main(argv) with the provided arguments,
then writes final status + exit_code to state.json.
"""

import json
import os
import sys
import time
from pathlib import Path


def _save_state(job_dir: str, state: dict) -> None:
    """Atomically write state.json (write tmp + os.rename)."""
    state_path = os.path.join(job_dir, "state.json")
    tmp_path = os.path.join(job_dir, "state.json.tmp")
    with open(tmp_path, "w") as f:
        json.dump(state, f, indent=2)
    os.rename(tmp_path, state_path)


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

    # Load current state and mark as running
    state = _load_state(job_dir)
    state["status"] = "running"
    state["pid"] = os.getpid()
    state["started_at"] = time.time()
    _save_state(job_dir, state)

    exit_code = 1
    try:
        from snodo.cli.main import main as cli_main
        result = cli_main(argv=argv)
        exit_code = result if isinstance(result, int) else 0
    except SystemExit as e:
        exit_code = e.code if isinstance(e.code, int) else 1
    except Exception as e:
        print(f"Job wrapper error: {e}", file=sys.stderr)
        exit_code = 1

    # Write final state
    state = _load_state(job_dir)
    status = "completed" if exit_code == 0 else "failed"
    # Don't overwrite if already cancelled
    if state.get("status") != "cancelled":
        state["status"] = status
    state["exit_code"] = exit_code
    state["completed_at"] = time.time()

    # Extract halt payload from the session checkpoint and flush to state.json
    try:
        project_root = str(Path(job_dir).parent.parent.parent)
        from snodo.infrastructure.state import read_state
        pstate = read_state(project_root)
        mode = pstate.current_mode
        if mode:
            from snodo.infrastructure.session import SessionManager
            sm = SessionManager()
            session = sm.get_active_session(mode, project_root)
            if session:
                halt = session.checkpoint.decisions.get("halt", {})
                if isinstance(halt, dict) and halt:
                    task_ids = list(halt.keys())
                    state["halt"] = halt[task_ids[0]] if len(task_ids) == 1 else halt
                classifications = session.checkpoint.decisions.get("classification", {})
                if isinstance(classifications, dict) and classifications:
                    task_ids = list(classifications.keys())
                    entry = classifications[task_ids[0]] if len(task_ids) == 1 else classifications
                    if isinstance(entry, dict):
                        if entry.get("flow_type"):
                            state["flow_type"] = entry["flow_type"]
                        if entry.get("wave_id"):
                            state["wave_id"] = entry["wave_id"]
    except Exception:
        pass

    _save_state(job_dir, state)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
