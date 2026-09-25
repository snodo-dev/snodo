"""Planning tool handlers for the MCP server.

FILE: snodo/mcp/plan_handlers.py

Exposes the plan gate — the step above the task loop — over the tool
surface: an intent becomes a proposed plan (propose_plan), the plan is
retrievable by its stable name at any time (get_plan), it can be validated
without executing anything (validate_plan, planner MCP), and an approved
plan can be run (run_plan).

What comes back preserves the structure plans already carry on disk
(.snodo/plans/<name>/plan.yml with name, intent, waves — ids, depends_on,
tasks — plus status.json beside it). The files remain the source of truth:
nothing here caches a plan or keeps a second copy of plan state.

run_plan refuses, before executing anything, a plan that fails the same
verifier ``snodo plan run`` uses (snodo.compiler.verifier.verify_plan_dir).
The run itself is a background job: it spawns the CLI's plan-run path through
the same job wrapper every dispatched unit uses, returns a job id at once, and
is followed with get_job_status / list_jobs / get_job_logs. Blocking on the
run was the defect — a wave outlives any MCP call, so a blocking run is
reported as a timeout while the run continues (Fixes #254). The engine loop
keeps its authority over every dispatched task; this opens no route around it.
The mcp layer may not import the app layer, so the plan runs as a subprocess,
mirroring how snodo.jobs.wrapper invokes the CLI.
"""

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from snodo.compiler.verifier import verify_plan_dir
from snodo.mcp.planner import PlannerError, plan_history_shape

logger = logging.getLogger(__name__)

#: Default ceiling on the opt-in blocking wait for a plan run. A wave takes
#: minutes, not milliseconds, so this is deliberately generous: it exists to
#: bound a test/script that asked to block, not to make blocking the norm.
_DEFAULT_WAIT_SECONDS = 3600.0


def _tail(text: str, lines: int = 50) -> str:
    """Last *lines* lines of *text*."""
    return "\n".join(text.splitlines()[-lines:])


