"""MCP handlers for the queue commands in ADR 053."""

from pathlib import Path
from typing import Any
import json


class QueueToolHandler:
    """Expose CLI-equivalent queue operations to MCP callers."""

    def __init__(self, project_root: str):
        self.project_root = Path(project_root)

    @staticmethod
    def _error(message: str):
        from snodo.mcp.server import MCPError

        raise MCPError(message)

    def handle_queue_list(self, _arguments: dict) -> dict:
        from snodo.infrastructure.queue_store import QueueError, QueueStore

        try:
            queues = QueueStore(self.project_root).list_queues()
            return {
                "queues": {
                    name: [{"name": plan, "status": self._plan_status(plan)} for plan in plans]
                    for name, plans in queues.items()
                }
            }
        except (QueueError, OSError, ValueError) as exc:
            self._error(str(exc))

    def handle_queue_create(self, arguments: dict) -> dict:
        from snodo.infrastructure.queue_store import QueueError, QueueStore

        name = str(arguments.get("name") or "")
        try:
            QueueStore(self.project_root).create_queue(name)
        except (QueueError, OSError, ValueError) as exc:
            self._error(str(exc))
        return {"ok": True, "queue": name}

    def handle_queue_move(self, arguments: dict) -> dict:
        from snodo.infrastructure.queue_store import QueueError, QueueStore

        plan = str(arguments.get("plan") or "")
        front = bool(arguments.get("front", False))
        before = arguments.get("before")
        after = arguments.get("after")
        queue = arguments.get("queue")
        try:
            store = QueueStore(self.project_root)
            if self._is_plan_running(plan):
                raise QueueError(f"Cannot move plan while it is running: {plan}")
            source = next((name for name, plans in store.list_queues().items() if plan in plans), None)
            store.move(plan, queue=queue, front=front, before=before, after=after)
        except (QueueError, OSError, ValueError) as exc:
            self._error(str(exc))
        return {
            "ok": True,
            "plan": plan,
            "queue": queue or source,
            "position": "front" if front else ("before" if before else "after" if after else "back"),
            "anchor": before or after,
        }

    def handle_queue_validate(self, arguments: dict) -> dict:
        from snodo.infrastructure.queue_validation import build_validation_report

        try:
            return build_validation_report(self.project_root, arguments.get("queue"))
        except (OSError, ValueError) as exc:
            self._error(str(exc))

    def handle_queue_run(self, arguments: dict) -> dict:
        """Submit the same queue CLI operation as a background job."""
        from snodo.infrastructure.queue_store import QueueError, QueueStore
        from snodo.jobs import JobError, JobManager
        from snodo.protocols import load_protocol

        queue_names = arguments.get("queues")
        all_queues = bool(arguments.get("all", False))
        parallel_run = arguments.get("parallel_run")
        if parallel_run is not None and (not isinstance(parallel_run, int) or isinstance(parallel_run, bool) or parallel_run < 1):
            self._error("--parallel-run must be at least 1")
        protocol_path = str(arguments.get("protocol") or ".snodo/protocol.yml")
        protocol = load_protocol(Path(protocol_path) if Path(protocol_path).is_absolute() else self.project_root / protocol_path)
        if not protocol:
            self._error(f"Could not load protocol: {protocol_path}")
        try:
            names = QueueStore(self.project_root).list_queues()
            if all_queues:
                if queue_names:
                    raise QueueError("Specify queue names or --all, not both")
                selected = list(names)
            else:
                selected = [part.strip() for part in queue_names.split(",")] if queue_names else ["default"]
                if any(not name for name in selected):
                    raise QueueError("Queue names must not be empty")
                if len(selected) != len(set(selected)):
                    raise QueueError("A queue may be specified only once")
                for name in selected:
                    if name not in names:
                        raise QueueError(f"Queue does not exist: {name}")
        except QueueError as exc:
            self._error(str(exc))

        task_args: dict[str, Any] = {
            "queue_run": True,
            "description": f"Run queue(s): {', '.join(selected)}",
            "queues": queue_names,
            "all": all_queues,
            "non_blocking": arguments.get("non_blocking"),
            "parallel_run": parallel_run,
            "protocol": protocol_path,
            "mock": bool(arguments.get("mock", False)),
            "cwd": str(self.project_root),
        }
        try:
            job_id = JobManager(str(self.project_root)).submit(task_args)
        except (JobError, ValueError, OSError) as exc:
            self._error(f"Failed to start queue run: {exc}")
        return {
            "status": "accepted",
            "job_id": job_id,
            "queues": selected,
            "instruction": "Queue run started. Poll get_job_status(job_id) until it completes; inspect output with get_job_logs(job_id).",
        }

    def _plan_status(self, plan_name: str) -> str:
        """Derive aggregate status from the plan and its recorded task statuses."""
        plan_dir = self.project_root / ".snodo" / "plans" / plan_name
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

    def _is_plan_running(self, plan_name: str) -> bool:
        """Detect live task states or a queued/running plan job."""
        if self._plan_status(plan_name) == "in_progress":
            return True
        try:
            from snodo.jobs import JobManager

            return any(
                job.get("plan") == plan_name and job.get("status") in {"queued", "running"}
                for job in JobManager(str(self.project_root)).list_jobs()
            )
        except Exception:
            return False

    def tool_handlers(self) -> dict:
        return {
            "queue_list": self.handle_queue_list,
            "queue_create": self.handle_queue_create,
            "queue_move": self.handle_queue_move,
            "queue_validate": self.handle_queue_validate,
            "queue_run": self.handle_queue_run,
        }
