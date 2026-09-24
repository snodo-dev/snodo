"""Run queued plans in FIFO order (ADR 053)."""

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import typer
import yaml

from snodo.infrastructure.paths import require_project_root


COMMAND_NAME = "queue"
app = typer.Typer(help="Manage plan queues")


@app.command("run")
def queue_run(
    queues: str | None = typer.Argument(
        None, help="Queue name or comma-separated queue names (defaults to 'default')",
    ),
    all_queues: bool = typer.Option(False, "--all", help="Run every queue in creation order"),
    non_blocking: bool | None = typer.Option(
        None, "--non-blocking/--blocking", help="Continue past unfinished plans",
    ),
    parallel_run: int | None = typer.Option(
        None, "--parallel-run", min=1, help="Maximum concurrent plans per queue",
    ),
    protocol: str = typer.Option(".snodo/protocol.yml", "--protocol", help="Protocol file"),
    mock: bool = typer.Option(False, "--mock", help="Use mock coder"),
):
    """Run queued plans until each queue is empty or blocked."""
    return _queue_run(queues, all_queues, non_blocking, parallel_run, protocol, mock)


def _queue_run(
    queues: str | None = None,
    all_queues: bool = False,
    non_blocking: bool | None = None,
    parallel_run: int | None = None,
    protocol: str = ".snodo/protocol.yml",
    mock: bool = False,
) -> int:
    from snodo.cli.commands import load_protocol
    from snodo.cli.commands.plan_run import _run_plan
    from snodo.cli.commands.run_cmd import RunArgs
    from snodo.infrastructure.queue_store import QueueError, QueueStore

    project_root = require_project_root()
    protocol_obj = load_protocol(Path(protocol))
    if not protocol_obj:
        return 1
    queue_config = protocol_obj.queue
    skip_blocked = queue_config.non_blocking if non_blocking is None else non_blocking
    concurrency = parallel_run if parallel_run is not None else queue_config.parallel_runs
    if concurrency > 1 and not skip_blocked:
        print("Note: --parallel-run requires --non-blocking; running one plan at a time.")
        concurrency = 1

    store = QueueStore(project_root)
    try:
        queue_map = store.list_queues()
        if all_queues:
            if queues:
                raise QueueError("Specify queue names or --all, not both")
            selected = list(queue_map)
        else:
            selected = [part.strip() for part in queues.split(",")] if queues else ["default"]
            if any(not name for name in selected):
                raise QueueError("Queue names must not be empty")
            if len(selected) != len(set(selected)):
                raise QueueError("A queue may be specified only once")
            for name in selected:
                if name not in queue_map:
                    raise QueueError(f"Queue does not exist: {name}")
    except QueueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    def run_named_queue(queue_name: str) -> int:
        try:
            with store.lock(queue_name):
                return _run_queue(store, queue_name, project_root, _run_plan, RunArgs,
                                  protocol, mock, skip_blocked, concurrency)
        except QueueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

    if not all_queues and len(selected) > 1:
        with ThreadPoolExecutor(max_workers=len(selected)) as pool:
            results = list(pool.map(run_named_queue, selected))
        return 1 if any(results) else 0
    results = [run_named_queue(name) for name in selected]
    return 1 if any(results) else 0


def _run_queue(store, queue_name, project_root, run_plan, run_args, protocol, mock,
               non_blocking: bool, concurrency: int) -> int:
    """Run one locked queue, retaining skipped plans in their original positions."""
    initial = store.list_queues().get(queue_name, [])
    pending = list(initial)
    failed = False
    while pending:
        current = store.list_queues().get(queue_name, [])
        pending = [plan for plan in pending if plan in current]
        if not pending:
            break
        if concurrency == 1:
            plan = pending.pop(0)
            if _plan_is_running(project_root, plan):
                print(f"Queue '{queue_name}' stopped: plan '{plan}' is already running.")
                return 1
            code = run_plan(run_args(plan=plan, protocol=protocol, mock=mock))
            if code == 0:
                store.remove(plan)
                continue
            task, reason = _failure_detail(project_root, plan)
            print(f"Queue '{queue_name}' stopped at plan '{plan}', task '{task}': {reason}")
            failed = True
            if not non_blocking:
                return 1
            continue

        batch = pending[:concurrency]
        pending = pending[concurrency:]
        running = [plan for plan in batch if _plan_is_running(project_root, plan)]
        if running:
            for plan in running:
                print(f"Queue '{queue_name}' stopped: plan '{plan}' is already running.")
            return 1
        with ThreadPoolExecutor(max_workers=len(batch)) as pool:
            futures = {
                pool.submit(run_plan, run_args(plan=plan, protocol=protocol, mock=mock)): plan
                for plan in batch
            }
            outcomes = [(plan, future.result()) for future, plan in futures.items()]
        for plan, code in outcomes:
            if code == 0:
                store.remove(plan)
            else:
                task, reason = _failure_detail(project_root, plan)
                print(f"Queue '{queue_name}' plan '{plan}', task '{task}': {reason}")
                failed = True
                if not non_blocking:
                    return 1
    return 1 if failed else 0


def _plan_is_running(project_root: Path, plan: str) -> bool:
    plan_status = project_root / ".snodo" / "plans" / plan / "status.json"
    try:
        tasks = json.loads(plan_status.read_text()).get("tasks", {})
        if any(
            (value.get("status") if isinstance(value, dict) else value)
            in {"running", "in_progress"}
            for value in tasks.values()
        ):
            return True
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    try:
        from snodo.jobs import JobManager

        return any(
            job.get("plan") == plan and job.get("status") in {"queued", "running"}
            for job in JobManager(str(project_root)).list_jobs()
        )
    except (ValueError, OSError):
        return False


def _failure_detail(project_root: Path, plan: str) -> tuple[str, str]:
    plan_dir = project_root / ".snodo" / "plans" / plan
    try:
        plan_data = yaml.safe_load((plan_dir / "plan.yml").read_text()) or {}
        status_path = plan_dir / "status.json"
        status = json.loads(status_path.read_text()) if status_path.exists() else {}
        task_states = status.get("tasks", {})
        tasks = [str(task) for wave in plan_data.get("waves", []) for task in wave.get("tasks", [])]
        for task in tasks:
            value = task_states.get(task, {})
            state = value.get("status") if isinstance(value, dict) else value
            if state in {"blocked", "errored", "unmerged"}:
                reason = value.get("reason") or value.get("error") or state
                return task, str(reason)
        for task in tasks:
            value = task_states.get(task, {})
            state = value.get("status") if isinstance(value, dict) else value
            if state != "completed":
                return task, f"{state or 'not completed'} (plan runner exited unsuccessfully)"
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return "(plan)", "plan runner exited unsuccessfully"
