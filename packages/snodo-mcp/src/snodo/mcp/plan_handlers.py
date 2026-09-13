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
The run itself spawns the CLI's plan-run path — the engine loop keeps its
authority over every dispatched task; this opens no route around it. The
mcp layer may not import the app layer, so the plan runs as a subprocess,
mirroring how snodo.jobs.wrapper invokes the CLI.
"""

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

import yaml

from snodo.compiler.verifier import verify_plan_dir
from snodo.mcp.planner import PlannerError

logger = logging.getLogger(__name__)

#: Bound on run output echoed back to the caller; the plan directory and
#: job logs hold the full trail — a tool response must not scale with it.
_MAX_OUTPUT_TAIL_LINES = 50

#: Safety ceiling on one plan run. Real runs dispatch real coders; the
#: ceiling exists only so a wedged subprocess cannot pin a tool call forever.
_RUN_TIMEOUT_SECONDS = 3600.0


def _tail(text: str, lines: int = _MAX_OUTPUT_TAIL_LINES) -> str:
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
        result = verify_plan_dir(plan_dir)
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
            "plan_name": plan_name,
            "wave_count": len(plan_data.get("waves", [])),
            "mode": self.server._active_mode(),
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
            "validation": self._validation(plan_dir),
        }

    def handle_run_plan(self, arguments: Dict[str, Any]) -> dict:
        """Run a plan through the protocol loop.

        The plan's structure is verified here, at the run boundary, rather
        than being merely encouraged earlier: a plan that does not conform
        raises before anything spawns. ``validate_plan`` performs the same
        conformance check and exists so a plan can be checked while it is
        being authored; it is not an authorisation step and calling it is not
        a precondition for running.

        No validation token is required to reach this handler, and none is
        consumed. A plan run is not itself a mutation — it starts the CLI's
        plan-run path, and every task that path dispatches passes the engine's
        validator quorum and consumes its own token at its own dispatch
        boundary. WF1 therefore holds per task, where irreversible work
        actually begins. Gating here would have meant a token issued by
        ``validate_task`` for one unrelated task standing in for authorisation
        of an entire plan, which gates nothing while refusing callers who have
        no coherent way to comply.
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

        cmd = [
            sys.executable, "-u", "-m", "snodo", "plan", "run", plan_name,
            "--protocol", str(arguments.get("protocol") or ".snodo/protocol.yml"),
        ]
        wave = arguments.get("wave")
        if wave is not None:
            cmd.extend(["--wave", str(wave)])
        model = arguments.get("model")
        if model:
            cmd.extend(["--model", str(model)])
        if arguments.get("no_isolation"):
            cmd.append("--no-isolation")
        if arguments.get("mock"):
            cmd.append("--mock")

        env = {**os.environ, "SNODO_PROJECT_ROOT": self.server.project_root}
        try:
            proc = subprocess.run(  # noqa: S603 - argv list (no shell); plan name/model are single argv elements, never interpreted
                cmd,
                cwd=self.server.project_root,
                capture_output=True,
                text=True,
                env=env,
                timeout=_RUN_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as e:
            raise MCPError(
                f"Plan run for '{plan_name}' exceeded {_RUN_TIMEOUT_SECONDS:.0f}s "
                f"and was terminated; check plan status with get_plan."
            ) from e
        except OSError as e:
            raise MCPError(f"Failed to start plan run for '{plan_name}': {e}") from e

        self.server._audit("plan_run", {
            "op": "plan_run",
            "plan_name": plan_name,
            "exit_code": proc.returncode,
            "mode": self.server._active_mode(),
        })

        result: Dict[str, Any] = {
            "plan": plan_name,
            "status": "completed" if proc.returncode == 0 else "failed",
            "exit_code": proc.returncode,
            "tasks": self._task_statuses(plan_name),
            "validation": validation,
        }
        if proc.returncode != 0:
            result["output_tail"] = _tail(proc.stdout or "")
            result["stderr_tail"] = _tail(proc.stderr or "")
        return result

    def tool_handlers(self) -> dict:
        return {
            "propose_plan": self.handle_propose_plan,
            "get_plan": self.handle_get_plan,
            "run_plan": self.handle_run_plan,
        }
