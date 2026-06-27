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
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


class PlannerError(Exception):
    """Raised when a planner operation fails."""


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

    def decompose(self, intent: str, plan_name: str) -> dict:
        """Create initial plan structure from intent.

        Creates the plan directory, plan.yml, and status.json.

        Args:
            intent: The intent/goal to decompose
            plan_name: Name for the plan (used as directory name)

        Returns:
            Plan data dict with name, intent, waves

        Raises:
            PlannerError: If plan already exists or creation fails
        """
        if not intent or not intent.strip():
            raise PlannerError("Intent cannot be empty")

        if not plan_name or not plan_name.strip():
            raise PlannerError("Plan name cannot be empty")

        plan_dir = self.plans_dir / plan_name

        if plan_dir.exists():
            raise PlannerError(f"Plan already exists: {plan_name}")

        try:
            plan_dir.mkdir(parents=True)
        except OSError as e:
            raise PlannerError(f"Failed to create plan directory: {e}")

        plan_data = {
            "name": plan_name,
            "intent": intent,
            "waves": [],
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
            PlannerError: If task_id has no dot or wave part is not an integer
        """
        if "." not in task_id:
            raise PlannerError(
                f"Invalid task_id format: {task_id}. Expected: <wave>.<seq>_<name>"
            )
        wave_str = task_id.split(".")[0]
        try:
            return int(wave_str)
        except ValueError:
            raise PlannerError(f"Invalid wave number in task_id: {task_id}")

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

        Checks: plan.yml exists, intent present, all waves have tasks,
        all tasks have spec files.

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
            return {"valid": False, "errors": ["plan.yml not found"],
                    "warnings": [], "wave_count": 0, "task_count": 0}

        with open(plan_file) as f:
            plan_data = yaml.safe_load(f) or {}

        errors: list[str] = []
        warnings: list[str] = []

        if not plan_data.get("intent"):
            errors.append("Missing intent")

        waves = plan_data.get("waves", [])
        if not waves:
            errors.append("No waves defined")

        task_count = self._validate_waves(waves, plan_dir, errors, warnings)

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "wave_count": len(waves),
            "task_count": task_count,
        }

    @staticmethod
    def _validate_waves(
        waves: list, plan_dir: Path, errors: list, warnings: list
    ) -> int:
        """Validate wave structure, spec files, and dependencies.

        Args:
            waves: List of wave dicts from plan.yml
            plan_dir: Path to plan directory
            errors: List to append errors to
            warnings: List to append warnings to

        Returns:
            Total task count
        """
        task_count = 0
        for wave in waves:
            wave_id = wave.get("id")
            tasks = wave.get("tasks", [])
            if not tasks:
                warnings.append(f"Wave {wave_id} has no tasks")

            wave_dir = plan_dir / f"wave_{wave_id}"
            for task_id in tasks:
                task_count += 1
                spec_file = wave_dir / f"{task_id}_task.md"
                if not spec_file.exists():
                    errors.append(f"Missing spec: {task_id}")

        # Check dependency references
        wave_ids = {w.get("id") for w in waves}
        for wave in waves:
            for dep in wave.get("depends_on", []):
                if dep not in wave_ids:
                    errors.append(f"Wave {wave.get('id')} depends on unknown wave {dep}")

        return task_count

    def get_plan(self, plan_name: str) -> dict:
        """Load a plan's data.

        Args:
            plan_name: Plan name

        Returns:
            Plan data dict from plan.yml

        Raises:
            PlannerError: If plan not found
        """
        plan_dir = self.plans_dir / plan_name
        if not plan_dir.exists():
            raise PlannerError(f"Plan not found: {plan_name}")

        plan_file = plan_dir / "plan.yml"
        if not plan_file.exists():
            raise PlannerError(f"plan.yml not found in: {plan_name}")

        with open(plan_file) as f:
            return yaml.safe_load(f) or {}

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

            with open(plan_file) as f:
                data = yaml.safe_load(f) or {}

            waves = data.get("waves", [])
            task_count = sum(len(w.get("tasks", [])) for w in waves)

            # Load status counts (normalize entries)
            status_file = plan_dir / "status.json"
            status_counts: dict[str, int] = {}
            if status_file.exists():
                with open(status_file) as f:
                    status_data = json.load(f)
                for entry in status_data.get("tasks", {}).values():
                    normalized = self._normalize_task_entry(entry)
                    s = normalized["status"]
                    status_counts[s] = status_counts.get(s, 0) + 1

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

    def update_status(self, plan_name: str, task_id: str, status: str) -> None:
        """Update a task's status in the plan.

        Args:
            plan_name: Plan name
            task_id: Task identifier
            status: New status (pending/in_progress/completed/blocked)

        Raises:
            PlannerError: If plan not found or invalid status
        """
        valid_statuses = {"pending", "in_progress", "completed", "blocked"}
        if status not in valid_statuses:
            raise PlannerError(f"Invalid status: {status}. Must be one of {valid_statuses}")

        plan_dir = self.plans_dir / plan_name
        if not plan_dir.exists():
            raise PlannerError(f"Plan not found: {plan_name}")

        status_file = plan_dir / "status.json"
        if status_file.exists():
            with open(status_file) as f:
                status_data = json.load(f)
        else:
            status_data = {"tasks": {}}

        tasks = status_data.setdefault("tasks", {})
        existing = tasks.get(task_id)
        if isinstance(existing, dict):
            existing["status"] = status
        else:
            tasks[task_id] = status

        with open(status_file, "w") as f:
            json.dump(status_data, f, indent=2)

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