class PlanToolHandler:
    """Handles propose_plan, get_plan, and run_plan tool calls."""

    def __init__(self, server: "Any"):
        self.server = server

    @property
    def _planner(self):
        return self.server.planner

    def _plan_dir(self, plan_name: str) -> Path:
        from snodo.mcp.server import MCPError

        if not plan_name or not str(plan_name).strip():
            raise MCPError("run/get/propose plan requires plan_name")
        plan_dir = self._planner.plans_dir / str(plan_name)
        if not (plan_dir / "plan.yml").is_file():
            raise MCPError(f"Plan not found: {plan_name}")
        return plan_dir

    def _validation(self, plan_dir: Path) -> dict:
        """The authoritative verdict of the same verifier the CLI gates on."""
        result = verify_plan_dir(plan_dir, workspace_root=self._planner.project_root)
        return {
            "valid": result.passed,
            "errors": list(result.errors),
            "warnings": list(result.warnings),
        }

    def _task_statuses(self, plan_name: str) -> Dict[str, str]:
        """Per-task status map read from the plan's status.json (source of truth)."""
        status_file = self._planner.plans_dir / plan_name / "status.json"
        if not status_file.exists():
            return {}
        try:
            with open(status_file) as f:
                data = json.load(f) or {}
        except Exception as e:  # noqa: BLE001 — report, never invent
            logger.warning("Could not read plan status %s: %s", status_file, e)
            return {}
        tasks = data.get("tasks", {})
        if not isinstance(tasks, dict):
            return {}
        statuses: Dict[str, str] = {}
        for tid, entry in tasks.items():
            if isinstance(entry, dict):
                statuses[tid] = str(entry.get("status", "pending"))
            else:
                statuses[tid] = str(entry)
        return statuses

    def _task_runs(self, plan_name: str) -> dict[str, dict]:
        """Join each plan task to its latest child job, when one exists."""
        from snodo.jobs import index_plan_jobs

        _, task_jobs = index_plan_jobs(str(self._planner.project_root), plan_name)
        return {
            task_id: {
                "job_id": job.get("id"),
                "status": job.get("status"),
                "started_at": job.get("started_at"),
                "completed_at": job.get("completed_at"),
                "duration_seconds": job.get("duration_seconds"),
            }
            for task_id, job in task_jobs.items()
        }

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    def handle_propose_plan(self, arguments: Dict[str, Any]) -> dict:
        """Turn an intent into a proposed plan on disk; nothing executes."""
        from snodo.mcp.server import MCPError

        intent = arguments.get("intent") or ""
        plan_name = str(arguments.get("plan_name") or "")
        waves = arguments.get("waves", 1)

        try:
            plan_data = self._planner.decompose(intent, plan_name, waves=waves)
        except PlannerError as e:
            raise MCPError(str(e)) from e

        self.server._audit("plan_proposed", {
            "op": "plan_proposed",
            **plan_history_shape(plan_data, plan_name),
        })
        return {
            "status": "proposed",
            "plan": plan_data,
            "validation": self._validation(self._planner.plans_dir / plan_name),
            "instruction": (
                "Review the proposal. Add task specs with generate_spec, gate "
                "with validate_plan, then run_plan once it passes."
            ),
        }

    def handle_get_plan(self, arguments: Dict[str, Any]) -> dict:
        """Retrieve a plan by name, in plan-file shape, any time — including
        while a run is in progress (the run writes status.json as it goes)."""
        plan_name = str(arguments.get("plan_name") or "")
        plan_dir = self._plan_dir(plan_name)

        try:
            with open(plan_dir / "plan.yml") as f:
                plan_data = yaml.safe_load(f) or {}
        except Exception as e:  # noqa: BLE001 — a corrupt file is reported, not hidden
            from snodo.mcp.server import MCPError

            raise MCPError(f"Failed to read plan.yml for '{plan_name}': {e}") from e

        return {
            "name": plan_data.get("name", plan_name),
            "intent": plan_data.get("intent", ""),
            "waves": plan_data.get("waves", []),
            "tasks": self._task_statuses(plan_name),
            "task_runs": self._task_runs(plan_name),
            "validation": self._validation(plan_dir),
        }

    def handle_record_task_status(self, arguments: Dict[str, Any]) -> dict:
        """Record an operator's status for a task, outside the loop.

        The one implementation is :meth:`PlannerMCP.record_status`, which
        ``snodo task complete`` also calls: the same vocabulary, provenance
        and audit event, so the two surfaces cannot drift. This records a
        human's account and decides nothing — it can never pass a task in a
        validator's place, and the audit entry is marked unjudged.
        """
        from snodo.mcp.server import MCPError

        plan_name = str(arguments.get("plan_name") or "")
        task_id = str(arguments.get("task_id") or "")
        status = str(arguments.get("status") or "")
        who = str(arguments.get("who") or "")
        notes = arguments.get("notes")
        notes = str(notes) if notes else None

        if not plan_name.strip():
            raise MCPError("record_task_status requires plan_name")
        if not task_id.strip():
            raise MCPError("record_task_status requires task_id")
        if not who.strip():
            raise MCPError(
                "record_task_status requires who — a recorded status names who decided it"
            )

        self._plan_dir(plan_name)  # refuse an unknown plan before recording

        try:
            self._planner.record_status(
                plan_name, task_id, status, who, notes=notes,
            )
        except PlannerError as e:
            raise MCPError(str(e)) from e

        return {
            "status": "recorded",
            "task_id": task_id,
            "plan": plan_name,
            "recorded_status": status,
            "who": who,
            "notes": notes,
            "judged": False,
            "instruction": (
                "Recorded as an operator's account, not a validator verdict. "
                "The plan's status.json is updated; read it with "
                "get_plan(plan_name). This does not satisfy the engine's quorum "
                "and does not complete any work the loop has not completed."
            ),
        }

    def handle_run_plan(self, arguments: Dict[str, Any], progress_sink=None) -> dict:
        """Start a plan run as a job and return its id, without blocking.

        The plan's structure is verified here, at the run boundary, rather
        than being merely encouraged earlier: a plan that does not conform
        raises before anything spawns. ``validate_plan`` performs the same
        conformance check and exists so a plan can be checked while it is
        being authored; it is not an authorisation step and calling it is not
        a precondition for running.

        Starting the run is all this does. It hands the CLI's plan-run path to
        the job system and returns the job id at once — the caller follows it
        with ``get_job_status`` / ``list_jobs`` / ``get_job_logs``, the same
        machinery every other dispatched unit uses, while per-task detail stays
        in ``get_plan``. Blocking here was the bug: a wave takes five to forty
        minutes, an MCP call caps at 180 seconds, so a blocking run is reported
        as a timeout while the run itself carries on — a caller told nothing
        about work that is going fine.

        No token gates this call, at the surface or anywhere else: no MCP
        tool demands one from the caller (ADR 047). A plan run is not itself
        a mutation — it starts the CLI's plan-run path, and every task that
        path dispatches passes the engine's validator quorum and consumes
        its own token at the loop's own execute boundary. The guarantee
        therefore holds per task, where irreversible work actually begins;
        gating the start would only have let a verdict about one unrelated
        task stand in for authorisation of an entire plan.

        A caller that genuinely wants to block — a test, a script — may pass
        ``wait`` true (with an optional ``timeout``); that is opt-in and never
        the default. ``wait`` returns the run's final status once it ends, or
        raises naming the still-running job if the wait itself expires.
        """
        from snodo.mcp.server import MCPError

        plan_name = str(arguments.get("plan_name") or "")
        plan_dir = self._plan_dir(plan_name)

        validation = self._validation(plan_dir)
        if not validation["valid"]:
            raise MCPError(
                f"Plan '{plan_name}' failed validation and was not run: "
                + "; ".join(validation["errors"])
                + ". Fix the plan, call validate_plan, then run_plan again."
            )

        from snodo.jobs import JobError, JobManager

        task_args: Dict[str, Any] = {
            "plan_name": plan_name,
            "cwd": self.server.project_root,
            "trigger": "mcp",
        }
        protocol = arguments.get("protocol")
        if protocol:
            task_args["protocol"] = str(protocol)
        wave = arguments.get("wave")
        if wave is not None:
            task_args["wave"] = wave
        model = arguments.get("model")
        if model:
            task_args["model"] = str(model)
        if arguments.get("no_isolation"):
            task_args["no_isolation"] = True
        if arguments.get("mock"):
            task_args["mock"] = True

        job_mgr = JobManager(self.server.project_root)
        try:
            job_id = job_mgr.submit(task_args)
        except (JobError, ValueError, OSError) as e:
            raise MCPError(
                f"Failed to start plan run for '{plan_name}': {e}"
            ) from e

        with open(plan_dir / "plan.yml") as f:
            plan_data = yaml.safe_load(f) or {}
        self.server._audit("plan_proposed", {
            "op": "plan_proposed",
            **plan_history_shape(plan_data, plan_name),
        })
        self.server._audit("plan_run", {
            "op": "plan_run",
            **plan_history_shape(plan_data, plan_name),
            "trigger": "mcp",
        })

        if arguments.get("wait"):
            return self._wait_for_plan_run(
                job_mgr, job_id, plan_name, validation, arguments, progress_sink,
            )

        return {
            "plan": plan_name,
            "status": "accepted",
            "job_id": job_id,
            "validation": validation,
            "instruction": (
                "Plan run started. Poll get_job_status(job_id) until its status "
                "is completed / failed / unmerged; read output with "
                "get_job_logs(job_id). Per-task detail stays in get_plan(plan_name)."
            ),
        }

    def _wait_for_plan_run(
        self, job_mgr, job_id: str, plan_name: str,
        validation: dict, arguments: Dict[str, Any], progress_sink=None,
    ) -> dict:
        """Opt-in blocking wait for a plan-run job (used by tests/scripts)."""
        from snodo.jobs import JobError
        from snodo.mcp.server import MCPError

        raw_timeout = arguments.get("timeout")
        try:
            timeout = (
                float(raw_timeout) if raw_timeout is not None
                else _DEFAULT_WAIT_SECONDS
            )
        except (TypeError, ValueError):
            timeout = _DEFAULT_WAIT_SECONDS

        if progress_sink is None:
            try:
                final = job_mgr.wait_for(job_id, timeout=timeout)
            except JobError as e:
                raise MCPError(
                    f"Plan run '{plan_name}' (job {job_id}) was still running after "
                    f"{timeout:.0f}s; follow it with get_job_status({job_id}) and "
                    f"get_plan('{plan_name}')."
                ) from e
        else:
            final = self._wait_with_progress(
                job_mgr, job_id, plan_name, timeout, progress_sink,
            )

        exit_code = final.get("exit_code")
        result: Dict[str, Any] = {
            "plan": plan_name,
            "job_id": job_id,
            "status": final.get("status", "unknown"),
            "exit_code": exit_code,
            "tasks": self._task_statuses(plan_name),
            "validation": validation,
        }
        if exit_code != 0:
            result["output_tail"] = _tail(
                job_mgr.get_logs(job_id, stream="stdout") or ""
            )
            result["stderr_tail"] = _tail(
                job_mgr.get_logs(job_id, stream="stderr") or ""
            )
        return result

    def _wait_with_progress(self, job_mgr, job_id: str, plan_name: str,
                            timeout: float, progress_sink) -> dict:
        """Wait for the run while reporting task status transitions."""
        from snodo.mcp.server import MCPError
        from snodo.jobs import JobError
        deadline = time.monotonic() + timeout
        seen = self._plan_progress_snapshot(plan_name, progress_sink=None)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(job_mgr.wait_for, job_id, timeout=timeout)
            while True:
                self._plan_progress_snapshot(plan_name, progress_sink=progress_sink, seen=seen)
                if future.done():
                    try:
                        final = future.result()
                    except JobError as e:
                        raise MCPError(
                            f"Plan run '{plan_name}' (job {job_id}) was still running after "
                            f"{timeout:.0f}s; follow it with get_job_status({job_id}) and "
                            f"get_plan('{plan_name}')."
                        ) from e
                    self._plan_progress_snapshot(
                        plan_name, progress_sink=progress_sink, seen=seen,
                    )
                    return final
                if time.monotonic() >= deadline:
                    future.cancel()
                    raise MCPError(
                        f"Plan run '{plan_name}' (job {job_id}) was still running after "
                        f"{timeout:.0f}s; follow it with get_job_status({job_id}) and "
                        f"get_plan('{plan_name}')."
                    )
                time.sleep(0.1)

    def _plan_progress_snapshot(self, plan_name: str, progress_sink=None,
                                seen: Optional[dict[str, str]] = None) -> dict[str, str]:
        """Read task status and optionally emit only changes from *seen*."""
        from snodo.jobs import index_plan_jobs

        current = self._task_statuses(plan_name)
        if seen is None:
            return current
        try:
            _, jobs = index_plan_jobs(str(self._planner.project_root), plan_name)
            for task_id, status in current.items():
                if seen.get(task_id) == status:
                    continue
                seen[task_id] = status
                job = jobs.get(task_id, {})
                suffix = f" (job {job['id']})" if job.get("id") else ""
                progress_sink(f"[{task_id}] {status}{suffix}")
        except Exception as e:  # noqa: BLE001 - progress is observational
            logger.debug("Could not report plan progress: %s", e)
        return current

    def tool_handlers(self) -> dict:
        return {
            "propose_plan": self.handle_propose_plan,
            "get_plan": self.handle_get_plan,
            "run_plan": self.handle_run_plan,
            "record_task_status": self.handle_record_task_status,
        }
