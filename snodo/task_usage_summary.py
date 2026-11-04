"""Print usage recorded for one task run."""
from pathlib import Path
import json

from snodo.usage_summary import render_usage_summary, summarize_usage


def print_task_usage_summary(project_root, task_id: str, elapsed_seconds: float) -> None:
    """Print the task's own usage records, if any were recorded."""
    path = Path(project_root) / ".snodo" / "tasks" / task_id / "state.json"
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError):
        return
    records = state.get("usage") if isinstance(state, dict) else None
    if not isinstance(records, list) or not records:
        return
    summary = summarize_usage(records, elapsed_seconds)
    for line in render_usage_summary(summary, elapsed_seconds):
        print(line)
