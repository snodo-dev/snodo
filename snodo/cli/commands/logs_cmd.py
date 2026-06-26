"""Unified logs command — handles both job IDs and recon IDs.

FILE: snodo/cli/commands/logs_cmd.py
"""

import sys
import time
from types import SimpleNamespace

import typer


def register(app: typer.Typer) -> None:
    """Register top-level CLI commands onto app (called by discovery loop)."""

    @app.command()
    def logs(
        composite_id: str = typer.Argument(..., help="Job ID (j_xxx) or Recon ID (rec_xxx)"),
        watch: bool = typer.Option(False, "--watch", "-w", help="Tail job logs in real time until job completes"),
    ):
        """Show output for a job or recon by ID."""
        args = SimpleNamespace(composite_id=composite_id, watch=watch)
        return logs_command(args)



def logs_command(args) -> int:
    """Show output for a job or recon by ID."""
    from snodo.infrastructure.paths import require_project_root

    composite_id = getattr(args, "composite_id", "")
    if not composite_id:
        print("Error: <id> is required", file=sys.stderr)
        return 1

    project_root = require_project_root()

    if composite_id.startswith("rec_"):
        return _show_recon(project_root, composite_id)
    if composite_id.startswith("j_"):
        return _show_job(project_root, composite_id, args)

    # Try job first, then recon
    if _job_exists(project_root, composite_id):
        return _show_job(project_root, composite_id, args)
    if _recon_exists(project_root, composite_id):
        return _show_recon(project_root, composite_id)

    print(f"Error: {composite_id!r} not found as a job or recon ID.",
          file=sys.stderr)
    return 1


def _job_exists(project_root: str, job_id: str) -> bool:
    from snodo.jobs import JobManager
    try:
        mgr = JobManager(project_root)
        mgr._job_dir(job_id)
        return True
    except Exception:
        return False


def _recon_exists(project_root: str, recon_id: str) -> bool:
    from pathlib import Path
    recon_dir = Path(project_root) / ".snodo" / "recons" / recon_id
    return recon_dir.is_dir()


def _show_job(project_root: str, job_id: str, args) -> int:
    """Show job stdout, delegating to the existing job handler."""
    from snodo.jobs import JobManager

    mgr = JobManager(project_root)
    watch = getattr(args, "watch", False)

    if watch:
        from snodo.jobs import TERMINAL_STATUSES
        job_dir = mgr._job_dir(job_id)
        log_path = job_dir / "stdout.log"
        if not log_path.exists():
            print("(no stdout output — file not created yet)")
            return 1
        try:
            with open(log_path) as f:
                f.seek(0)
                while True:
                    line = f.readline()
                    if line:
                        print(line, end="", flush=True)
                    else:
                        try:
                            status = mgr.get_status(job_id)
                            if status.get("status") in TERMINAL_STATUSES:
                                while True:
                                    line = f.readline()
                                    if line:
                                        print(line, end="", flush=True)
                                    else:
                                        break
                                break
                        except Exception:
                            pass
                        time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        return 0

    content = mgr.get_logs(job_id)
    if content:
        print(content, end="")
    else:
        print("(no stdout output)")
    return 0


def _show_recon(project_root: str, recon_id: str) -> int:
    """Show recon results from results.json."""
    from pathlib import Path
    import json

    recon_dir = Path(project_root) / ".snodo" / "recons" / recon_id
    if not recon_dir.is_dir():
        print(f"Error: Recon not found: {recon_id}", file=sys.stderr)
        return 1

    state_path = recon_dir / "state.json"
    results_path = recon_dir / "results.json"

    state = {}
    if state_path.exists():
        try:
            with open(state_path) as f:
                state = json.load(f)
        except Exception:
            pass

    results = []
    if results_path.exists():
        try:
            with open(results_path) as f:
                results = json.load(f)
        except Exception:
            pass

    query = state.get("query", "—")
    status = state.get("status", "—")
    agents = state.get("agents", [])

    print(f"Recon: {recon_id}")
    print(f"Query: {query}")
    print(f"Status: {status}")
    if agents:
        print(f"Agents: {', '.join(agents)}")
    print()

    for r in results:
        agent = r.get("agent", "—")
        model = r.get("model", "—")
        result_text = r.get("result", "").strip()
        error = r.get("error", "")

        print(f"--- {agent} ({model}) ---")
        if error:
            print(f"Error: {error}")
        if result_text:
            print(result_text)
        else:
            print("(empty result)")
        print()

    if not results:
        print("No results yet.")

    return 0
