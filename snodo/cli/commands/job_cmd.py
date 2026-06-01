"""Job command - Manage async background jobs.

FILE: snodo/cli/commands/job_cmd.py
"""

import sys
import time
from pathlib import Path


def job_command(args) -> int:
    """Manage background jobs."""
    from snodo.jobs import JobManager, JobError

    project_root = str(Path.cwd())

    try:
        manager = JobManager(project_root)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    action = args.job_action
    try:
        if action == "list":
            return _job_list(manager)
        elif action == "status":
            return _job_status(manager, args.job_id)
        elif action == "logs":
            stream = getattr(args, "stream", "stdout")
            tail = getattr(args, "tail", None)
            return _job_logs(manager, args.job_id, stream, tail)
        elif action == "wait":
            timeout = getattr(args, "timeout", None)
            return _job_wait(manager, args.job_id, timeout)
        elif action == "cancel":
            return _job_cancel(manager, args.job_id)
        else:
            print("Unknown job action. Use: list, status, logs, wait, cancel", file=sys.stderr)
            return 1
    except JobError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def _job_list(manager) -> int:
    """List all jobs."""
    jobs = manager.list_jobs()
    if not jobs:
        print("No jobs found.")
        return 0

    # Table header
    print(f"{'ID':<12} {'Status':<12} {'Created':<20} {'Description'}")
    print("-" * 72)
    for job in jobs:
        desc = job["description"]
        if len(desc) > 40:
            desc = desc[:37] + "..."
        created = _format_time(job["created_at"])
        print(f"{job['id']:<12} {job['status']:<12} {created:<20} {desc}")
    return 0


def _job_status(manager, job_id: str) -> int:
    """Show full job status."""
    status = manager.get_status(job_id)
    task = status.get("task", {})

    print(f"Job: {status['id']}")
    print(f"Status: {status.get('status', 'unknown')}")
    print(f"PID: {status.get('pid', 'N/A')}")
    print(f"Created: {_format_time(status.get('created_at'))}")

    started = status.get("started_at")
    if started:
        print(f"Started: {_format_time(started)}")

    completed = status.get("completed_at")
    if completed:
        print(f"Completed: {_format_time(completed)}")

    exit_code = status.get("exit_code")
    if exit_code is not None:
        print(f"Exit code: {exit_code}")

    print()
    if task.get("description"):
        print(f"Description: {task['description']}")
    if task.get("protocol"):
        print(f"Protocol: {task['protocol']}")
    if task.get("model"):
        print(f"Model: {task['model']}")
    if task.get("mock"):
        print("Mock: yes")

    return 0


def _job_logs(manager, job_id: str, stream: str, tail) -> int:
    """Show job logs."""
    content = manager.get_logs(job_id, stream=stream, tail=tail)
    if content:
        print(content, end="")
    else:
        print(f"(no {stream} output)")
    return 0


def _job_wait(manager, job_id: str, timeout) -> int:
    """Wait for job completion, then print status."""
    from snodo.jobs import JobError

    print(f"Waiting for job {job_id}...")
    try:
        status = manager.wait_for(job_id, timeout=timeout)
    except JobError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(f"Job {job_id}: {status.get('status')}")
    exit_code = status.get("exit_code", 1)
    if exit_code is not None:
        print(f"Exit code: {exit_code}")

    return exit_code if isinstance(exit_code, int) else 1


def _job_cancel(manager, job_id: str) -> int:
    """Cancel a running job."""
    manager.cancel(job_id)
    print(f"Job {job_id} cancelled.")
    return 0


def _format_time(ts) -> str:
    """Format a timestamp for display."""
    if not ts:
        return "N/A"
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
    except (TypeError, ValueError, OSError):
        return "N/A"
