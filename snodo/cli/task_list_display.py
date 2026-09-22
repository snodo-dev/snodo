"""Rich rendering for the human-facing task list."""

import sys
from datetime import datetime, timezone
from typing import Any


def _task_time(timestamp: Any) -> tuple[str, str]:
    """Return relative age and UTC date for the same task timestamp."""
    if timestamp is None:
        return "?", "?"
    try:
        seconds = max(0, (datetime.now(timezone.utc) - timestamp).total_seconds())
        if seconds < 60:
            age = f"{int(seconds)}s ago"
        elif seconds < 3600:
            age = f"{int(seconds // 60)}m ago"
        elif seconds < 86400:
            age = f"{int(seconds // 3600)}h ago"
        else:
            age = f"{int(seconds // 86400)}d ago"
        date = timestamp.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        return age, date
    except (TypeError, AttributeError, ValueError):
        return "?", "?"


def render_task_table(entries: list[dict[str, Any]]) -> None:
    """Render task entries and page the table when stdout is interactive."""
    from rich.console import Console
    from rich.table import Table

    table = Table(title="Tasks")
    for column in ("TASK ID", "BRANCH", "ATTEMPT", "STATUS", "AGE", "DATE"):
        table.add_column(column)
    for info in entries:
        age, date = _task_time(info["timestamp"])
        table.add_row(
            info["task_id"], info["branch"], str(info["attempt"]),
            info["status"], age, date,
        )

    console = Console(file=sys.stdout, markup=False, highlight=False)
    if getattr(sys.stdout, "isatty", lambda: False)():
        with console.pager():
            console.print(table)
    else:
        console.print(table)
