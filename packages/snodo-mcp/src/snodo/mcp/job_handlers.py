"""Job tool handlers for the MCP server.

Extracted from mcp/server.py to isolate job-related tool handling.
"""

from typing import Any, Dict


class JobToolHandler:
    """Handles get_job_status, list_jobs, and get_job_logs tool calls."""

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

    def handle_list_jobs(self, arguments: Dict[str, Any]) -> list:
        """List all jobs as bounded one-line summaries.

        Rows identify the work (task_ref, title) and how it ended (status,
        exit_code, duration) without carrying task spec prose; the full spec
        of a job is available per job from get_job_status.
        """
        from snodo.jobs import JobManager

        job_mgr = JobManager(self.project_root)
        return job_mgr.list_jobs()

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
            "log": log_content,
        }

    def tool_handlers(self) -> dict:
        return {
            "get_job_status": self.handle_get_job_status,
            "list_jobs": self.handle_list_jobs,
            "get_job_logs": self.handle_get_job_logs,
        }
