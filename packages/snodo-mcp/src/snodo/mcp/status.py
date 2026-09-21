"""The closed task-status vocabulary and its CLI markers."""

TASK_STATUSES = {
    "pending",
    "in_progress",
    "completed",
    "blocked",
    "errored",
    "unmerged",
}

STATUS_MARKERS = {
    "completed": "+",
    "in_progress": "~",
    "blocked": "!",
    "errored": "?",
    "unmerged": "u",
    "pending": " ",
}


def status_marker(status: str) -> str:
    """Return the marker for an engine task status.

    Job snapshots call an in-progress task ``running``; it keeps the existing
    display while the engine vocabulary remains the six statuses above.
    Unknown values retain the existing question-mark marker.
    """
    return STATUS_MARKERS.get("in_progress" if status == "running" else status, "?")
