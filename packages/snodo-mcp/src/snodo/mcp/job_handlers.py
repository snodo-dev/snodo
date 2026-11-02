"""Job tool handlers for the MCP server.

Extracted from mcp/server.py to isolate job-related tool handling.
"""

import re
from typing import Any, Dict


_ANSI_ESCAPE_SEQUENCE = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\)|[@-_])"
)


class JobToolHandler:
    """Handles job observation tool calls."""

    def __init__(self, project_root: str, serving_version: str = "unknown"):
        self.project_root = project_root
        self.serving_version = serving_version

    def handle_get_job_status(self, arguments: Dict[str, Any]) -> dict:
        """Get the current status of one dispatched job.

        This is the single-job tool: it carries the full task spec — the
        detail the bounded list_jobs listing omits.
        """
        from snodo.jobs import JobManager

        job_id = arguments.get("job_id", "")
        if not job_id:
            from snodo.mcp.server import MCPError
            raise MCPError("get_job_status requires job_id")

        job_mgr = JobManager(self.project_root)
        try:
            full = job_mgr.get_status(job_id)
        except Exception as e:
            from snodo.mcp.server import MCPError
            raise MCPError(f"Job not found or error: {e}") from e

        task = full.get("task") if isinstance(full.get("task"), dict) else {}
        plan = task.get("plan_name") or ""
        result = {
            "id": full.get("id", job_id),
            "status": full.get("status", "unknown"),
            "job_type": full.get("job_type", "task"),
            "pid": full.get("pid"),
            "exit_code": full.get("exit_code"),
            "created_at": full.get("created_at"),
            "started_at": full.get("started_at"),
            "completed_at": full.get("completed_at"),
            # A plan run is not one task: it names its plan and leaves task_ref
            # empty. A task spawned by a plan names that plan-run job so the
            # two never read as the same kind of row (Fixes #254).
            "plan": plan,
            "queues": task.get("queues", []) if task.get("queue_run") else [],
            "parent_job": task.get("parent_job", "") or "",
            "task_ref": "" if plan or full.get("job_type") == "queue" else (
                task.get("task_id") or task.get("retry_task_id") or ""
            ),
            "task_spec": task.get("description", ""),
            "module": task.get("module_id"),
        }
        provenance = full.get("cost", {}).get("provenance", {})
        job_version = provenance.get("snodo_version") if isinstance(provenance, dict) else None
        if job_version:
            result["snodo_version"] = {
                "serving": self.serving_version,
                "job": job_version,
                "mismatch": job_version != self.serving_version,
            }
        return result

    def handle_list_jobs(self, arguments: Dict[str, Any]) -> dict:
        """List bounded, compact job summaries for orchestrators."""
        from snodo.jobs import JobManager
        from snodo.mcp.server import MCPError

        default_limit, maximum_limit = 50, 100
        raw_limit = arguments.get("limit", default_limit)
        if isinstance(raw_limit, bool) or not isinstance(raw_limit, int) or raw_limit < 1:
            raise MCPError("list_jobs limit must be an integer between 1 and 100")
        limit = min(raw_limit, maximum_limit)
        status = arguments.get("status")
        allowed_statuses = {"queued", "running", "completed", "failed", "cancelled", "unmerged"}
        if status is not None and status not in allowed_statuses:
            choices = ", ".join(sorted(allowed_statuses))
            raise MCPError(f"Unknown job status {status!r}; expected one of: {choices}")

        rows = JobManager(self.project_root).list_jobs()
        if status is not None:
            rows = [row for row in rows if row.get("status") == status]
        truncated = len(rows) > limit
        compact = []
        for row in rows[:limit]:
            item = {
                "id": row.get("id"),
                "type": row.get("job_type") or row.get("type") or ("plan" if row.get("plan") else "task"),
                "status": row.get("status"),
                "created": row.get("created_at"),
                "started": row.get("started_at"),
                "completed": row.get("completed_at"),
            }
            for key in ("plan", "wave", "parent_job"):
                if row.get(key) not in (None, ""):
                    item[key] = row[key]
            title = row.get("title") or row.get("task_ref")
            if title:
                item["title"] = str(title)[:160]
            if row.get("task_ref"):
                item["task_id"] = str(row["task_ref"])[:100]
            error = row.get("error")
            if error:
                item["error"] = str(error)[:240]
            compact.append(item)
        return {"jobs": compact, "limit": limit, "truncated": truncated}

    def handle_get_job_logs(self, arguments: Dict[str, Any]) -> dict:
        """Fetch logs for a job."""
        from snodo.jobs import JobManager

        job_id = arguments.get("job_id", "")
        if not job_id:
            from snodo.mcp.server import MCPError
            raise MCPError("get_job_logs requires job_id")
        stream = arguments.get("stream", "stdout")
        tail = arguments.get("tail", 50)

        job_mgr = JobManager(self.project_root)
        try:
            log_content = job_mgr.get_logs(job_id, stream=stream, tail=tail)
        except Exception as e:
            from snodo.mcp.server import MCPError
            raise MCPError(f"Job not found or error: {e}") from e

        return {
            "job_id": job_id,
            "stream": stream,
            "tail": tail,
            "log": _ANSI_ESCAPE_SEQUENCE.sub("", log_content),
        }

    def handle_watch_job(self, arguments: Dict[str, Any]) -> str:
        """Return a text snapshot and an MCP Apps resource for live viewing."""
        job_id = arguments.get("job_id", "")
        if not job_id:
            from snodo.mcp.server import MCPError
            raise MCPError("watch_job requires job_id")

        status = self.handle_get_job_status({"job_id": job_id})
        logs = self.handle_get_job_logs({"job_id": job_id, "tail": 10})
        output = logs.get("log", "") or "(no stdout output)"
        next_action = ""
        if status.get("status") == "failed" and status.get("task_ref"):
            try:
                from snodo.jobs import JobManager
                full = JobManager(self.project_root).get_status(job_id)
                halt = full.get("halt") or {}
                if halt and (halt.get("final_decision") or halt.get("halt_type")):
                    from snodo.infrastructure.next_actions import next_actions_for_halt

                    halt = dict(halt)
                    halt.setdefault("task_id", status["task_ref"])
                    actions = next_actions_for_halt(
                        halt, in_plan=bool(status.get("plan"))
                    )
                    if actions:
                        lines = ["\nRecommended next actions:"]
                        for action in actions:
                            lines.append(action.instruction)
                            if action.command:
                                lines.append(action.command)
                        next_action = "\n" + "\n".join(lines)
            except (ValueError, TypeError, KeyError):
                pass
        return (
            f"Job {job_id} — {status['status']} ({_job_elapsed(status)})\n"
            f"\nLast output lines:\n{output.rstrip()}{next_action}"
        )

    def tool_handlers(self) -> dict:
        return {
            "get_job_status": self.handle_get_job_status,
            "list_jobs": self.handle_list_jobs,
            "get_job_logs": self.handle_get_job_logs,
            "watch_job": self.handle_watch_job,
        }


def _job_elapsed(status: dict) -> str:
    """Format elapsed job time from the status timestamps, when available."""
    import time

    start = status.get("started_at") or status.get("created_at")
    if start is None:
        return "elapsed unavailable"
    end = status.get("completed_at") or time.time()
    try:
        seconds = max(0, int(float(end) - float(start)))
    except (TypeError, ValueError):
        return "elapsed unavailable"
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d} elapsed" if hours else f"{minutes}:{seconds:02d} elapsed"
