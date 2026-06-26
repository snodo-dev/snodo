"""Job tool handlers for the MCP server.

Extracted from mcp/server.py to isolate job-related tool handling.
"""

from typing import Any, Dict


class JobToolHandler:
    """Handles get_job_status, list_jobs, and get_job_logs tool calls."""

    def __init__(self, project_root: str):
        self.project_root = project_root

    def handle_get_job_status(self, arguments: Dict[str, Any]) -> dict:
        """Get the current status of a dispatched job (status fields only)."""
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
            raise MCPError(f"Job not found or error: {e}")

        return {
            "id": full.get("id", job_id),
            "status": full.get("status", "unknown"),
            "pid": full.get("pid"),
            "exit_code": full.get("exit_code"),
            "created_at": full.get("created_at"),
            "started_at": full.get("started_at"),
            "completed_at": full.get("completed_at"),
        }

    def handle_list_jobs(self, arguments: Dict[str, Any]) -> list:
        """List all jobs for the current project."""
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
            raise MCPError(f"Job not found or error: {e}")

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
