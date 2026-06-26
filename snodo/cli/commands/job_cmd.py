"""Job command - Manage async background jobs.

FILE: snodo/cli/commands/job_cmd.py
"""

import sys
import time


def job_command(args) -> int:
    """Manage background jobs."""
    from snodo.jobs import JobManager, JobError
    from snodo.infrastructure.paths import require_project_root

    project_root = require_project_root()

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
            watch = getattr(args, "watch", False)
            return _job_logs(manager, args.job_id, stream, tail, watch)
        elif action == "wait":
            timeout = getattr(args, "timeout", None)
            return _job_wait(manager, args.job_id, timeout)
        elif action == "cancel":
            return _job_cancel(manager, args.job_id)
        elif action == "archive":
            return _job_archive(manager, args)
        elif action == "prune":
            return _job_prune(manager, args)
        elif action == "unarchive":
            return _job_unarchive(manager, args)
        elif action == "retry":
            return _job_retry(manager, args)
        else:
            print("Unknown job action. Use: list, status, logs, wait, cancel, archive, prune, unarchive, retry", file=sys.stderr)
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


def _job_logs(manager, job_id: str, stream: str, tail, watch: bool = False) -> int:
    """Show job logs, optionally tailing in real time with --watch."""
    if watch:
        return _job_logs_watch(manager, job_id, stream)
    content = manager.get_logs(job_id, stream=stream, tail=tail)
    if content:
        print(content, end="")
    else:
        print(f"(no {stream} output)")
    return 0


def _job_logs_watch(manager, job_id: str, stream: str) -> int:
    """Tail job logs in real time, exiting when job reaches terminal status."""
    from snodo.jobs import TERMINAL_STATUSES

    job_dir = manager._job_dir(job_id)
    log_path = job_dir / f"{stream}.log"

    if not log_path.exists():
        print(f"(no {stream} output — file not created yet)")
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
                        status = manager.get_status(job_id)
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


