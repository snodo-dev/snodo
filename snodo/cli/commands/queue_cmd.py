"""Manage named plan queues (ADR 053)."""

import json
from pathlib import Path

import typer

from snodo.infrastructure.queue_store import QueueError, QueueStore


COMMAND_NAME = "queue"
app = typer.Typer(invoke_without_command=True, help="Manage plan queues")


@app.callback()
def _queue_callback(
    ctx: typer.Context,
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
):
    """List queues, or choose a queue operation."""
    if ctx.invoked_subcommand is None:
        raise typer.Exit(queue_list(json_output=json_output))


@app.command("create")
def queue_create(
    name: str = typer.Argument(..., help="Name for the new queue"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
):
    """Create an empty queue."""
    raise typer.Exit(_queue_create(name, json_output=json_output))


@app.command("move")
def queue_move(
    plan: str = typer.Argument(..., help="Queued plan to move"),
    front: bool = typer.Option(False, "--front", help="Move to the front of its queue"),
    before: str | None = typer.Option(None, "--before", help="Place before another plan"),
    after: str | None = typer.Option(None, "--after", help="Place after another plan"),
    to: str | None = typer.Option(None, "--to", help="Destination queue"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
):
    """Move a plan within or between queues."""
    raise typer.Exit(_queue_move(
        plan, front=front, before=before, after=after, queue=to,
        json_output=json_output,
    ))


@app.command("remove")
def queue_remove(
    plan: str = typer.Argument(..., help="Queued plan to remove"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
):
    """Remove a plan from its queue without changing its plan records."""
    raise typer.Exit(_queue_remove(plan, json_output=json_output))


def queue_list(json_output: bool = False) -> int:
    """List all queues and each queued plan's status from its own records."""
    from snodo.infrastructure.paths import require_project_root

    try:
        root = Path(require_project_root())
        queues = QueueStore(root).list_queues()
        payload = {
            name: [
                {"name": plan, "status": _plan_status(root, plan)}
                for plan in plans
            ]
            for name, plans in queues.items()
        }
    except (QueueError, OSError, ValueError) as exc:
        return _error("queue", str(exc), json_output)

    if json_output:
        from snodo.cli.json_output import emit_json, schema_name

        return emit_json({"schema": schema_name("queue"), "ok": True, "queues": payload})

    if not payload:
        print("No queues found.")
    for name, plans in payload.items():
        print(f"{name}:")
        if not plans:
            print("  (empty)")
        for plan in plans:
            print(f"  {plan['name']} [{plan['status']}]")
    return 0


def _queue_create(name: str, *, json_output: bool) -> int:
    from snodo.infrastructure.paths import require_project_root

    try:
        QueueStore(require_project_root()).create_queue(name)
    except (QueueError, OSError, ValueError) as exc:
        return _error("queue.create", str(exc), json_output)

    if json_output:
        from snodo.cli.json_output import emit_json, schema_name

        return emit_json({"schema": schema_name("queue.create"), "ok": True, "queue": name})
    print(f"Created queue: {name}")
    return 0


def _queue_move(
    plan: str,
    *,
    front: bool,
    before: str | None,
    after: str | None,
    queue: str | None,
    json_output: bool,
) -> int:
    from snodo.infrastructure.paths import require_project_root

    try:
        root = Path(require_project_root())
        store = QueueStore(root)
        if _is_plan_running(root, plan):
            raise QueueError(f"Cannot move plan while it is running: {plan}")
        source_queue = next(
            (name for name, plans in store.list_queues().items() if plan in plans),
            None,
        )
        store.move(plan, queue=queue, front=front, before=before, after=after)
    except (QueueError, OSError, ValueError) as exc:
        return _error("queue.move", str(exc), json_output)

    if json_output:
        from snodo.cli.json_output import emit_json, schema_name

        return emit_json({
            "schema": schema_name("queue.move"), "ok": True,
            "plan": plan, "queue": queue or source_queue,
            "position": "front" if front else ("before" if before else "after" if after else "back"),
            "anchor": before or after,
        })
    destination = f" to {queue}" if queue and queue != source_queue else ""
    if front:
        position = " at the front"
    elif before:
        position = f" before {before}"
    elif after:
        position = f" after {after}"
    else:
        position = " at the back"
    print(f"Moved {plan}{destination}{position}.")
    return 0


def _queue_remove(plan: str, *, json_output: bool) -> int:
    from snodo.infrastructure.paths import require_project_root

    try:
        root = Path(require_project_root())
        store = QueueStore(root)
        source_queue = next(
            (name for name, plans in store.list_queues().items() if plan in plans),
            None,
        )
        if source_queue is None:
            raise QueueError(f"Plan is not queued: {plan}")
        if _is_plan_running(root, plan):
            raise QueueError(f"Cannot remove plan while it is running: {plan}")
        store.remove(plan)
    except (QueueError, OSError, ValueError) as exc:
        return _error("queue.remove", str(exc), json_output)

    if json_output:
        from snodo.cli.json_output import emit_json, schema_name

        return emit_json({
            "schema": schema_name("queue.remove"), "ok": True,
            "plan": plan, "queue": source_queue,
        })
    print(f"Removed {plan} from {source_queue}.")
    return 0


def _plan_status(project_root: Path, plan_name: str) -> str:
    """Derive a plan's aggregate status from its plan.yml and status.json."""
    plan_dir = project_root / ".snodo" / "plans" / plan_name
    try:
        import yaml

        plan = yaml.safe_load((plan_dir / "plan.yml").read_text(encoding="utf-8")) or {}
        status_path = plan_dir / "status.json"
        status_data = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
        task_statuses = status_data.get("tasks", {})
        statuses = []
        for wave in plan.get("waves", []):
            for task in wave.get("tasks", []):
                value = task_statuses.get(str(task), "pending")
                if isinstance(value, dict):
                    value = value.get("status", "pending")
                value = str(value)
                statuses.append({"running": "in_progress", "failed": "errored", "merged": "completed"}.get(value, value))
    except (OSError, ValueError, TypeError, AttributeError):
        return "unknown"

    if not statuses:
        return "pending"
    for status in ("errored", "blocked", "unmerged", "in_progress"):
        if status in statuses:
            return status
    return "completed" if all(status == "completed" for status in statuses) else "pending"


def _is_plan_running(project_root: Path, plan_name: str) -> bool:
    """Detect a live plan run from task statuses and the job records."""
    if _plan_status(project_root, plan_name) == "in_progress":
        return True
    try:
        from snodo.jobs import JobManager

        return any(
            job.get("plan") == plan_name and job.get("status") in {"queued", "running"}
            for job in JobManager(str(project_root)).list_jobs()
        )
    except Exception:
        return False


def _error(command: str, message: str, json_output: bool) -> int:
    if json_output:
        from snodo.cli.json_output import EXIT_BLOCKER, emit_error

        return emit_error(command, message, EXIT_BLOCKER)
    from snodo.cli.json_output import print_error

    print_error(f"Error: {message}")
    return 1
