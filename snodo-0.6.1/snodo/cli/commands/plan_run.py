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


def _task_is_blocked(tasks_status: dict, task_id: str) -> bool:
    """Check if a task is marked blocked, handling both string and dict entries."""
    entry = tasks_status.get(task_id)
    if isinstance(entry, dict):
        return entry.get("status") == "blocked"
    return entry == "blocked"


def _resolve_failure_context(session, task_id: str) -> Optional[dict]:
    """Resolve retry failure context for *task_id*, mirroring ``_retry_task``.

    Prefers ``decisions["task_failure"][task_id]`` and falls back to the
    persisted halt record via ``_failure_from_halt_record`` — the same
    resolution ``_retry_task`` uses, reused rather than reimplemented. Returns
    None when neither source yields a dict.
    """
    from snodo.cli.commands.run_cmd import _failure_from_halt_record

    task_failure = session.checkpoint.decisions.get("task_failure", {})
    if not isinstance(task_failure, dict):
        task_failure = {}
    failure = task_failure.get(task_id)
    if not isinstance(failure, dict):
        failure = _failure_from_halt_record(session, task_id)
    return failure


def _plan_retry_decision(planner, args, protocol, task_id: str) -> str:
    """Decide how a blocked task resumes: ``retry``, ``exhausted``, or ``fresh``.

    Mirrors ``_retry_task``'s context resolution and ``max_retries`` handling:
    - ``retry``: failure context exists and retries remain — execute as a retry.
    - ``exhausted``: failure context exists but ``max_retries`` is reached — do
      not re-execute; prints the abandon/override guidance.
    - ``fresh``: no failure context (or no session manager) — run the task
      fresh, today's behaviour.
    """
    session_manager = getattr(args, "session_manager", None)
    if session_manager is None:
        return "fresh"

    from snodo.infrastructure.state import read_state
    project_root = str(planner.project_root)
    state = read_state(project_root)
    mode = state.current_mode or protocol.initial_mode

    session = session_manager.get_active_session(mode, project_root)
    if session is None:
        return "fresh"

    failure = _resolve_failure_context(session, task_id)
    if failure is None:
        return "fresh"

    attempt = failure.get("attempt", 0)
    max_retries = getattr(protocol.execution, "max_retries", 3)
    if attempt >= max_retries:
        print(f"Task {task_id} has failed {max_retries} times.")
        print(f"  Review branch {failure.get('branch', 'unknown')} and either:")
        print(f"  - snodo run --retry {task_id} \"revised spec\" (override spec)")
        print(f"  - snodo task abandon {task_id} (delete branch)")
        return "exhausted"
    return "retry"


def _get_completed_waves(waves: list, tasks_status: dict) -> set:
    """Determine which waves are fully completed.

    Args:
        waves: All waves from plan data
        tasks_status: Task status mapping

    Returns:
        Set of completed wave IDs (stored as both str and raw values)
    """
    completed = set()
    for wave in waves:
        wid = wave.get("id")
        wave_tasks = wave.get("tasks", [])
        if wave_tasks and all(_task_completed(tasks_status, str(t)) or _task_completed(tasks_status, t) for t in wave_tasks):
            completed.add(wid)
            completed.add(str(wid))
    return completed


def _execute_wave_task(planner, args, protocol, model, wave_id, task_id) -> bool:
    """Execute a single task within a wave.

    A task the status file already marks "blocked" resumes through the retry
    path when failure context exists (reusing ``_retry_task``'s resolution):
    it is executed as a retry rather than a fresh dispatch, so the failure
    context ``_auto_write_failure_context`` persists is consumed. A task at
    ``max_retries`` is not re-executed. With no failure context, the task runs
    fresh (today's behaviour) and the line says so.

    Returns:
        True on success, False on failure.
    """
    from snodo.cli.commands.run_cmd import _execute_task

    wave_dir = planner.plans_dir / args.plan / f"wave_{wave_id}"
    spec_file = wave_dir / f"{task_id}_task.md"
    if not spec_file.exists():
        planner.update_status(args.plan, task_id, "blocked")
        print(f"  [{task_id}] ERROR: spec file not found", file=sys.stderr)
        return False

    spec = spec_file.read_text()

    status_data = planner.get_status(args.plan)
    tasks_status = status_data.get("tasks", {})
    if _task_is_blocked(tasks_status, task_id):
        decision = _plan_retry_decision(planner, args, protocol, task_id)
        if decision == "exhausted":
            print(f"  [{task_id}] not re-executed (max_retries reached)")
            return False
        if decision == "retry":
            from snodo.cli.commands.run_cmd import _retry_task
            project_root = str(planner.project_root)
            session_manager = getattr(args, "session_manager", None)
            print(f"  [{task_id}] resuming as retry (failure context found)")
            result = _retry_task(args, task_id, project_root, session_manager)
            if result == 0:
                planner.update_status(args.plan, task_id, "completed")
                return True
            planner.update_status(args.plan, task_id, "blocked")
            print(f"  [{task_id}] FAILED", file=sys.stderr)
            return False
        # decision == "fresh": no failure context — fall through to fresh run
        print(f"  [{task_id}] no failure context found; running fresh")

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
    filtered = [w for w in waves if str(w.get("id")) == str(wave_filter)]
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
                   all_waves, interactive) -> bool:
    """Execute waves in order, respecting dependencies.

    Returns:
        True if any task failed or wave was blocked, False if all succeeded.
    """
    has_failed_or_blocked = False
    for wave in waves:
        status_data = planner.get_status(args.plan)
        tasks_status = status_data.get("tasks", {})
        completed_waves = _get_completed_waves(all_waves, tasks_status)

        wave_id = wave.get("id")
        deps = wave.get("depends_on", [])

        unmet = [d for d in deps if d not in completed_waves and str(d) not in completed_waves]
        if unmet:
            print(f"Wave {wave_id}: blocked (depends on: {', '.join(str(d) for d in unmet)})")
            has_failed_or_blocked = True
            continue

        print(f"Wave {wave_id}:")
        for task_id in wave.get("tasks", []):
            if _should_skip_task(task_id, tasks_status, interactive):
                continue
            if not _execute_wave_task(planner, args, protocol, model, wave_id, task_id):
                return True  # failed

    return has_failed_or_blocked


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
            from snodo.compiler.models import Plan
            plan_model = Plan.from_dict(plan_data, status_data)
        except (ValueError, PlannerError, Exception) as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

        plan_dir = planner.plans_dir / args.plan
        from snodo.compiler.verifier import verify_plan
        verification = verify_plan(plan_model, plan_dir=plan_dir)

        if verification.warnings:
            print("Warnings:", file=sys.stderr)
            for w in verification.warnings:
                print(f"  - {w}", file=sys.stderr)

        if not verification.passed:
            print(f"Error: Plan verification failed for '{args.plan}':", file=sys.stderr)
            for err in verification.errors:
                print(f"  - {err}", file=sys.stderr)
            return 1

        print(f"Plan: {plan_data.get('name', args.plan)}")
        print(f"Intent: {plan_data.get('intent', 'N/A')}")
        print()

        all_waves = plan_data.get("waves", [])
        waves = _filter_waves(all_waves, getattr(args, "wave", None))
        if waves is None:
            return 1

        interactive = getattr(args, "interactive", False)

        failed = _execute_waves(waves, planner, args, protocol, model,
                                all_waves, interactive)

        _print_plan_progress(planner, args.plan)
        return 1 if failed else 0
