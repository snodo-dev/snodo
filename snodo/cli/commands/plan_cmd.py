"""Plan command - Manage plans.

FILE: snodo/cli/commands/plan_cmd.py
"""

import sys
from pathlib import Path


def plan_command(args) -> int:
    """Manage plans."""
    from snodo.mcp.planner import PlannerMCP

    project_root = str(Path.cwd())

    try:
        planner = PlannerMCP(project_root)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if args.plan_action == "list":
        return _plan_list(planner)
    elif args.plan_action == "status":
        return _plan_status(planner, args.name)
    elif args.plan_action == "create":
        return _plan_create(planner, args)
    else:
        print("Unknown plan action. Use: list, status, create", file=sys.stderr)
        return 1


def _plan_list(planner) -> int:
    """List all plans."""
    plans = planner.list_plans()
    if not plans:
        print("No plans found.")
        return 0

    print("Plans:")
    for p in plans:
        counts = p.get("status_counts", {})
        done = counts.get("completed", 0)
        total = p["task_count"]
        progress = f"{done}/{total}" if total else "0/0"
        print(f"  {p['name']}: {p['intent']}")
        print(f"    Waves: {p['wave_count']}  Tasks: {progress}")
    return 0


def _plan_status(planner, name: str) -> int:
    """Show plan progress."""
    from snodo.mcp.planner import PlannerError

    try:
        plan_data = planner.get_plan(name)
        status_data = planner.get_status(name)
    except PlannerError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    tasks = status_data.get("tasks", {})
    print(f"Plan: {plan_data.get('name', name)}")
    print(f"Intent: {plan_data.get('intent', 'N/A')}")
    print()

    _print_plan_waves(plan_data.get("waves", []), tasks)
    _print_plan_summary(tasks)
    return 0


def _plan_create(planner, args) -> int:
    """Create a new plan from an intent description."""
    description = getattr(args, "description", "")
    plan_name = getattr(args, "plan_name", None)

    if not description:
        print("Error: plan description is required", file=sys.stderr)
        return 1

    from snodo.mcp.planner import PlannerError

    # Auto-generate name from description if not provided
    if not plan_name:
        plan_name = description.lower().replace(" ", "_")[:40].rstrip("_")

    try:
        plan_data = planner.decompose(description, plan_name)
    except PlannerError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(f"Plan created: {plan_name}")
    print(f"  Intent: {description}")
    waves = plan_data.get("waves", [])
    print(f"  Waves: {len(waves)}")
    total_tasks = sum(len(w.get("tasks", [])) for w in waves)
    print(f"  Tasks: {total_tasks}")
    return 0


def _print_plan_waves(waves: list, tasks: dict) -> None:
    """Print wave and task details."""
    _STATUS_MARKERS = {"completed": "+", "in_progress": "~",
                       "blocked": "!", "pending": " "}
    for wave in waves:
        wave_id = wave.get("id")
        deps = wave.get("depends_on", [])
        dep_str = f" (depends on: {', '.join(str(d) for d in deps)})" if deps else ""
        print(f"  Wave {wave_id}{dep_str}:")
        for task_id in wave.get("tasks", []):
            raw = tasks.get(task_id, "pending")
            state = raw["status"] if isinstance(raw, dict) else raw
            marker = _STATUS_MARKERS.get(state, "?")
            print(f"    [{marker}] {task_id}: {state}")
    print()


def _print_plan_summary(tasks: dict) -> None:
    """Print plan progress summary."""
    total = len(tasks)
    done = sum(1 for s in tasks.values() if (s["status"] if isinstance(s, dict) else s) == "completed")
    blocked = sum(1 for s in tasks.values() if s == "blocked")
    print(f"Progress: {done}/{total} completed", end="")
    if blocked:
        print(f", {blocked} blocked", end="")
    print()
