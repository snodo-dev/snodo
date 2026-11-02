"""Task and recovery-lineage matching shared across package layers."""

import re


def belongs_to_recovery_lineage(task_reference: object, root_task_id: str) -> bool:
    """Return whether a reference is the root task or one of its recovery tasks."""
    if not isinstance(task_reference, str) or not isinstance(root_task_id, str):
        return False
    if task_reference == root_task_id:
        return True
    match = re.fullmatch(re.escape(root_task_id) + r"_fix_(\d+)", task_reference)
    return match is not None and int(match.group(1)) > 0


def same_task_lineage(left: object, right: object) -> bool:
    """Match identical task references or root/recovery references."""
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    if left == right:
        return True
    for candidate in (left, right):
        match = re.fullmatch(r"(.+)_fix_([1-9][0-9]*)", candidate)
        if match and belongs_to_recovery_lineage(left, match.group(1)) and belongs_to_recovery_lineage(right, match.group(1)):
            return True
    return False