def _job_archive(manager, args) -> int:
    """Archive old terminal jobs."""
    days = getattr(args, "days", 10)
    skip_prompt = getattr(args, "yes", False)
    try:
        archived = manager.archive_jobs(older_than_days=days, dry_run=True)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    if not archived:
        print(f"No terminal jobs older than {days} days to archive.")
        return 0
    print(f"Will archive {len(archived)} job(s):")
    for jid in archived[:10]:
        print(f"  {jid}")
    if len(archived) > 10:
        print(f"  ... and {len(archived) - 10} more")
    if not skip_prompt:
        try:
            answer = input(f"Archive {len(archived)} job(s)? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return 1
        if answer != "y":
            print("Aborted.")
            return 0
    try:
        archived = manager.archive_jobs(older_than_days=days)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(f"Archived {len(archived)} jobs to .snodo/jobs_archive/")
    return 0


def _job_prune(manager, args) -> int:
    """Prune (delete) old terminal jobs."""
    days = getattr(args, "days", 10)
    skip_prompt = getattr(args, "yes", False)
    try:
        to_prune = manager.prune_jobs(older_than_days=days, dry_run=True)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    if not to_prune:
        print(f"No terminal jobs older than {days} days to prune.")
        return 0
    print(f"Will delete {len(to_prune)} job(s):")
    for jid in to_prune[:10]:
        print(f"  {jid}")
    if len(to_prune) > 10:
        print(f"  ... and {len(to_prune) - 10} more")
    if not skip_prompt:
        try:
            answer = input(f"Delete {len(to_prune)} job(s)? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return 1
        if answer != "y":
            print("Aborted.")
            return 0
    try:
        pruned = manager.prune_jobs(older_than_days=days)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(f"Pruned {len(pruned)} job(s)")
    return 0


def _job_unarchive(manager, args) -> int:
    """Restore archived jobs."""
    days = getattr(args, "days", 12)
    skip_prompt = getattr(args, "yes", False)
    try:
        to_restore = manager.unarchive_jobs(within_days=days, dry_run=True)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    if not to_restore:
        print(f"No archived jobs within {days} days to restore.")
        return 0
    print(f"Will restore {len(to_restore)} job(s):")
    for jid in to_restore[:10]:
        print(f"  {jid}")
    if len(to_restore) > 10:
        print(f"  ... and {len(to_restore) - 10} more")
    if not skip_prompt:
        try:
            answer = input(f"Restore {len(to_restore)} job(s)? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return 1
        if answer != "y":
            print("Aborted.")
            return 0
    try:
        restored = manager.unarchive_jobs(within_days=days)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(f"Restored {len(restored)} jobs")
    return 0


def _job_retry(manager, args) -> int:
    """Retry the task associated with a failed job."""
    import json

    job_id = getattr(args, "job_id", "")
    revised_spec = getattr(args, "description", "")
    if not job_id:
        print("Error: job_id is required", file=sys.stderr)
        return 1

    # Read task.json to get the original task_id
    job_dir = manager._job_dir(job_id)
    task_path = job_dir / "task.json"
    if not task_path.exists():
        print(f"No task.json found for job {job_id}", file=sys.stderr)
        return 1
    try:
        with open(task_path) as f:
            task_data = json.load(f)
    except Exception as e:
        print(f"Error reading task.json: {e}", file=sys.stderr)
        return 1

    task_id = task_data.get("task_id", "")
    if not task_id:
        description = task_data.get("description", "")
        if not description:
            print(f"No task_id or description in task.json for job {job_id}.",
                  file=sys.stderr)
            return 1
        print(f"No task_id found. Original task: {description[:200]}")
        try:
            answer = input("Dispatch as new task instead? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.")
            return 1
        if answer != "y":
            print("Cannot retry — job predates task tracking.")
            return 1
        return _dispatch_as_new_task(args, task_data, job_id)

    # Build retry args with full context for _retry_task
    from types import SimpleNamespace
    from snodo.infrastructure.audit import get_audit_log
    from snodo.infrastructure.session import SessionManager
    from snodo.infrastructure.paths import require_project_root

    project_root = require_project_root()
    audit_log = get_audit_log()
    session_manager = SessionManager(audit_log=audit_log)

    retry_args = SimpleNamespace(
        description=revised_spec,
        protocol=getattr(args, "protocol", ".snodo/protocol.yml"),
        model=getattr(args, "model", None),
        audit_log=audit_log,
        session_manager=session_manager,
    )

    from snodo.cli.commands.run_cmd import _retry_task
    return _retry_task(retry_args, task_id, project_root, session_manager)


def _dispatch_as_new_task(args, task_data: dict, job_id: str) -> int:
    """Dispatch old job as a new task when task_id is missing."""
    from snodo.infrastructure.audit import get_audit_log
    from snodo.infrastructure.session import SessionManager
    from snodo.infrastructure.paths import require_project_root
    from snodo.config import ConfigManager, provider_env
    from snodo.cli.commands import load_protocol
    from snodo.core.interfaces import Task

    description = getattr(args, "description", "") or task_data.get("description", "")
    project_root = require_project_root()
    audit_log = get_audit_log()
    session_manager = SessionManager(audit_log=audit_log)

    protocol_path = getattr(args, "protocol", ".snodo/protocol.yml")
    from pathlib import Path
    protocol = load_protocol(Path(project_root) / protocol_path if not protocol_path.startswith("/") else Path(protocol_path))
    if not protocol:
        return 1

    mgr = ConfigManager()
    model = getattr(args, "model", None) or mgr.get_model()

    task_id = f"task_{hash(description) & 0xffffff:06x}"
    task = Task(id=task_id, spec=description)

    from snodo.cli.commands.run_cmd import _execute_task
    from types import SimpleNamespace as NS

    exec_args = NS(
        description=description,
        protocol=protocol_path,
        model=model,
        mock=False,
        verbose=False,
        background=False,
        plan=None,
        retry=None,
        sandbox="local",
        from_pr=None,
        resume=None,
        audit_log=audit_log,
        session_manager=session_manager,
    )

    print(f"Dispatched as new task {task_id}")
    with provider_env(model):
        return _execute_task(exec_args, protocol, task, model)


def _format_time(ts) -> str:
    """Format a timestamp for display."""
    if not ts:
        return "N/A"
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
    except (TypeError, ValueError, OSError):
        return "N/A"
