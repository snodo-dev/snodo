"""Read-only delivery status for plan integration branches."""

import json
from pathlib import Path
from typing import Dict

from snodo.tools.git import open_repo


IN_MAIN = "in_main"
NOT_IN_MAIN = "not_in_main"
NO_BRANCH = "no_branch"


def _plan_delivery(repo, plans_path: Path, plan_name: str) -> str:
    """Determine whether a plan's integration tip is contained in HEAD."""
    try:
        status_path = plans_path / plan_name / "status.json"
        with status_path.open(encoding="utf-8") as status_file:
            status = json.load(status_file)
        branch = status.get("integration", {}).get("branch")
        if not isinstance(branch, str) or not branch:
            return NO_BRANCH

        local_ref = f"refs/heads/{branch}"
        remote_ref = f"refs/remotes/origin/{branch}"
        refs = repo.git.show_ref(with_exceptions=False)
        ref_lines = set(refs.splitlines())
        if any(line.endswith(f" {local_ref}") for line in ref_lines):
            branch_ref = local_ref
        elif any(line.endswith(f" {remote_ref}") for line in ref_lines):
            branch_ref = remote_ref
        else:
            return NO_BRANCH

        exit_code, _stdout, _stderr = repo.git.execute(
            ["git", "merge-base", "--is-ancestor", branch_ref, "HEAD"],
            with_extended_output=True,
            with_exceptions=False,
        )
        return IN_MAIN if exit_code == 0 else NOT_IN_MAIN
    except Exception:
        return NO_BRANCH


def plan_delivery(project_path, plan_name: str) -> str:
    """Return the current Git delivery status for one plan."""
    try:
        with open_repo(str(project_path)) as repo:
            return _plan_delivery(repo, Path(project_path) / ".snodo" / "plans", plan_name)
    except Exception:
        return NO_BRANCH


def plans_delivery(project_path) -> Dict[str, str]:
    """Return delivery status for each plan folder, opening the repository once."""
    plans_path = Path(project_path) / ".snodo" / "plans"
    try:
        plan_names = sorted(path.name for path in plans_path.iterdir() if path.is_dir())
    except OSError:
        return {}
    try:
        with open_repo(str(project_path)) as repo:
            return {
                name: _plan_delivery(repo, plans_path, name)
                for name in plan_names
            }
    except Exception:
        return {name: NO_BRANCH for name in plan_names}
