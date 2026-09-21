"""Planner MCP server for plan decomposition and management.

FILE: snodo/mcp/planner.py (Task 4.2)

Implements planning operations for planner mode:
- decompose: Create plan structure from intent
- generate_spec: Write task spec files into plan
- validate_plan: Validate plan completeness
- get_plan, list_plans, get_status, update_status: Plan management

Plans live in .snodo/plans/<plan_name>/ with:
- plan.yml: waves, dependencies, intent
- status.json: task states
- wave_N/: task spec files
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from snodo.compiler.models import Plan
from snodo.compiler.verifier import (
    verify_plan,
    PlanWellFormednessError as _BasePlanWellFormednessError,
)
from snodo.mcp.status import TASK_STATUSES

_logger = logging.getLogger(__name__)

#: The audit event for a task an operator recorded as completed outside the
#: loop. `snodo task complete` has written this event since #287 and the MCP
#: `record_task_status` tool writes the same one: the record is the operator's,
#: whichever surface carried it, and a reader cannot — and should not — tell
#: the two apart.
HAND_COMPLETED_EVENT = "task_completed_by_hand"

#: The audit event for any other status an operator records outside the loop.
#: Kept distinct from the completion event so `snodo task show` never reports a
#: blocked or errored task as hand-completed; the provenance fields are the
#: same, and the `status` field says which status was recorded.
HAND_STATUS_EVENT = "task_status_recorded_by_hand"


def resolve_audit_log(project_root: Any, audit_log: Any) -> Any:
    """The audit log a record should be written to.

    An injected log wins (the MCP server passes one when it has it); otherwise
    the project's own ``.snodo/audit.log`` is resolved the same way the CLI
    resolves it, so a record lands in the project's chain regardless of which
    surface made it. Returns ``None`` when no log can be resolved — the caller
    reports that rather than silently recording nothing.
    """
    if audit_log is not None:
        return audit_log
    try:
        from snodo.infrastructure.audit import get_audit_log
        path = Path(project_root) / ".snodo" / "audit.log"
        return get_audit_log(str(path) if path.exists() else None)
    except Exception as exc:  # noqa: BLE001 — an unavailable log is reported, not hidden
        _logger.debug("Could not resolve audit log for %s: %s", project_root, exc)
        return None


def hand_record_event(
    task_id: str,
    who: str,
    recorded_at: str,
    *,
    plan: Optional[str] = None,
    notes: Optional[str] = None,
    status: str = "completed",
) -> tuple[str, Dict[str, Any]]:
    """Build the ``(event_type, data)`` for an operator's status record.

    The one builder behind both surfaces. A recorded status is an operator's
    account, so every event carries ``judged: False``, ``engine_judged: False``
    and ``outside_loop: True`` — a reader can tell it from a status the loop
    itself wrote. It records what a human decided and decides nothing.
    """
    event_type = (
        HAND_COMPLETED_EVENT if status == "completed" else HAND_STATUS_EVENT
    )
    data: Dict[str, Any] = {
        "op": event_type,
        "task_ref": task_id,
        "who": who,
        "recorded_at": recorded_at,
        "timestamp": recorded_at,
        "judged": False,
        "engine_judged": False,
        "outside_loop": True,
    }
    if plan:
        data["plan"] = plan
    if notes:
        data["notes"] = notes
    if status != "completed":
        data["status"] = status
    return event_type, data


class PlannerError(Exception):
    """Raised when a planner operation fails."""


class PlanWellFormednessError(_BasePlanWellFormednessError, PlannerError):
    """Raised when a plan fails well-formedness verification at load time."""


class PlannerMCP:
    """MCP server for plan decomposition and management.

    Operates on .snodo/plans/ directory within project root.
    Plans are the source of truth for multi-task execution.
    """

    def __init__(self, project_root: str, audit_log: Any = None):
        """Initialize planner MCP with project root.

        Args:
            project_root: Absolute path to project root directory
            audit_log: Optional AuditLog for event logging
        """
        self.project_root = Path(project_root).resolve()
        self.plans_dir = self.project_root / ".snodo" / "plans"
        self._audit_log = audit_log

        if not self.project_root.exists():
            raise ValueError(f"Project root does not exist: {self.project_root}")

        if not self.project_root.is_dir():
            raise ValueError(f"Project root is not a directory: {self.project_root}")

    def _audit(self, event_type: str, data: Dict[str, Any]) -> None:
        """Log to injected audit log if available."""
        if self._audit_log is not None:
            self._audit_log.append_event(event_type, data)

    @staticmethod
    def _normalize_task_entry(entry: Any) -> dict:
        """Normalize a status.json task entry to dict format.

        Handles both legacy string format and new dict format.

        Args:
            entry: Either a status string or a dict with metadata

        Returns:
            Normalized dict with status, parent_task_ref, depth, spec_hash
        """
        if isinstance(entry, str):
            return {
                "status": entry,
                "parent_task_ref": None,
                "depth": 0,
                "spec_hash": None,
            }
        return {
            "status": entry.get("status", "pending"),
            "parent_task_ref": entry.get("parent_task_ref"),
            "depth": entry.get("depth", 0),
            "spec_hash": entry.get("spec_hash"),
        }

    def _get_task_status(self, plan_name: str, task_id: str) -> Optional[dict]:
        """Get normalized task entry from status.json.

        Args:
            plan_name: Plan name
            task_id: Task identifier

        Returns:
            Normalized task dict, or None if task not found
        """
        status = self.get_status(plan_name)
        entry = status.get("tasks", {}).get(task_id)
        if entry is None:
            return None
        return self._normalize_task_entry(entry)

    @staticmethod
    def _normalize_spec(spec: str) -> str:
        """Normalize a spec string for comparison.

        Strips whitespace and normalizes line endings.
        """
        return spec.strip().replace("\r\n", "\n")

    def _read_task_spec(self, plan_name: str, task_id: str) -> Optional[str]:
        """Read a task's spec file from disk.

        Args:
            plan_name: Plan name
            task_id: Task identifier

        Returns:
            Spec content, or None if file not found
        """
        if "." not in task_id:
            return None
        wave_str = task_id.split(".")[0]
        try:
            wave_num = int(wave_str)
        except ValueError:
            return None
        spec_file = self.plans_dir / plan_name / f"wave_{wave_num}" / f"{task_id}_task.md"
        if not spec_file.exists():
            return None
        return spec_file.read_text()

    def _check_cycle(self, plan_name: str, spec: str, parent_ref: str) -> None:
        """Walk ancestor chain and check for spec cycles.

        Args:
            plan_name: Plan name (plan-scoped lookup)
            spec: Proposed normalized spec
            parent_ref: Starting parent task ref

        Raises:
            PlannerError: If cycle detected
        """
        current_ref: str | None = parent_ref
        visited: set[str] = set()
        while current_ref and current_ref not in visited:
            visited.add(current_ref)
            ancestor_spec = self._read_task_spec(plan_name, current_ref)
            if ancestor_spec and self._normalize_spec(ancestor_spec) == spec:
                raise PlannerError(
                    f"cycle_detected: proposed spec matches ancestor {current_ref}"
                )
            ancestor_entry = self._get_task_status(plan_name, current_ref)
            if not ancestor_entry:
                break
            current_ref = ancestor_entry.get("parent_task_ref")

    def decompose(self, intent: str, plan_name: str, waves: int = 1) -> dict:
        """Create an empty plan scaffold on disk with N empty wave slots.

        LLM-based automatic decomposition is deferred. Creates the plan
        directory, plan.yml scaffold with N sequential empty wave slots
        (default 1, making the scaffold immediately valid), and status.json.

        Args:
            intent: The intent/goal for the plan
            plan_name: Name for the plan (used as directory name)
            waves: Number of empty waves to scaffold (default: 1)

        Returns:
            Plan data dict with name, intent, waves

        Raises:
            PlannerError: If plan already exists, waves is negative/invalid,
                or creation fails
        """
        if not intent or not intent.strip():
            raise PlannerError("Intent cannot be empty")

        if not plan_name or not plan_name.strip():
            raise PlannerError("Plan name cannot be empty")

        if not isinstance(waves, int) or isinstance(waves, bool) or waves < 0:
            raise PlannerError("waves must be a non-negative integer")

        plan_dir = self.plans_dir / plan_name

        if plan_dir.exists():
            raise PlannerError(f"Plan already exists: {plan_name}")

        try:
            plan_dir.mkdir(parents=True)
        except OSError as e:
            raise PlannerError(f"Failed to create plan directory: {e}") from e

        waves_list = [
            {
                "id": i,
                "depends_on": [i - 1] if i > 1 else [],
                "tasks": [],
            }
            for i in range(1, waves + 1)
        ]

        plan_data = {
            "name": plan_name,
            "intent": intent,
            "waves": waves_list,
        }

        plan_file = plan_dir / "plan.yml"
        with open(plan_file, "w") as f:
            yaml.dump(plan_data, f, default_flow_style=False)

        status_data: dict = {"tasks": {}}
        status_file = plan_dir / "status.json"
        with open(status_file, "w") as f:
            json.dump(status_data, f, indent=2)

        return plan_data

    def generate_spec(
        self,
        plan_name: str,
        task_id: str,
        spec: str,
        parent_task_ref: Optional[str] = None,
        replace: bool = False,
    ) -> str:
        """Write a task specification file into a plan.

        Task ID format: <wave>.<seq>_<name> (e.g., "1.1_models").
        Creates wave directory if needed. Updates plan.yml and status.json.

        Args:
            plan_name: Plan name
            task_id: Task identifier (e.g., "1.1_models")
            spec: Task specification content (markdown)
            parent_task_ref: ID of parent task (plan-scoped)
            replace: Allow overwriting existing task spec

        Returns:
            Path to the created spec file (relative to project root)

        Raises:
            PlannerError: If plan not found, task_id format invalid,
                parent not found, depth exceeded, cycle detected,
                or task exists without replace=True
        """
        plan_dir = self.plans_dir / plan_name
        if not plan_dir.exists():
            raise PlannerError(f"Plan not found: {plan_name}")
        if not task_id or not task_id.strip():
            raise PlannerError("Task ID cannot be empty")
        if not spec or not spec.strip():
            raise PlannerError("Spec cannot be empty")

        wave_num = self._parse_wave_num(task_id)

        status_file = plan_dir / "status.json"
        with open(status_file) as f:
            status_data = json.load(f)
        tasks = status_data.setdefault("tasks", {})

        old_spec_hash = self._handle_existing_task(
            tasks, task_id, parent_task_ref, plan_name, replace
        )
        normalized_spec = self._normalize_spec(spec)
        new_depth = self._resolve_parent_and_depth(
            plan_name, task_id, parent_task_ref, normalized_spec
        )
        spec_hash = hashlib.sha256(normalized_spec.encode()).hexdigest()[:16]

        spec_file = self._write_spec_and_update(
            plan_dir, wave_num, task_id, spec,
            status_file, status_data, tasks,
            parent_task_ref, new_depth, spec_hash,
        )

        if old_spec_hash is not None:
            self._audit("task_replaced", {
                "task_id": task_id,
                "old_spec_hash": old_spec_hash,
                "new_spec_hash": spec_hash,
                "plan_name": plan_name,
            })
        self._audit("task_added", {
            "task_id": task_id,
            "parent_task_ref": parent_task_ref,
            "depth": new_depth,
            "spec_hash": spec_hash,
            "plan_name": plan_name,
        })
        return str(spec_file.relative_to(self.project_root))

    def _parse_wave_num(self, task_id: str) -> int:
        """Parse and return the wave number from a task_id.

        Raises:
            PlannerError: If task_id has no name or wave part is not an integer
        """
        if "." not in task_id:
            raise PlannerError(
                f"Invalid task_id format: {task_id}. Expected a named ID such as "
                "1.1_models (<wave>.<seq>_<name>)"
            )
        wave_str = task_id.split(".")[0]
        try:
            wave_num = int(wave_str)
        except ValueError as e:
            raise PlannerError(f"Invalid wave number in task_id: {task_id}") from e

        name_part = task_id.split(".", 1)[1].split("_", 1)
        if len(name_part) < 2 or not name_part[1].strip():
            raise PlannerError(
                f"Invalid task_id format: {task_id}. Expected a named ID such as "
                "1.1_models (<wave>.<seq>_<name>)"
            )
        return wave_num

    def _handle_existing_task(
        self,
        tasks: dict,
        task_id: str,
        parent_task_ref: Optional[str],
        plan_name: str,
        replace: bool,
    ) -> Optional[str]:
        """Guard against duplicate task_id and capture the old spec_hash for replace.

        Raises:
            PlannerError: If task already exists and replace is False

        Returns:
            old_spec_hash string if replacing an existing task, else None
        """
        existing_entry = tasks.get(task_id)
        if existing_entry is not None and not replace:
            self._audit("task_add_rejected", {
                "task_id": task_id,
                "parent_task_ref": parent_task_ref,
                "depth": 0,
                "reason": "task_exists",
                "plan_name": plan_name,
            })
            raise PlannerError(f"task_exists: {task_id} already exists in plan {plan_name}")
        if existing_entry is not None and replace:
            normalized_existing = self._normalize_task_entry(existing_entry)
            return normalized_existing.get("spec_hash")
        return None

    def _resolve_parent_and_depth(
        self,
        plan_name: str,
        task_id: str,
        parent_task_ref: Optional[str],
        normalized_spec: str,
    ) -> int:
        """Look up parent, enforce depth limit, and check for spec cycles.

        Raises:
            PlannerError: If parent not found, depth limit exceeded, or cycle detected

        Returns:
            Computed depth for the new task (0 if no parent)
        """
        if not parent_task_ref:
            return 0
        parent_entry = self._get_task_status(plan_name, parent_task_ref)
        if parent_entry is None:
            self._audit("task_add_rejected", {
                "task_id": task_id,
                "parent_task_ref": parent_task_ref,
                "depth": 0,
                "reason": "parent_not_found",
                "plan_name": plan_name,
            })
            raise PlannerError(
                f"parent_not_found: {parent_task_ref} not in plan {plan_name}"
            )
        new_depth = parent_entry["depth"] + 1
        from snodo.config import ConfigManager
        max_depth = ConfigManager().load().get("engine", {}).get("max_subtask_depth", 3)
        if new_depth > max_depth:
            self._audit("task_add_rejected", {
                "task_id": task_id,
                "parent_task_ref": parent_task_ref,
                "depth": new_depth,
                "reason": "max_subtask_depth_exceeded",
                "plan_name": plan_name,
            })
            raise PlannerError(
                f"max_subtask_depth_exceeded: depth {new_depth} > max {max_depth}"
            )
        self._check_cycle(plan_name, normalized_spec, parent_task_ref)
        return new_depth

    def _write_spec_and_update(
        self,
        plan_dir: Path,
        wave_num: int,
        task_id: str,
        spec: str,
        status_file: Path,
        status_data: dict,
        tasks: dict,
        parent_task_ref: Optional[str],
        new_depth: int,
        spec_hash: str,
    ) -> Path:
        """Write the spec file, update plan.yml, and persist status.json.

        Returns:
            Path to the written spec file
        """
        wave_dir = plan_dir / f"wave_{wave_num}"
        wave_dir.mkdir(exist_ok=True)
        spec_file = wave_dir / f"{task_id}_task.md"
        spec_file.write_text(spec)

        plan_file = plan_dir / "plan.yml"
        with open(plan_file) as f:
            plan_data = yaml.safe_load(f) or {}
        waves = plan_data.setdefault("waves", [])
        wave_entry = self._find_or_create_wave(waves, wave_num)
        if task_id not in wave_entry["tasks"]:
            wave_entry["tasks"].append(task_id)
        with open(plan_file, "w") as f:
            yaml.dump(plan_data, f, default_flow_style=False)

        tasks[task_id] = {
            "status": "pending",
            "parent_task_ref": parent_task_ref,
            "depth": new_depth,
            "spec_hash": spec_hash,
        }
        with open(status_file, "w") as f:
            json.dump(status_data, f, indent=2)

        return spec_file

    @staticmethod
    def _find_or_create_wave(waves: list, wave_num: int) -> dict:
        """Find an existing wave entry or create a new one.

        Args:
            waves: List of wave dicts
            wave_num: Wave number to find or create

        Returns:
            The wave dict (existing or newly created)
        """
        for w in waves:
            if w.get("id") == wave_num:
                return w

        new_wave = {"id": wave_num, "tasks": []}
        waves.append(new_wave)
        waves.sort(key=lambda w: w["id"])
        return new_wave

    def validate_plan(self, plan_name: str) -> dict:
        """Validate a plan's completeness and structure.

        Validates the Plan model using verify_plan().

        Args:
            plan_name: Plan name to validate

        Returns:
            Dict with valid (bool), errors (list), warnings (list),
            wave_count, task_count

        Raises:
            PlannerError: If plan not found
        """
        plan_dir = self.plans_dir / plan_name
        if not plan_dir.exists():
            raise PlannerError(f"Plan not found: {plan_name}")

        plan_file = plan_dir / "plan.yml"
        if not plan_file.exists():
            return {
                "valid": False,
                "errors": ["plan.yml not found"],
                "warnings": [],
                "wave_count": 0,
                "task_count": 0,
            }

        with open(plan_file) as f:
            plan_data = yaml.safe_load(f) or {}

        status_file = plan_dir / "status.json"
        status_data = {}
        if status_file.exists():
            try:
                with open(status_file) as f:
                    status_data = json.load(f) or {}
            except Exception:
                status_data = {}

        plan = Plan.from_dict(plan_data, status_data)
        result = verify_plan(plan, plan_dir=plan_dir)

        return {
            "valid": result.passed,
            "errors": result.errors,
            "warnings": result.warnings,
            "wave_count": len(plan.waves),
            "task_count": sum(len(w.tasks) for w in plan.waves),
        }

    def get_plan(self, plan_name: str) -> Plan:
        """Load and verify a plan's data.

        Args:
            plan_name: Plan name

        Returns:
            Plan model instance

        Raises:
            PlannerError: If plan not found
            PlanWellFormednessError: If plan fails verification
        """
        plan_dir = self.plans_dir / plan_name
        if not plan_dir.exists():
            raise PlannerError(f"Plan not found: {plan_name}")

        plan_file = plan_dir / "plan.yml"
        if not plan_file.exists():
            raise PlannerError(f"plan.yml not found in: {plan_name}")

        with open(plan_file) as f:
            plan_data = yaml.safe_load(f) or {}

        status_file = plan_dir / "status.json"
        status_data = {}
        if status_file.exists():
            try:
                with open(status_file) as f:
                    status_data = json.load(f) or {}
            except Exception:
                status_data = {}

        plan = Plan.from_dict(plan_data, status_data)
        result = verify_plan(plan, plan_dir=plan_dir)
        if not result.passed:
            raise PlanWellFormednessError(result.errors)

        return plan

    def list_plans(self) -> List[dict]:
        """List all plans with summary info.

        Returns:
            List of dicts with name, intent, wave_count, task_count
        """
        if not self.plans_dir.exists():
            return []

        plans = []
        for plan_dir in sorted(self.plans_dir.iterdir()):
            if not plan_dir.is_dir():
                continue

            plan_file = plan_dir / "plan.yml"
            if not plan_file.exists():
                continue

            try:
                with open(plan_file) as f:
                    data = yaml.safe_load(f)
                if not isinstance(data, dict):
                    data = {}
            except Exception:
                data = {}

            waves = data.get("waves", []) if isinstance(data.get("waves"), list) else []
            task_count = sum(len(w.get("tasks", [])) for w in waves if isinstance(w, dict))

            # Load status counts (normalize entries)
            status_file = plan_dir / "status.json"
            status_counts: dict[str, int] = {}
            if status_file.exists():
                try:
                    with open(status_file) as f:
                        status_data = json.load(f) or {}
                    if isinstance(status_data, dict):
                        for entry in status_data.get("tasks", {}).values():
                            normalized = self._normalize_task_entry(entry)
                            s = normalized["status"]
                            status_counts[s] = status_counts.get(s, 0) + 1
                except Exception as e:
                    # A plan whose status file cannot be read still lists, but
                    # with no counts — say so rather than showing zeros as fact.
                    _logger.warning(
                        "Could not read plan status %s: %s", status_file, e,
                    )

            plans.append({
                "name": data.get("name", plan_dir.name),
                "intent": data.get("intent", ""),
                "wave_count": len(waves),
                "task_count": task_count,
                "status_counts": status_counts,
            })

        return plans

    def get_status(self, plan_name: str) -> dict:
        """Load a plan's status.

        Args:
            plan_name: Plan name

        Returns:
            Status dict with tasks mapping

        Raises:
            PlannerError: If plan not found
        """
        plan_dir = self.plans_dir / plan_name
        if not plan_dir.exists():
            raise PlannerError(f"Plan not found: {plan_name}")

        status_file = plan_dir / "status.json"
        if not status_file.exists():
            return {"tasks": {}}

        with open(status_file) as f:
            return json.load(f)

    def update_status(
        self,
        plan_name: str,
        task_id: str,
        status: str,
        **metadata: Any,
    ) -> None:
        """Update a task's status in the plan.

        Args:
            plan_name: Plan name
            task_id: Task identifier
            status: New status (pending/in_progress/completed/blocked/errored).
            **metadata: Additional metadata (e.g. completed_by, completed_at, judged).

              ``errored`` is for tasks that halted without being judged — an
              operational fault (``validator_error`` / ``internal_error``), or
              a halted task that could not be merged. Unlike ``blocked`` it
              never routes the next attempt through the retry path with the
              previous failure handed to a faultless coder (issue #231).

        Raises:
            PlannerError: If plan not found or invalid status
        """
        self._check_status(status)

        plan_dir = self.plans_dir / plan_name
        if not plan_dir.exists():
            raise PlannerError(f"Plan not found: {plan_name}")

        from snodo.infrastructure.state import atomic_update_json

        def _updater(data: dict) -> None:
            tasks = data.setdefault("tasks", {})
            existing = tasks.get(task_id)
            if isinstance(existing, dict):
                existing["status"] = status
                for k, v in metadata.items():
                    existing[k] = v
            elif metadata:
                entry = {"status": status}
                for k, v in metadata.items():
                    entry[k] = v
                tasks[task_id] = entry
            else:
                tasks[task_id] = status

        atomic_update_json(plan_dir, "status.json", _updater, strict=True)

        # Liveness (Fixes #291): a plan/task status write changes what is
        # running, and it does not append an audit event, so the writer says
        # so here. A terminal write (completed/blocked/errored/unmerged) is
        # the only record that will tell the far side the task stopped, so it
        # forces past the throttle; a start or a not-yet-started task pushes
        # at the normal once-per-minute budget, coalescing with its neighbours.
        # A session with nothing started, and sync disabled, send nothing.
        # The snapshot's ``last_event`` will not move for this write — it
        # reports the last audit event, and this appends none — but its
        # ``last_activity_at`` will: the file this write rewrote carries the
        # mark of when the change happened (Fixes #324).
        try:
            from snodo.infrastructure import cloud_liveness
            cloud_liveness.note_transition(
                str(self.project_root),
                force=status in cloud_liveness.TERMINAL_PLAN_STATUSES,
            )
        except Exception as exc:  # noqa: BLE001 — liveness never breaks a status write
            _logger.debug("Liveness note after status write skipped: %s", exc)

    @staticmethod
    def _check_status(status: str) -> None:
        """Refuse a status outside the plan vocabulary.

        The one place the vocabulary is checked, so every writer — the loop's
        own ``update_status`` and an operator's ``record_status`` — refuses
        exactly the same values. Widening it is a decision (ADR 045), not an
        edit: ``scripts/enforce_vocabularies.py`` reads this set.
        """
        valid_statuses = TASK_STATUSES
        if status not in valid_statuses:
            raise PlannerError(f"Invalid status: {status}. Must be one of {valid_statuses}")

    def record_status(
        self,
        plan_name: Optional[str],
        task_id: str,
        status: str,
        who: str,
        *,
        notes: Optional[str] = None,
        recorded_at: Optional[str] = None,
    ) -> dict:
        """Record a status an operator decided on, outside the loop.

        The one implementation behind both surfaces: ``snodo task complete``
        and the MCP ``record_task_status`` tool both call this, so the plan
        state they leave and the audit event they append cannot drift apart.
        The status vocabulary is :meth:`update_status`'s — a value it would
        refuse is refused here too, and no new status is introduced.

        This records what a human decided; it decides nothing. The audit event
        carries ``judged: False`` and ``outside_loop: True``, so a reader can
        tell an operator's account from a status the loop itself wrote, and a
        recorded ``completed`` is never evidence a validator quorum passed.

        Args:
            plan_name: Plan the task belongs to, or None for an audit-only
                record (the CLI's task with no discoverable plan).
            task_id: Task identifier.
            status: One of :meth:`update_status`'s statuses.
            who: The person (or role) the status is recorded for.
            notes: Optional reason — the "why" beside the "who".
            recorded_at: ISO timestamp; defaults to now (UTC).

        Returns:
            ``{"event_type", "status", "task_id", "plan", "who",
            "recorded_at", "notes", "judged"}``.

        Raises:
            PlannerError: If a field is missing, the status is not in the
                vocabulary, the plan is unknown, or the audit log is
                unavailable.
        """
        if not task_id or not str(task_id).strip():
            raise PlannerError("task_id is required")
        if not who or not str(who).strip():
            raise PlannerError("who is required — a recorded status names who decided it")

        timestamp = recorded_at or datetime.now(timezone.utc).isoformat()

        # update_status validates the status vocabulary and raises before
        # anything is written, so an invalid status never reaches the plan or
        # the audit log (the same guardrail the CLI path has).
        if plan_name:
            if status == "completed":
                metadata = {
                    "completed_by": who,
                    "completed_at": timestamp,
                    "judged": False,
                }
            else:
                metadata = {
                    "recorded_by": who,
                    "recorded_at": timestamp,
                    "judged": False,
                }
            self.update_status(plan_name, task_id, status, **metadata)

        event_type, event_data = hand_record_event(
            task_id, who, timestamp,
            plan=plan_name, notes=notes, status=status,
        )
        audit_log = resolve_audit_log(self.project_root, self._audit_log)
        if audit_log is None:
            raise PlannerError("Audit log unavailable.")
        audit_log.append_event(event_type, event_data)

        return {
            "event_type": event_type,
            "status": status,
            "task_id": task_id,
            "plan": plan_name,
            "who": who,
            "recorded_at": timestamp,
            "notes": notes,
            "judged": False,
        }

    def recompute_depths(self, plan_name: str) -> dict:
        """Two-pass depth recompute for legacy plans.

        Pass 1: Set depth=0 for tasks without parent_task_ref.
        Pass 2: Iterate until stable — set depth = parent.depth + 1.
        Updates status.json.

        Args:
            plan_name: Plan name

        Returns:
            Dict mapping task_id -> computed depth

        Raises:
            PlannerError: If plan not found
        """
        plan_dir = self.plans_dir / plan_name
        if not plan_dir.exists():
            raise PlannerError(f"Plan not found: {plan_name}")

        status_file = plan_dir / "status.json"
        if not status_file.exists():
            return {}

        with open(status_file) as f:
            status_data = json.load(f)

        tasks = status_data.get("tasks", {})
        if not tasks:
            return {}

        normalized: Dict[str, dict] = {}
        for tid, entry in tasks.items():
            normalized[tid] = self._normalize_task_entry(entry)

        self._propagate_depths(normalized)

        for tid, entry in normalized.items():
            tasks[tid] = entry

        with open(status_file, "w") as f:
            json.dump(status_data, f, indent=2)

        return {tid: entry["depth"] for tid, entry in normalized.items()}

    @staticmethod
    def _propagate_depths(normalized: Dict[str, dict]) -> None:
        """Two-pass in-place depth propagation over a normalized task dict.

        Pass 1: roots (no parent_task_ref) get depth=0.
        Pass 2: iterate until stable — each child's depth = parent.depth + 1.
        """
        for entry in normalized.values():
            if not entry.get("parent_task_ref"):
                entry["depth"] = 0

        changed = True
        while changed:
            changed = False
            for entry in normalized.values():
                parent_ref = entry.get("parent_task_ref")
                if parent_ref and parent_ref in normalized:
                    expected = normalized[parent_ref]["depth"] + 1
                    if entry["depth"] != expected:
                        entry["depth"] = expected
                        changed = True
