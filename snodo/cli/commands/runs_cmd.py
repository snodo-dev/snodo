"""Emit accumulated completed task run records."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import typer

from snodo.infrastructure.paths import resolve_project_root


def register(app: typer.Typer) -> None:
    """Register the run-record report command."""

    @app.command("runs")
    def runs(
        json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
    ):
        """List completed task run records."""
        return runs_command(SimpleNamespace(json=json_output))


def _without_absent(value: Any) -> Any:
    """Keep measured zeroes while omitting measurements that were not made."""
    if isinstance(value, dict):
        return {
            key: _without_absent(item)
            for key, item in value.items()
            if item is not None
        }
    if isinstance(value, list):
        return [_without_absent(item) for item in value]
    return value


def _completed_runs(root: Path) -> list[dict]:
    tasks_dir = root / ".snodo" / "tasks"
    if not tasks_dir.is_dir():
        return []

    records = []
    for task_dir in sorted(tasks_dir.iterdir(), key=lambda path: path.name):
        if not task_dir.is_dir():
            continue
        try:
            state = json.loads((task_dir / "state.json").read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(state, dict) or state.get("completed_at") is None:
            continue

        record = {
            "task_id": state.get("task_id") or task_dir.name,
            "completed_at": state["completed_at"],
        }
        cost = state.get("cost")
        if isinstance(cost, dict):
            record["cost"] = _without_absent(cost)
        records.append(record)
    return records


def runs_command(args) -> int:
    """Emit the accumulated completed task run records."""
    root = resolve_project_root()
    if root is None:
        if getattr(args, "json", False):
            from snodo.cli.json_output import emit_error
            return emit_error("runs", "Not inside a snodo project.", 1)
        print("Not inside a snodo project.", file=sys.stderr)
        return 1

    records = _completed_runs(Path(root))
    if getattr(args, "json", False):
        from snodo.cli.json_output import emit_json, schema_name
        return emit_json({
            "schema": schema_name("runs"),
            "ok": True,
            "project_root": str(root),
            "runs": records,
        })

    if not records:
        print("No completed runs recorded.")
        return 0
    for record in records:
        print(record["task_id"])
    return 0
