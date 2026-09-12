"""Plan execution helpers for the snodo run command.

Extracted from cli/commands/run_cmd.py to isolate plan execution logic.
"""

import json
import sys
import time
from pathlib import Path
from typing import Optional

from snodo.core.interfaces import Task
from snodo.config import ConfigManager, provider_env
from snodo.cli.commands import load_protocol
from snodo.cli.commands import followup


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


_HALT_OUTCOME_LABELS = {
    "escalate": "escalated",
    "blocker": "blocked",
    "validator_error": "validator_error",
    "internal_error": "internal_error",
}


def _halt_outcome_from_task_state(session, task_id: str) -> Optional[str]:
    """Return the engine's canonical halt outcome for *task_id* from job state.

    Reads the job state.json records (the engine's own writes, produced by
    ``WritebackMixin._auto_write_halt_payload``) for the task. The outcome is
    the canonical ``final_decision`` / ``halt_type`` — one of the four-outcome
    vocabulary (escalate, blocker, validator_error, internal_error). Returns
    None when no matching job halt was recorded.
    """
    project_root = str(getattr(session, "project_root", "") or "")
    jobs_dir = Path(project_root) / ".snodo" / "jobs"
    if jobs_dir.is_dir():
        for job_path in sorted(jobs_dir.iterdir()):
            state_path = job_path / "state.json"
            if not state_path.is_file():
                continue
            try:
                data = json.loads(state_path.read_text())
            except (OSError, ValueError, TypeError):
                continue
            if not isinstance(data, dict):
                continue
            halt = data.get("halt")
            if not isinstance(halt, dict) or halt.get("task_id") != task_id:
                continue
            decision = halt.get("final_decision") or halt.get("halt_type")
            if decision in _HALT_OUTCOME_LABELS:
                return decision
    return None


def _halt_outcome_from_session(session, task_id: str) -> Optional[str]:
    """Return the engine's canonical halt outcome for *task_id* from the session.

    Falls back to the session checkpoint's ``decisions["halt"]`` record when no
    job record exists. Returns None when no halt was recorded.
    """
    if session is None:
        return None
    try:
        halt = session.checkpoint.decisions.get("halt", {})
    except Exception:
        return None
    if not isinstance(halt, dict):
        return None
    record = halt.get(task_id)
    if not isinstance(record, dict):
        return None
    decision = record.get("final_decision") or record.get("halt_type")
    if decision in _HALT_OUTCOME_LABELS:
        return decision
    return None


def _halt_outcome(session, task_id: str) -> Optional[str]:
    """Return the engine's canonical halt outcome for *task_id*.

    Prefers the persisted job state, then the session checkpoint. None when no
    halt was recorded — an outcome that is not one of the four (e.g. the halt
    record is entirely absent) keeps today's generic classification.
    """
    outcome = _halt_outcome_from_task_state(session, task_id)
    if outcome is None:
        outcome = _halt_outcome_from_session(session, task_id)
    return outcome


def _task_record_status(planner, plan: str, task_id: str, exit_code: int, session) -> str:
    """Record the plan status for a finished task, honoring the engine outcome.

    The status the plan layer records must not lose the operator-facing
    distinction the engine already computed: a task that FAILED judgement is
    ``blocked`` (next attempt retries with the failure context), while a task
    that was never judged, or whose judged work could not be merged, is
    ``errored`` — a different state that must not hand its halt reason to a
    faultless coder as a critique (issue #231).

    Returns the status actually recorded.
    """
    outcome = _halt_outcome(session, task_id)
    if outcome in ("validator_error", "internal_error"):
        planner.update_status(plan, task_id, "errored")
        return "errored"
    if outcome in ("escalate", "blocker"):
        planner.update_status(plan, task_id, "blocked")
        return "blocked"
    planner.update_status(plan, task_id, "blocked")
    return "blocked"


def _task_outcome_line(task_id: str, outcome: Optional[str]) -> str:
    """Render the named outcome for *task_id*, in operator-actionable terms.

    Backs onto the engine's four-outcome vocabulary so the label can never
    disagree with what the engine classified: ``blocked`` (failed judgement —
    re-run after addressing the concerns), ``escalated`` (needs a human
    decision — authorize), ``validator_error`` / ``internal_error``
    (operational faults — re-run, do not retry the task), and a missing-halt
    fallback that says the run failed without a recorded outcome.
    """
    if outcome is None:
        return f"[{task_id}] FAILED in"
    labels = {
        "escalate": f"[{task_id}] ESCALATED in",
        "blocker": f"[{task_id}] BLOCKED in",
        "validator_error": f"[{task_id}] VALIDATOR ERROR in",
        "internal_error": f"[{task_id}] INTERNAL ERROR in",
    }
    return labels[outcome]

