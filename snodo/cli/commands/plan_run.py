"""Plan execution helpers for the snodo run command.

Extracted from cli/commands/run_cmd.py to isolate plan execution logic.
"""

import sys
from pathlib import Path
from typing import Optional

from snodo.core.interfaces import Task
from snodo.config import ConfigManager, provider_env
from snodo.cli.commands import load_protocol


def _task_completed(tasks_status: dict, task_id: str) -> bool:
    """Check if a task is completed, handling both string and dict entries."""
    entry = tasks_status.get(task_id)
    if isinstance(entry, dict):
        return entry.get("status") == "completed"
    return entry == "completed"


def _get_completed_waves(waves: list, tasks_status: dict) -> set:
    """Determine which waves are fully completed.

    Args:
        waves: All waves from plan data
        tasks_status: Task status mapping

    Returns:
        Set of completed wave IDs
    """
    completed = set()
    for wave in waves:
        wid = wave.get("id")
        wave_tasks = wave.get("tasks", [])
        if wave_tasks and all(_task_completed(tasks_status, t) for t in wave_tasks):
            completed.add(wid)
    return completed


def _execute_wave_task(planner, args, protocol, model, wave_id, task_id) -> bool:
    """Execute a single task within a wave.

    Returns:
        True on success, False on failure.
    """
    from snodo.cli.commands.run_cmd import _execute_task

    wave_dir = planner.plans_dir / args.plan / f"wave_{wave_id}"
    spec_file = wave_dir / f"{task_id}_task.md"
    if not spec_file.exists():
        print(f"  [{task_id}] ERROR: spec file not found", file=sys.stderr)
        return False

    spec = spec_file.read_text()
    planner.update_status(args.plan, task_id, "in_progress")

    task = Task(id=task_id, spec=spec)
    print(f"  [{task_id}] executing...")
    result = _execute_task(args, protocol, task, model)

    if result == 0:
        planner.update_status(args.plan, task_id, "completed")
        return True
    else:
        planner.update_status(args.plan, task_id, "blocked")
        print(f"  [{task_id}] FAILED", file=sys.stderr)
        return False


def _filter_waves(waves: list, wave_filter) -> Optional[list]:
    """Filter waves by ID. Returns None on error."""
    if wave_filter is None:
        return waves
    filtered = [w for w in waves if w.get("id") == wave_filter]
    if not filtered:
        print(f"Error: Wave {wave_filter} not found in plan", file=sys.stderr)
        return None
    return filtered


def _should_skip_task(task_id, tasks_status, interactive) -> bool:
    """Check if a task should be skipped (completed or user declined).

    Returns:
        True if the task should be skipped.
    """
    if _task_completed(tasks_status, task_id):
        print(f"  [{task_id}] skipped (completed)")
        return True
    if interactive:
        answer = input(f"  Execute {task_id}? [y/N] ").strip().lower()
        if answer != "y":
            print(f"  [{task_id}] skipped (user)")
            return True
    return False


def _execute_waves(waves, planner, args, protocol, model,
                   tasks_status, completed_waves, interactive) -> bool:
    """Execute waves in order, respecting dependencies.

    Returns:
        True if any task failed, False if all succeeded.
    """
    for wave in waves:
        wave_id = wave.get("id")
        deps = wave.get("depends_on", [])

        unmet = [d for d in deps if d not in completed_waves]
        if unmet:
            print(f"Wave {wave_id}: blocked (depends on: {', '.join(str(d) for d in unmet)})")
            continue

        print(f"Wave {wave_id}:")
        for task_id in wave.get("tasks", []):
            if _should_skip_task(task_id, tasks_status, interactive):
                continue
            if not _execute_wave_task(planner, args, protocol, model, wave_id, task_id):
                return True  # failed

    return False


def _print_plan_progress(planner, plan_name: str) -> None:
    """Print final plan progress."""
    status_data = planner.get_status(plan_name)
    tasks = status_data.get("tasks", {})
    done = sum(1 for s in tasks.values()
               if (s.get("status") if isinstance(s, dict) else s) == "completed")
    print(f"\nPlan progress: {done}/{len(tasks)} completed")


def _run_plan(args) -> int:
    """Execute a plan's tasks through the protocol loop."""
    from snodo.mcp.planner import PlannerMCP, PlannerError

    protocol_path = Path(args.protocol)
    protocol = load_protocol(protocol_path)
    if not protocol:
        return 1

    mgr = ConfigManager()
    model = args.model or mgr.get_model()

    with provider_env(model) as mgr:
        try:
            from snodo.infrastructure.paths import require_project_root
            project_root = require_project_root()
            audit_log = getattr(args, "audit_log", None)
            planner = PlannerMCP(project_root, audit_log=audit_log)
            plan_data = planner.get_plan(args.plan)
            status_data = planner.get_status(args.plan)
        except (ValueError, PlannerError) as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

        print(f"Plan: {plan_data.get('name', args.plan)}")
        print(f"Intent: {plan_data.get('intent', 'N/A')}")
        print()

        waves = _filter_waves(plan_data.get("waves", []), getattr(args, "wave", None))
        if waves is None:
            return 1

        tasks_status = status_data.get("tasks", {})
        completed_waves = _get_completed_waves(plan_data.get("waves", []), tasks_status)
        interactive = getattr(args, "interactive", False)

        failed = _execute_waves(waves, planner, args, protocol, model,
                                tasks_status, completed_waves, interactive)

        _print_plan_progress(planner, args.plan)
        return 1 if failed else 0
