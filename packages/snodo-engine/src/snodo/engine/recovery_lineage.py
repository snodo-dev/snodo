"""Helpers for identifying task recovery lineage references."""

import re


def belongs_to_recovery_lineage(task_reference: object, root_task_id: str) -> bool:
    """Return whether a task reference is the root or a direct recovery sub-task."""
    if not isinstance(task_reference, str) or not isinstance(root_task_id, str):
        return False
    if task_reference == root_task_id:
        return True

    match = re.fullmatch(re.escape(root_task_id) + r"_fix_(\d+)", task_reference)
    return match is not None and int(match.group(1)) > 0
