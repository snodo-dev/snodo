"""Run queued plans in FIFO order (ADR 053)."""

from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import typer
import yaml

from snodo.infrastructure.paths import require_project_root


# Attached to the single `snodo queue` group owned by queue_cmd; defining a
# second Typer named "queue" here would replace that group at discovery.
from snodo.cli.commands import queue_cmd as _queue_group


@_queue_group.app.command("run")
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

    project_root = Path(require_project_root())
    protocol_obj = load_protocol(Path(protocol))
    if not protocol_obj:
        return 1
    queue_config = protocol_obj.queue
    skip_blocked = queue_config.non_blocking if non_blocking is None else non_blocking
    concurrency = parallel_run if parallel_run is not None else queue_config.parallel_runs
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
                                  protocol, mock, skip_blocked, concurrency,
                                  protocol_config=protocol_obj)
        except QueueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

    from snodo.infrastructure import cloud_liveness
    cloud_liveness.install()
    try:
        if not all_queues and len(selected) > 1:
            with ThreadPoolExecutor(max_workers=len(selected)) as pool:
                results = list(pool.map(run_named_queue, selected))
            return 1 if any(results) else 0
        results = [run_named_queue(name) for name in selected]
        return 1 if any(results) else 0
    finally:
        cloud_liveness.uninstall()


def _run_queue(store, queue_name, project_root, run_plan, run_args, protocol, mock,
               non_blocking: bool, concurrency: int, protocol_config=None) -> int:
    """Run one locked queue, retaining skipped plans in their original positions."""
    initial = store.list_queues().get(queue_name, [])
    queue_branch = None
    queue_meta = project_root / ".snodo" / "queue-integration" / f"{queue_name}.json"
    from snodo.infrastructure.state import read_state
    state = read_state(project_root)
    policy = protocol_config or protocol
    mode = state.current_mode or getattr(policy, "initial_mode", None)
    delivery = policy.delivery_for(mode) if hasattr(policy, "delivery_for") else "local_merge"
    if delivery in {"change_request", "push_branch"} and initial:
        try:
            from snodo.infrastructure.state import atomic_update_json
            from snodo.infrastructure.worktree import _name_component, worktree_dir
            from snodo.tools.git import open_repo, resolve_base_branch
            state = json.loads(queue_meta.read_text()) if queue_meta.exists() else {}
            queue_branch = state.get("branch")
            if not queue_branch:
                base_ref = resolve_base_branch(str(project_root))
                with open_repo(str(project_root)) as repo:
                    base_sha = repo.commit(base_ref).hexsha
                    queue_branch = f"queue/{_name_component(queue_name)}/integration"
                    queue_path = worktree_dir(str(project_root)) / "queues" / _name_component(queue_name) / "integration"
                    queue_path.parent.mkdir(parents=True, exist_ok=True)
                    if queue_branch not in repo.heads:
                        repo.git.worktree("add", str(queue_path), "-b", queue_branch, base_ref)
                    elif not queue_path.exists():
                        repo.git.worktree("add", str(queue_path), queue_branch)
                atomic_update_json(queue_meta.parent, queue_meta.name,
                                   lambda data: data.update(branch=queue_branch, base_sha=base_sha), strict=True)
            previous = os.environ.get("SNODO_QUEUE_INTEGRATION_BRANCH")
            os.environ["SNODO_QUEUE_INTEGRATION_BRANCH"] = queue_branch
        except Exception as exc:
            print(f"Queue integration setup failed: {exc}", file=sys.stderr)
            return 1
    else:
        previous = None
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
                failed = True
                break
            code = run_plan(run_args(
                plan=plan, protocol=protocol, mock=mock,
                trigger="queue", queue=queue_name,
            ))
            if code == 0:
                store.remove(plan)
                continue
            task, reason = _failure_detail(project_root, plan)
            print(f"Queue '{queue_name}' stopped at plan '{plan}', task '{task}': {reason}")
            failed = True
            if not non_blocking:
                break
            continue

        batch = pending[:concurrency]
        pending = pending[concurrency:]
        running = [plan for plan in batch if _plan_is_running(project_root, plan)]
        if running:
            for plan in running:
                print(f"Queue '{queue_name}' stopped: plan '{plan}' is already running.")
            failed = True
            break
        with ThreadPoolExecutor(max_workers=len(batch)) as pool:
            futures = {
                pool.submit(run_plan, run_args(
                    plan=plan, protocol=protocol, mock=mock,
                    trigger="queue", queue=queue_name,
                )): plan
                for plan in batch
            }
            outcomes = [(plan, future.result()) for future, plan in futures.items()]
        stop_after_batch = False
        for plan, code in outcomes:
            if code == 0:
                store.remove(plan)
            else:
                task, reason = _failure_detail(project_root, plan)
                print(f"Queue '{queue_name}' stopped at plan '{plan}', task '{task}': {reason}")
                failed = True
                if not non_blocking:
                    stop_after_batch = True
        if stop_after_batch:
            break
    if queue_branch and not failed and not store.list_queues().get(queue_name):
        from snodo.cli.commands.run_merge import _deliver_plan_integration
        from snodo.infrastructure.audit import get_audit_log
        from snodo.project import get_project_id
        project_id, _ = get_project_id(str(project_root))
        delivered = False
        if delivery in {"push_branch", "change_request"}:
            try:
                from snodo.tools.git import open_repo
                from snodo.cli.commands.plan_run import _remote_branch_matches
                with open_repo(str(project_root)) as repo:
                    local_sha = repo.commit(queue_branch).hexsha
                    remote = getattr(policy.execution, "delivery_remote", "origin")
                    delivered = _remote_branch_matches(repo, remote, queue_branch, local_sha)
            except Exception:
                delivered = False
        result = 0 if delivered else _deliver_plan_integration(
            str(project_root), queue_branch, queue_name,
            "Plans: " + ", ".join(initial), policy, mode,
            get_audit_log(project_id=project_id),
        )
        if result:
            failed = True
        else:
            queue_meta.unlink(missing_ok=True)
    if queue_branch:
        if previous is None:
            os.environ.pop("SNODO_QUEUE_INTEGRATION_BRANCH", None)
        else:
            os.environ["SNODO_QUEUE_INTEGRATION_BRANCH"] = previous
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