def _task_is_unmerged(tasks_status: dict, task_id: str) -> bool:
    """Check if a task is marked unmerged, handling both string and dict entries."""
    entry = tasks_status.get(task_id)
    if isinstance(entry, dict):
        return entry.get("status") == "unmerged"
    return entry == "unmerged"


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
        for option in followup.task_retry_options(task_id):
            print(f"  - {option}")
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


def _format_duration(seconds: float) -> str:
    """Format duration in seconds as a human-readable string with s, m, h buckets."""
    if seconds < 0:
        seconds = 0.0
    if seconds < 60:
        return f"{seconds:.1f}s"
    if seconds < 3600:
        minutes = int(seconds // 60)
        rem_s = seconds % 60
        return f"{minutes}m {rem_s:.1f}s"
    hours = int(seconds // 3600)
    rem_minutes = int((seconds % 3600) // 60)
    rem_s = seconds % 60
    return f"{hours}h {rem_minutes}m {rem_s:.1f}s"


def _format_timestamp(ts: Optional[float]) -> str:
    """Format a timestamp as YYYY-MM-DD HH:MM:SS."""
    if not ts:
        return "N/A"
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
    except (TypeError, ValueError, OSError):
        return "N/A"


def _session_for_task(args, planner, protocol, task_id: str):
    """Resolve the active session whose checkpoint holds *task_id*'s halt record.

    Mirrors ``_plan_retry_decision``'s session resolution so the outcome read
    and the retry decision read the same session, and so a failed-task lookup
    with no session manager (e.g. inline tests) degrades to no record — the
    generic classification.

    Returns:
        The active session, or None.
    """
    session_manager = getattr(args, "session_manager", None)
    if session_manager is None:
        return None
    from snodo.infrastructure.state import read_state
    project_root = str(planner.project_root)
    state = read_state(project_root)
    mode = state.current_mode or protocol.initial_mode
    return session_manager.get_active_session(mode, project_root)


def _execute_wave_task(planner, args, protocol, model, wave_id, task_id) -> bool:
    """Execute a single task within a wave.

    A task the status file already marks "blocked" resumes through the retry
    path when failure context exists (reusing ``_retry_task``'s resolution):
    it is executed as a retry rather than a fresh dispatch, so the failure
    context ``_auto_write_failure_context`` persists is consumed. A task at
    ``max_retries`` is not re-executed. With no failure context, the task runs
    fresh (today's behaviour) and the line says so.

    A failed task is recorded by the outcome the engine decided — the persisted
    halt payload's ``final_decision`` — not by the exit code alone: a task that
    FAILED judgement is recorded ``blocked`` (and retries with failure context),
    while an operational fault (``validator_error`` / ``internal_error``, which
    means the work was never judged) is recorded ``errored`` and must not hand
    the halt reason to a faultless coder on the next attempt (issue #231).

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
    if _task_is_unmerged(tasks_status, task_id):
        print(f"  [{task_id}] unmerged branch found; attempting fast-path merge")
        start_mono = time.monotonic()
        start_wall = time.time()
        audit_log = getattr(args, "audit_log", None)
        if audit_log is None:
            from snodo.infrastructure.audit import AuditLog
            audit_log_path = planner.project_root / ".snodo" / "audit.log"
            if audit_log_path.exists():
                audit_log = AuditLog(str(audit_log_path))
        session_manager = getattr(args, "session_manager", None)
        from snodo.infrastructure.state import read_state
        state = read_state(str(planner.project_root))
        mode = getattr(args, "mode", None) or state.current_mode or protocol.initial_mode
        session = session_manager.get_active_session(mode, str(planner.project_root)) if session_manager else None
        session_id = session.session_id if session else None

        from snodo.cli.commands.run_cmd import _try_merge_unmerged_task
        merge_success = _try_merge_unmerged_task(
            str(planner.project_root),
            task_id,
            spec,
            protocol=protocol,
            session_id=session_id,
            audit_log=audit_log,
        )
        end_mono = time.monotonic()
        end_wall = time.time()
        dur_str = _format_duration(end_mono - start_mono)
        start_str = _format_timestamp(start_wall)
        end_str = _format_timestamp(end_wall)

        if merge_success is True:
            planner.update_status(args.plan, task_id, "completed")
            print(f"  [{task_id}] completed in {dur_str} (started {start_str}, finished {end_str})")
            return True
        elif merge_success is False:
            planner.update_status(args.plan, task_id, "unmerged")
            print(
                f"  [{task_id}] complete (unmerged) in {dur_str} (started {start_str}, finished {end_str})",
                file=sys.stderr,
            )
            return False
        print(f"  [{task_id}] unmerged branch not found or unverified; running fresh")

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
            start_mono = time.monotonic()
            start_wall = time.time()
            result = _retry_task(args, task_id, project_root, session_manager)
            end_mono = time.monotonic()
            end_wall = time.time()
            dur_str = _format_duration(end_mono - start_mono)
            start_str = _format_timestamp(start_wall)
            end_str = _format_timestamp(end_wall)
            if result == 0:
                planner.update_status(args.plan, task_id, "completed")
                print(f"  [{task_id}] completed in {dur_str} (started {start_str}, finished {end_str})")
                return True
            session = _session_for_task(args, planner, protocol, task_id)
            _task_record_status(planner, args.plan, task_id, result, session)
            print(
                f"{_task_outcome_line(task_id, _halt_outcome(session, task_id))} "
                f"{dur_str} (started {start_str}, finished {end_str})",
                file=sys.stderr,
            )
            return False
        # decision == "fresh": no failure context — fall through to fresh run
        print(f"  [{task_id}] no failure context found; running fresh")

    planner.update_status(args.plan, task_id, "in_progress")

    task = Task(id=task_id, spec=spec)
    print(f"  [{task_id}] executing...")
    start_mono = time.monotonic()
    start_wall = time.time()
    result = _execute_task(args, protocol, task, model)
    end_mono = time.monotonic()
    end_wall = time.time()
    dur_str = _format_duration(end_mono - start_mono)
    start_str = _format_timestamp(start_wall)
    end_str = _format_timestamp(end_wall)

    if result == 0:
        planner.update_status(args.plan, task_id, "completed")
        print(f"  [{task_id}] completed in {dur_str} (started {start_str}, finished {end_str})")
        return True
    elif result == 2:
        planner.update_status(args.plan, task_id, "unmerged")
        print(
            f"  [{task_id}] complete (unmerged) in {dur_str} (started {start_str}, finished {end_str})",
            file=sys.stderr,
        )
        return False
    else:
        session = _session_for_task(args, planner, protocol, task_id)
        _task_record_status(planner, args.plan, task_id, result, session)
        print(
            f"{_task_outcome_line(task_id, _halt_outcome(session, task_id))} "
            f"{dur_str} (started {start_str}, finished {end_str})",
            file=sys.stderr,
        )
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


def _execute_wave_tasks_concurrent(
    planner, args, protocol, model, wave_id, tasks_to_run: list, effective_concurrency: int
) -> bool:
    """Execute wave tasks concurrently via JobManager background job dispatch.

    Dispatches tasks as isolated background processes (up to effective_concurrency
    at a time) under .snodo/jobs/<job_id>, waits for all tasks to complete,
    updates planner status, and ensures sibling tasks run to completion even if
    one fails.

    Returns:
        True if all tasks succeeded, False if any task failed or blocked.
    """
    from snodo.jobs import JobManager, JobError, TERMINAL_STATUSES
    from snodo.coders import resolve_coder_name
    from snodo.infrastructure.state import read_state

    project_root = str(planner.project_root)
    manager = JobManager(project_root)

    state = read_state(project_root)
    mode = getattr(args, "mode", None) or state.current_mode or protocol.initial_mode
    mode_obj = protocol.get_mode(mode)
    mode_coder = getattr(mode_obj, "coder", None) if mode_obj else None
    coder = resolve_coder_name(
        model=model,
        mode_coder=mode_coder,
        cli_coder=getattr(args, "coder", None),
        use_mock=getattr(args, "mock", False),
    )

    wave_failed = False
    active_jobs: dict[str, str] = {}  # job_id -> task_id
    task_start_mono: dict[str, float] = {}
    task_start_wall: dict[str, float] = {}
    pending_tasks = list(tasks_to_run)

    def _poll_active():
        nonlocal wave_failed
        for j_id, t_id in list(active_jobs.items()):
            try:
                st = manager.get_status(j_id)
                status = st.get("status")
                if status in TERMINAL_STATUSES:
                    exit_code = st.get("exit_code")
                    session = _session_for_task(args, planner, protocol, t_id)
                    outcome = _halt_outcome(session, t_id)
                    now_mono = time.monotonic()
                    now_wall = time.time()
                    job_started = st.get("started_at")
                    job_completed = st.get("completed_at")
                    t_start_wall = job_started or task_start_wall.get(t_id, now_wall)
                    t_end_wall = job_completed or now_wall

                    if job_started is not None and job_completed is not None:
                        task_duration = max(0.0, float(job_completed) - float(job_started))
                    else:
                        t_start_mono = task_start_mono.get(t_id, now_mono)
                        task_duration = max(0.0, now_mono - t_start_mono)

                    dur_str = _format_duration(task_duration)
                    start_str = _format_timestamp(t_start_wall)
                    end_str = _format_timestamp(t_end_wall)

                    if status == "completed" and exit_code == 0:
                        planner.update_status(args.plan, t_id, "completed")
                        print(f"  [{t_id}] completed (job {j_id}) in {dur_str} (started {start_str}, finished {end_str})")
                    elif status == "unmerged" or exit_code == 2:
                        planner.update_status(args.plan, t_id, "unmerged")
                        print(
                            f"  [{t_id}] complete (unmerged) (job {j_id}) in {dur_str} (started {start_str}, finished {end_str})",
                            file=sys.stderr,
                        )
                        wave_failed = True
                    else:
                        _task_record_status(planner, args.plan, t_id, exit_code or 1, session)
                        err_msg = st.get("error") or "execution failed"
                        print(
                            f"{_task_outcome_line(t_id, outcome)} {dur_str} "
                            f"(started {start_str}, finished {end_str}): {err_msg}",
                            file=sys.stderr,
                        )
                        wave_failed = True
                    active_jobs.pop(j_id, None)
            except Exception as e:
                now_mono = time.monotonic()
                now_wall = time.time()
                t_start_mono = task_start_mono.get(t_id, now_mono)
                t_start_wall = task_start_wall.get(t_id, now_wall)
                task_duration = max(0.0, now_mono - t_start_mono)
                dur_str = _format_duration(task_duration)
                start_str = _format_timestamp(t_start_wall)
                end_str = _format_timestamp(now_wall)

                planner.update_status(args.plan, t_id, "errored")
                print(
                    f"  [{t_id}] ERROR checking job {j_id} in {dur_str} (started {start_str}, finished {end_str}): {e}",
                    file=sys.stderr,
                )
                wave_failed = True
                active_jobs.pop(j_id, None)

    for task_id in pending_tasks:
        wave_dir = planner.plans_dir / args.plan / f"wave_{wave_id}"
        spec_file = wave_dir / f"{task_id}_task.md"
        if not spec_file.exists():
            planner.update_status(args.plan, task_id, "blocked")
            print(f"  [{task_id}] ERROR: spec file not found", file=sys.stderr)
            wave_failed = True
            continue

        spec = spec_file.read_text()
        tasks_status = planner.get_status(args.plan).get("tasks", {})
        if _task_is_unmerged(tasks_status, task_id):
            print(f"  [{task_id}] unmerged branch found; attempting fast-path merge")
            start_mono = time.monotonic()
            start_wall = time.time()
            audit_log = getattr(args, "audit_log", None)
            if audit_log is None:
                from snodo.infrastructure.audit import AuditLog
                audit_log_path = Path(project_root) / ".snodo" / "audit.log"
                if audit_log_path.exists():
                    audit_log = AuditLog(str(audit_log_path))
            session_manager = getattr(args, "session_manager", None)
            session = session_manager.get_active_session(mode, project_root) if session_manager else None
            session_id = session.session_id if session else None

            from snodo.cli.commands.run_cmd import _try_merge_unmerged_task
            merge_success = _try_merge_unmerged_task(
                project_root,
                task_id,
                spec,
                protocol=protocol,
                session_id=session_id,
                audit_log=audit_log,
            )
            end_mono = time.monotonic()
            end_wall = time.time()
            dur_str = _format_duration(end_mono - start_mono)
            start_str = _format_timestamp(start_wall)
            end_str = _format_timestamp(end_wall)

            if merge_success is True:
                planner.update_status(args.plan, task_id, "completed")
                print(f"  [{task_id}] completed in {dur_str} (started {start_str}, finished {end_str})")
                continue
            elif merge_success is False:
                planner.update_status(args.plan, task_id, "unmerged")
                print(
                    f"  [{task_id}] complete (unmerged) in {dur_str} (started {start_str}, finished {end_str})",
                    file=sys.stderr,
                )
                wave_failed = True
                continue
            print(f"  [{task_id}] unmerged branch not found or unverified; running fresh")

        is_retry = False
        if _task_is_blocked(tasks_status, task_id):
            decision = _plan_retry_decision(planner, args, protocol, task_id)
            if decision == "exhausted":
                print(f"  [{task_id}] not re-executed (max_retries reached)")
                wave_failed = True
                continue
            if decision == "retry":
                is_retry = True
                print(f"  [{task_id}] resuming as retry (failure context found)")
            else:
                print(f"  [{task_id}] no failure context found; running fresh")

        planner.update_status(args.plan, task_id, "in_progress")

        task_args = {
            "task_id": task_id,
            "description": spec,
            "protocol": args.protocol,
            "model": model,
            "coder": coder,
            "mode": mode,
            "mock": getattr(args, "mock", False),
            "verbose": getattr(args, "verbose", False),
            "no_isolation": getattr(args, "no_isolation", False),
            "cwd": project_root,
        }
        if is_retry:
            task_args["retry"] = task_id
        if getattr(args, "resume", None):
            task_args["resume"] = args.resume

        try:
            task_start_mono[task_id] = time.monotonic()
            task_start_wall[task_id] = time.time()
            job_id = manager.submit(task_args)
            active_jobs[job_id] = task_id
            print(f"  [{task_id}] executing (job {job_id})...")
        except (ValueError, JobError) as e:
            planner.update_status(args.plan, task_id, "errored")
            print(f"  [{task_id}] ERROR submitting job: {e}", file=sys.stderr)
            wave_failed = True
            continue

        while len(active_jobs) >= effective_concurrency:
            time.sleep(0.05)
            _poll_active()

    while active_jobs:
        time.sleep(0.05)
        _poll_active()

    return not wave_failed


def _execute_waves(waves, planner, args, protocol, model,
                   all_waves, interactive, effective_concurrency: int = 1) -> bool:
    """Execute waves in order, respecting dependencies and concurrency limits.

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
        tasks_to_run = []
        for task_id in wave.get("tasks", []):
            if _should_skip_task(task_id, tasks_status, interactive):
                continue
            tasks_to_run.append(task_id)

        if not tasks_to_run:
            continue

        wave_start_mono = time.monotonic()
        if effective_concurrency <= 1 or len(tasks_to_run) <= 1:
            for task_id in tasks_to_run:
                if not _execute_wave_task(planner, args, protocol, model, wave_id, task_id):
                    has_failed_or_blocked = True
        else:
            success = _execute_wave_tasks_concurrent(
                planner, args, protocol, model, wave_id, tasks_to_run, effective_concurrency
            )
            if not success:
                has_failed_or_blocked = True

        wave_end_mono = time.monotonic()
        wave_dur = wave_end_mono - wave_start_mono
        print(f"Wave {wave_id} total: {_format_duration(wave_dur)}")

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

        from snodo.infrastructure.state import read_state
        from snodo.infrastructure.config import load_llm_config

        state = read_state(project_root)
        active_mode = getattr(args, "mode", None) or state.current_mode or protocol.initial_mode

        mode_ceiling = protocol.concurrency_for(active_mode) if hasattr(protocol, "concurrency_for") else 1
        llm_cfg = load_llm_config()
        operator_capacity = getattr(llm_cfg.coder, "concurrency", 1)
        effective_concurrency = max(1, min(int(mode_ceiling), int(operator_capacity)))

        interactive = getattr(args, "interactive", False)
        if interactive and effective_concurrency > 1:
            print(
                f"Error: --interactive is incompatible with concurrent wave execution (concurrency={effective_concurrency}).",
                file=sys.stderr,
            )
            return 1

        failed = _execute_waves(
            waves, planner, args, protocol, model,
            all_waves, interactive, effective_concurrency=effective_concurrency,
        )

        _print_plan_progress(planner, args.plan)
        return 1 if failed else 0
