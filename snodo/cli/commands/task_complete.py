"""Record that a task was completed by hand outside the loop.

FILE: snodo/cli/commands/task_complete.py
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from snodo.cli.json_output import emit_error, emit_json, schema_name

_logger = logging.getLogger(__name__)


def _resolve_who(project_root: str, explicit_who: Optional[str] = None) -> str:
    """Resolve the identity of the person recording the completion."""
    if explicit_who:
        return explicit_who

    who = os.environ.get("SNODO_USER") or os.environ.get("USER")
    if who:
        return who

    try:
        from snodo.tools.git import GitMCP
        git = GitMCP(project_root)
        git_user = git.repo.config_reader().get_value("user", "name", default=None)
        if git_user:
            return str(git_user)
    except Exception as e:
        _logger.debug("Could not resolve git user name: %s", e)

    try:
        import getpass
        return getpass.getuser()
    except Exception as e:
        _logger.debug("Could not resolve system user: %s", e)
        return "human"


def _discover_plan_for_task(project_root: str, task_id: str) -> list[str]:
    """Find all plans containing task_id in plan.yml waves or status.json tasks."""
    plans_dir = Path(project_root) / ".snodo" / "plans"
    if not plans_dir.is_dir():
        return []

    found = []
    for p_dir in sorted(plans_dir.iterdir()):
        if not p_dir.is_dir():
            continue
        # Check status.json
        s_file = p_dir / "status.json"
        if s_file.is_file():
            try:
                s_data = json.loads(s_file.read_text())
                if task_id in s_data.get("tasks", {}):
                    found.append(p_dir.name)
                    continue
            except Exception as e:
                _logger.debug("Could not read status.json in %s: %s", p_dir, e)
        # Check plan.yml
        p_file = p_dir / "plan.yml"
        if p_file.is_file():
            try:
                import yaml
                p_data = yaml.safe_load(p_file.read_text()) or {}
                for wave in p_data.get("waves", []):
                    if task_id in wave.get("tasks", []):
                        found.append(p_dir.name)
                        break
            except Exception as e:
                _logger.debug("Could not read plan.yml in %s: %s", p_dir, e)
    return found


def get_hand_completion_record(project_root: str, task_id: str) -> Optional[dict]:
    """Read the latest hand-completion audit event for *task_id*, if any."""
    try:
        from snodo.infrastructure.audit import get_audit_log
        audit_log_path = Path(project_root) / ".snodo" / "audit.log"
        if not audit_log_path.exists():
            return None
        audit_log = get_audit_log(str(audit_log_path))
        events = getattr(audit_log, "events", [])
        for ev in reversed(events):
            data = ev.data or {}
            op = data.get("op") or ev.event_type
            if op in ("task_completed_by_hand", "hand_completed"):
                ref = data.get("task_ref") or data.get("task_id")
                if ref == task_id:
                    return data
    except Exception as e:
        _logger.debug("Could not check hand completion for %s: %s", task_id, e)
    return None


def print_hand_completion_info(hand_completion: dict) -> None:
    """Print human-facing details of a hand completion."""
    print()
    print("Completed outside loop (by hand):")
    print(f"  who:         {hand_completion.get('who')}")
    print(f"  recorded_at: {hand_completion.get('recorded_at')}")
    print("  judged:      False (completed outside engine loop)")
    if hand_completion.get("notes"):
        print(f"  notes:       {hand_completion.get('notes')}")


def task_complete_command(args: Any) -> int:
    """Record that a person completed a task outside the loop.

    Records the completion in the audit log (who, when, unjudged by engine),
    updates the plan's status.json so subsequent waves advance, and clears any
    lingering failure context from the active session.
    """
    task_id = getattr(args, "task_id", "") or ""
    json_out = getattr(args, "json", False)
    plan_arg = getattr(args, "plan", None)
    who_arg = getattr(args, "who", None)
    notes_arg = getattr(args, "notes", None)

    if not task_id:
        if json_out:
            return emit_error("task_complete", "task_id is required", 1)
        print("Usage: snodo task complete <task_id> [--plan <name>] [--who <name>] [--notes <text>]", file=sys.stderr)
        return 1

    from snodo.cli.commands.task_cmd import resolve_project_root
    project_root = resolve_project_root()
    if project_root is None:
        if json_out:
            return emit_error("task_complete", "Not inside a snodo project.", 1)
        print("Not inside a snodo project.", file=sys.stderr)
        return 1

    who = _resolve_who(project_root, explicit_who=who_arg)
    recorded_at = datetime.now(timezone.utc).isoformat()

    plans_dir = Path(project_root) / ".snodo" / "plans"
    plan_name = plan_arg
    if plan_name:
        if not (plans_dir / plan_name).is_dir():
            msg = f"Plan '{plan_name}' not found."
            if json_out:
                return emit_error("task_complete", msg, 1)
            print(f"Error: {msg}", file=sys.stderr)
            return 1
    else:
        candidate_plans = _discover_plan_for_task(project_root, task_id)
        if len(candidate_plans) == 1:
            plan_name = candidate_plans[0]
        elif len(candidate_plans) > 1:
            msg = f"Task '{task_id}' found in multiple plans ({', '.join(candidate_plans)}). Please specify --plan."
            if json_out:
                return emit_error("task_complete", msg, 1)
            print(f"Error: {msg}", file=sys.stderr)
            return 1

    # Update plan status if associated with a plan
    if plan_name:
        try:
            from snodo.mcp.planner import PlannerMCP
            planner = PlannerMCP(project_root)
            planner.update_status(
                plan_name,
                task_id,
                "completed",
                completed_by=who,
                completed_at=recorded_at,
                judged=False,
            )
        except Exception as e:
            msg = f"Could not update plan status: {e}"
            if json_out:
                return emit_error("task_complete", msg, 1)
            print(f"Error: {msg}", file=sys.stderr)
            return 1

    # Append audit event
    from snodo.infrastructure.audit import get_audit_log
    audit_log_path = Path(project_root) / ".snodo" / "audit.log"
    audit_log = get_audit_log(str(audit_log_path) if audit_log_path.exists() else None)
    if audit_log is None:
        if json_out:
            return emit_error("task_complete", "Audit log unavailable.", 1)
        print("Error: Audit log unavailable.", file=sys.stderr)
        return 1

    event_data: dict[str, Any] = {
        "op": "task_completed_by_hand",
        "task_ref": task_id,
        "who": who,
        "recorded_at": recorded_at,
        "timestamp": recorded_at,
        "judged": False,
        "engine_judged": False,
        "outside_loop": True,
    }
    if plan_name:
        event_data["plan"] = plan_name
    if notes_arg:
        event_data["notes"] = notes_arg

    audit_log.append_event("task_completed_by_hand", event_data)

    # Clear failure context from active session
    try:
        from snodo.infrastructure.state import read_state
        from snodo.infrastructure.session import SessionManager
        state = read_state(project_root)
        mode = state.current_mode
        if mode:
            mgr = SessionManager()
            session = mgr.get_active_session(mode, project_root)
            if session:
                task_failures = session.checkpoint.decisions.get("task_failure", {})
                if isinstance(task_failures, dict) and task_id in task_failures:
                    del task_failures[task_id]
                    try:
                        mgr.update_decision(session.session_id, "task_failure", task_failures)
                    except Exception as e:
                        _logger.warning("Could not clear failure context for task %s: %s", task_id, e)
    except Exception as e:
        _logger.debug("Could not inspect session failure context: %s", e)

    # Update task state file if present
    task_state_file = Path(project_root) / ".snodo" / "tasks" / task_id / "state.json"
    if task_state_file.is_file():
        try:
            ts_data = json.loads(task_state_file.read_text())
            if isinstance(ts_data, dict):
                ts_data["status"] = "completed"
                ts_data["completed_by"] = who
                ts_data["completed_at"] = recorded_at
                ts_data["judged"] = False
                task_state_file.write_text(json.dumps(ts_data, indent=2))
        except Exception as e:
            _logger.debug("Could not update task state file for %s: %s", task_id, e)

    if json_out:
        return emit_json({
            "schema": schema_name("task_complete"),
            "ok": True,
            "task_id": task_id,
            "plan": plan_name,
            "who": who,
            "recorded_at": recorded_at,
            "judged": False,
            "notes": notes_arg,
        })

    print(f"✓ Recorded hand completion for task {task_id} (completed by {who}, unjudged by engine).")
    if plan_name:
        print(f"  Plan '{plan_name}' status updated to completed.")
    return 0
