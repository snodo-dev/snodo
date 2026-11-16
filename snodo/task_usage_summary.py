"""Print usage recorded for one task run."""
from pathlib import Path
import json
import re

from snodo.usage_summary import render_usage_summary, summarize_usage


_FIX_TASK_ID = re.compile(r"_fix_\d+$")


def _job_usage_records(project_root: Path, task_id: str) -> list[dict]:
    """Load job usage for this task and any recovery-round task IDs."""
    jobs_dir = project_root / ".snodo" / "jobs"
    records = []
    try:
        job_dirs = list(jobs_dir.iterdir())
    except OSError:
        return records
    for job_dir in job_dirs:
        try:
            state = json.loads((job_dir / "state.json").read_text())
        except (OSError, ValueError):
            continue
        usage = state.get("usage") if isinstance(state, dict) else None
        if not isinstance(usage, list):
            continue
        for record in usage:
            if not isinstance(record, dict):
                continue
            record_task_id = record.get("task_id")
            if (
                isinstance(record_task_id, str)
                and (_FIX_TASK_ID.sub("", record_task_id) == task_id)
            ):
                records.append(record)
    return records


def print_task_usage_summary(project_root, task_id: str, elapsed_seconds: float) -> None:
    """Print task-state usage, falling back to matching job records."""
    path = Path(project_root) / ".snodo" / "tasks" / task_id / "state.json"
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError):
        state = None
    records = state.get("usage") if isinstance(state, dict) else None
    valid_records = [record for record in records if isinstance(record, dict)] if isinstance(records, list) else []
    if not valid_records:
        valid_records = _job_usage_records(Path(project_root), task_id)
    if not valid_records:
        return
    summary = summarize_usage(valid_records, elapsed_seconds)
    for line in render_usage_summary(summary, elapsed_seconds):
        print(line)
