"""Branch identity helpers shared by task cleanup commands."""


def branch_task_owner(branch: str, task_id: str) -> str | None:
    """Return the exact task owner key encoded by a task branch name."""
    parts = branch.split("/")
    if len(parts) < 2 or parts[0] != "task":
        return None
    if parts[1] == task_id:
        return task_id
    if len(parts) >= 4 and parts[2] == task_id:
        return f"{parts[1]}/{task_id}"
    return None


def task_branches_for_id(branches: list[str], task_id: str) -> tuple[list[str], list[str]]:
    """Return matching branches and their distinct owner keys."""
    owned = [branch for branch in branches if branch_task_owner(branch, task_id)]
    owners = sorted({branch_task_owner(branch, task_id) for branch in owned})
    return owned, owners


def inspect_task_branches(project_root: str, task_id: str):
    """Open the project repository and resolve branches matching a task id."""
    from snodo.tools.git import GitMCP

    git = GitMCP(project_root)
    owned, owners = task_branches_for_id(
        [head.name for head in git.repo.heads], task_id
    )
    return git, owned, owners
